"""
agentilluminator.adapters.workspace
WorkspaceAdapter: captures file_write and file_read events by watching
agent workspace directories via inotify (Linux) or fswatch (macOS).

Requires no agent changes. Captures ground-truth file activity regardless
of what the agent logs or reports.
"""

from __future__ import annotations

import fnmatch
import os
import platform
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from agentilluminator.adapters.base import TraceAdapter
from agentilluminator.core.events import EventStatus, EventType, TraceEvent


class WorkspaceAdapter(TraceAdapter):
    """
    Watch one or more workspace directories for file activity.

    Uses inotify-tools on Linux (`inotifywait`) and fswatch on macOS.
    Falls back to polling if neither is available — less precise but functional.

    Config:
        watch_paths: list of directories to watch
        ignore_patterns: glob patterns to exclude (e.g. ["*.tmp", "traces/*"])
        agent_id: default agent_id to tag events with (can be overridden per-path)
    """

    def __init__(
        self,
        watch_paths: list[str],
        ignore_patterns: list[str] | None = None,
        agent_id: str = "",
        recursive: bool = True,
    ) -> None:
        self._watch_paths = [Path(p) for p in watch_paths]
        self._ignore_patterns = ignore_patterns or []
        self._agent_id = agent_id
        self._recursive = recursive
        self._queue: deque[TraceEvent] = deque()
        self._watcher_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._system = platform.system()

    # ------------------------------------------------------------------
    # TraceAdapter interface
    # ------------------------------------------------------------------

    def setup(self) -> None:
        self._stop_event.clear()
        self._watcher_thread = threading.Thread(
            target=self._watch_loop, daemon=True, name="illuminator-workspace-watcher"
        )
        self._watcher_thread.start()

    def teardown(self) -> None:
        self._stop_event.set()
        if self._watcher_thread:
            self._watcher_thread.join(timeout=5)

    def poll(self) -> list[TraceEvent]:
        events = []
        while self._queue:
            events.append(self._queue.popleft())
        return events

    # ------------------------------------------------------------------
    # Internal watcher
    # ------------------------------------------------------------------

    def _watch_loop(self) -> None:
        if self._system == "Linux" and self._inotifywait_available():
            self._inotify_watch()
        elif self._system == "Darwin" and self._fswatch_available():
            self._fswatch_watch()
        else:
            self._polling_watch()

    def _inotify_watch(self) -> None:
        args = ["inotifywait", "--monitor", "--format", "%e %w%f", "--quiet"]
        if self._recursive:
            args.append("--recursive")
        args += ["--event", "close_write", "--event", "open"]
        args += [str(p) for p in self._watch_paths]

        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            for line in proc.stdout:
                if self._stop_event.is_set():
                    break
                self._parse_inotify_line(line.strip())
        finally:
            proc.terminate()

    def _parse_inotify_line(self, line: str) -> None:
        if not line:
            return
        parts = line.split(" ", 1)
        if len(parts) < 2:
            return
        events_str, path = parts
        if self._should_ignore(path):
            return
        if "CLOSE_WRITE" in events_str:
            self._enqueue(EventType.FILE_WRITE, path)
        elif "OPEN" in events_str:
            self._enqueue(EventType.FILE_READ, path)

    def _fswatch_watch(self) -> None:
        args = ["fswatch", "--one-per-batch", "--event=Updated", "--event=Created"]
        if self._recursive:
            args.append("--recursive")
        args += [str(p) for p in self._watch_paths]

        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            for line in proc.stdout:
                if self._stop_event.is_set():
                    break
                path = line.strip()
                if path and not self._should_ignore(path):
                    self._enqueue(EventType.FILE_WRITE, path)
        finally:
            proc.terminate()

    def _polling_watch(self, interval: float = 2.0) -> None:
        """Fallback: poll mtime on watched directories."""
        seen: dict[str, float] = {}
        while not self._stop_event.is_set():
            for base in self._watch_paths:
                for root, _, files in os.walk(base):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        if self._should_ignore(fpath):
                            continue
                        try:
                            mtime = os.path.getmtime(fpath)
                        except OSError:
                            continue
                        prev = seen.get(fpath)
                        if prev is None:
                            seen[fpath] = mtime
                        elif mtime > prev:
                            seen[fpath] = mtime
                            self._enqueue(EventType.FILE_WRITE, fpath)
            time.sleep(interval)

    def _enqueue(self, event_type: EventType, path: str) -> None:
        summary = f"{'Wrote' if event_type == EventType.FILE_WRITE else 'Read'} {Path(path).name}"
        event = TraceEvent(
            event_type=event_type,
            summary=summary,
            agent_id=self._agent_id,
            status=EventStatus.OK,
            detail={"path": path},
        )
        self._queue.append(event)

    def _should_ignore(self, path: str) -> bool:
        name = os.path.basename(path)
        for pattern in self._ignore_patterns:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern):
                return True
        return False

    @staticmethod
    def _inotifywait_available() -> bool:
        return subprocess.run(["which", "inotifywait"], capture_output=True).returncode == 0

    @staticmethod
    def _fswatch_available() -> bool:
        return subprocess.run(["which", "fswatch"], capture_output=True).returncode == 0
