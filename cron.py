"""
agentilluminator.adapters.cron
CronAdapter: captures cron_fire and cron_miss events from systemd journal
or /var/log/syslog. Optionally checks declared crons against a schedule
to detect missed fires.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections import deque
from datetime import datetime, timedelta
from typing import NamedTuple

from agentilluminator.adapters.base import TraceAdapter
from agentilluminator.core.events import EventStatus, EventType, TraceEvent


class ExpectedCron(NamedTuple):
    cron_id: str
    schedule: str          # cron expression e.g. "0 7 * * *"
    tolerance_sec: int = 300   # how late before flagged as miss


class CronAdapter(TraceAdapter):
    """
    Monitor systemd journal (or syslog fallback) for cron fires.
    Optionally declare expected crons to detect misses.

    Config:
        journal_unit: systemd unit to tail (default: "cron")
        expected_crons: list of ExpectedCron for miss detection
        agent_id: default agent_id to tag events with
        lookback_sec: how far back to read on startup (default: 300)
    """

    SYSTEMD_AVAILABLE = None  # cached after first check

    def __init__(
        self,
        journal_unit: str = "cron",
        expected_crons: list[ExpectedCron] | None = None,
        agent_id: str = "",
        lookback_sec: int = 300,
    ) -> None:
        self._unit = journal_unit
        self._expected_crons = expected_crons or []
        self._agent_id = agent_id
        self._lookback_sec = lookback_sec
        self._queue: deque[TraceEvent] = deque()
        self._last_seen: dict[str, datetime] = {}
        self._cursor: str | None = None

    def setup(self) -> None:
        # Prime cursor so we only get new events on first poll
        if self._journalctl_available():
            result = subprocess.run(
                ["journalctl", "--show-cursor", "-n", "0"],
                capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                if line.startswith("-- cursor:"):
                    self._cursor = line.split(":", 1)[1].strip()

    def poll(self) -> list[TraceEvent]:
        self._poll_journal()
        self._check_misses()
        events = []
        while self._queue:
            events.append(self._queue.popleft())
        return events

    # ------------------------------------------------------------------
    # Journal polling
    # ------------------------------------------------------------------

    def _poll_journal(self) -> None:
        if not self._journalctl_available():
            self._poll_syslog()
            return

        args = ["journalctl", "-u", self._unit, "-o", "short-iso", "--no-pager"]
        if self._cursor:
            args += ["--after-cursor", self._cursor]
        else:
            args += [f"--since=-{self._lookback_sec}s"]

        result = subprocess.run(args, capture_output=True, text=True)
        lines = result.stdout.splitlines()

        # Extract new cursor
        for line in lines:
            if line.startswith("-- cursor:"):
                self._cursor = line.split(":", 1)[1].strip()

        for line in lines:
            event = self._parse_journal_line(line)
            if event:
                self._queue.append(event)

    CRON_PATTERN = re.compile(
        r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
        r".*?CRON.*?CMD\s+\((?P<cmd>.+?)\)"
    )

    def _parse_journal_line(self, line: str) -> TraceEvent | None:
        m = self.CRON_PATTERN.search(line)
        if not m:
            return None
        cmd = m.group("cmd")
        ts_str = m.group("ts")
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            ts = datetime.utcnow()

        cron_id = self._identify_cron(cmd)
        if cron_id:
            self._last_seen[cron_id] = ts

        return TraceEvent(
            event_type=EventType.CRON_FIRE,
            summary=f"Cron fired: {cron_id or cmd[:60]}",
            agent_id=self._agent_id,
            status=EventStatus.OK,
            timestamp=ts,
            detail={"cmd": cmd, "cron_id": cron_id or ""},
        )

    def _poll_syslog(self) -> None:
        """Fallback: tail /var/log/syslog for CRON entries."""
        try:
            with open("/var/log/syslog", "r") as f:
                for line in f:
                    if "CRON" in line and "CMD" in line:
                        event = self._parse_syslog_line(line)
                        if event:
                            self._queue.append(event)
        except OSError:
            pass

    def _parse_syslog_line(self, line: str) -> TraceEvent | None:
        if "CMD" not in line:
            return None
        cmd_match = re.search(r"CMD\s+\((.+?)\)", line)
        if not cmd_match:
            return None
        cmd = cmd_match.group(1)
        return TraceEvent(
            event_type=EventType.CRON_FIRE,
            summary=f"Cron fired: {cmd[:60]}",
            agent_id=self._agent_id,
            status=EventStatus.OK,
            detail={"cmd": cmd, "cron_id": self._identify_cron(cmd)},
        )

    # ------------------------------------------------------------------
    # Miss detection
    # ------------------------------------------------------------------

    def _check_misses(self) -> None:
        now = datetime.utcnow()
        for expected in self._expected_crons:
            last = self._last_seen.get(expected.cron_id)
            if last is None:
                continue
            next_expected = self._next_fire(expected.schedule, last)
            if next_expected and now > next_expected + timedelta(seconds=expected.tolerance_sec):
                self._queue.append(TraceEvent(
                    event_type=EventType.CRON_MISS,
                    summary=f"Cron missed: {expected.cron_id}",
                    agent_id=self._agent_id,
                    status=EventStatus.FAIL,
                    detail={
                        "cron_id": expected.cron_id,
                        "last_seen": last.isoformat(),
                        "expected_by": (next_expected + timedelta(seconds=expected.tolerance_sec)).isoformat(),
                    },
                ))
                # Reset so we don't repeatedly emit the same miss
                self._last_seen[expected.cron_id] = now

    def _identify_cron(self, cmd: str) -> str:
        """Try to match a command string to a declared expected cron id."""
        for expected in self._expected_crons:
            if expected.cron_id.lower() in cmd.lower():
                return expected.cron_id
        return ""

    @staticmethod
    def _next_fire(schedule: str, after: datetime) -> datetime | None:
        """
        Minimal cron schedule parser for miss detection.
        Supports only simple cases (no ranges, no step values).
        For production use, install `croniter` and replace this.
        """
        try:
            from croniter import croniter  # type: ignore
            return croniter(schedule, after).get_next(datetime)
        except ImportError:
            pass
        # Fallback: assume hourly if we can't parse
        return after + timedelta(hours=1)

    @classmethod
    def _journalctl_available(cls) -> bool:
        if cls.SYSTEMD_AVAILABLE is None:
            result = subprocess.run(["which", "journalctl"], capture_output=True)
            cls.SYSTEMD_AVAILABLE = result.returncode == 0
        return cls.SYSTEMD_AVAILABLE
