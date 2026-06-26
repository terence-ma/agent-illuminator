"""
agentilluminator.adapters.bridge
BridgeAdapter: lightweight HTTP receiver that captures tool invocations,
agent wake/close events from any HTTP-based agent bridge.

Agents or bridge middleware POST structured JSON events to the illuminator
receiver endpoint. This is the only adapter that requires minimal
infrastructure change — route agent traffic through a bridge that emits
events, or have the bridge itself POST to illuminator.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from agentilluminator.adapters.base import TraceAdapter
from agentilluminator.core.events import EventStatus, EventType, TraceEvent


class BridgeAdapter(TraceAdapter):
    """
    Opens a local HTTP server that accepts POSTed trace events.

    Bridge middleware or agents POST JSON to http://localhost:{port}/event

    Expected payload:
    {
      "event_type": "tool_invocation",
      "summary": "bash: run_script.py",
      "agent_id": "qin",
      "status": "pass",
      "detail": {"tool": "bash", "args": ["run_script.py"], "exit_code": 0}
    }
    """

    def __init__(self, listen_port: int = 19100, agent_id: str = "") -> None:
        self._port = listen_port
        self._agent_id = agent_id
        self._queue: deque[TraceEvent] = deque()
        self._server: HTTPServer | None = None
        self._server_thread: threading.Thread | None = None

    def setup(self) -> None:
        adapter = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/event":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    data = json.loads(body)
                    event = adapter._parse_payload(data)
                    adapter._queue.append(event)
                    self.send_response(200)
                except Exception:
                    self.send_response(400)
                self.end_headers()

            def log_message(self, *args):
                pass  # suppress default HTTP logging

        self._server = HTTPServer(("127.0.0.1", self._port), Handler)
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="illuminator-bridge"
        )
        self._server_thread.start()

    def teardown(self) -> None:
        if self._server:
            self._server.shutdown()

    def poll(self) -> list[TraceEvent]:
        events = []
        while self._queue:
            events.append(self._queue.popleft())
        return events

    def _parse_payload(self, data: dict) -> TraceEvent:
        try:
            event_type = EventType(data.get("event_type", "annotation"))
        except ValueError:
            event_type = EventType.ANNOTATION

        try:
            status = EventStatus(data.get("status", "ok"))
        except ValueError:
            status = EventStatus.OK

        ts_str = data.get("timestamp")
        ts = datetime.fromisoformat(ts_str) if ts_str else datetime.utcnow()

        return TraceEvent(
            event_type=event_type,
            summary=data.get("summary", ""),
            agent_id=data.get("agent_id", self._agent_id),
            status=status,
            timestamp=ts,
            detail=data.get("detail", {}),
            tags=data.get("tags", []),
        )


# ---------------------------------------------------------------------------


"""
agentilluminator.adapters.supervisor
SupervisorAdapter: reads structured output from independent supervisor
scripts and emits supervisor_verdict, assertion, and gap_detected events.

Supervisors write verdict files to a configured output directory.
The adapter polls that directory for new files.

Expected verdict file format (JSON):
{
  "verdict": "pass" | "fail",
  "pipeline_id": "morning-brief",
  "agent_id": "qin",
  "summary": "Pipeline complete — all steps verified",
  "assertions": [
    {"label": "delivery_receipt_present", "passed": true},
    {"label": "no_steps_skipped", "passed": true}
  ],
  "gaps": []
}
"""


import os
from pathlib import Path


class SupervisorAdapter(TraceAdapter):
    """
    Polls a directory for supervisor verdict files.

    Supervisors write a JSON verdict file after each check. This adapter
    reads new files, emits events, and moves processed files to an
    archive subdirectory to avoid reprocessing.

    Config:
        output_dir: directory where supervisor writes verdict files
        agent_id: default agent_id to tag events with
    """

    def __init__(self, output_dir: str, agent_id: str = "") -> None:
        self._output_dir = Path(output_dir)
        self._agent_id = agent_id
        self._archive_dir = self._output_dir / "processed"

    def setup(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._archive_dir.mkdir(parents=True, exist_ok=True)

    def poll(self) -> list[TraceEvent]:
        events = []
        for fpath in sorted(self._output_dir.glob("*.json")):
            try:
                with open(fpath) as f:
                    data = json.load(f)
                events.extend(self._parse_verdict(data))
                # Archive the processed file
                fpath.rename(self._archive_dir / fpath.name)
            except Exception:
                continue
        return events

    def _parse_verdict(self, data: dict) -> list[TraceEvent]:
        events = []
        verdict = data.get("verdict", "unknown")
        status = EventStatus.PASS if verdict == "pass" else EventStatus.FAIL
        agent_id = data.get("agent_id", self._agent_id)

        # Main supervisor verdict event
        events.append(TraceEvent(
            event_type=EventType.SUPERVISOR_VERDICT,
            summary=data.get("summary", f"Supervisor verdict: {verdict}"),
            agent_id=agent_id,
            status=status,
            detail={
                "pipeline_id": data.get("pipeline_id", ""),
                "verdict": verdict,
            },
        ))

        # Individual assertions
        for assertion in data.get("assertions", []):
            events.append(TraceEvent(
                event_type=EventType.ASSERTION,
                summary=assertion.get("label", ""),
                agent_id=agent_id,
                status=EventStatus.PASS if assertion.get("passed") else EventStatus.FAIL,
                detail=assertion,
            ))

        # Gaps
        for gap in data.get("gaps", []):
            events.append(TraceEvent(
                event_type=EventType.GAP_DETECTED,
                summary=gap.get("description", "Gap detected"),
                agent_id=agent_id,
                status=EventStatus.FAIL,
                detail=gap,
            ))

        return events
