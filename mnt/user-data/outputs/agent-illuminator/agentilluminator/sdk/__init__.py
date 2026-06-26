"""
agentilluminator.sdk
Lightweight agent-side SDK for emitting trace events from within an agent.

Zero external dependencies. Fails silently if no daemon is running.
Agents import this and call illuminate.event() to enrich their traces
with semantic annotations, assertions, and custom events.

Usage:
    from agentilluminator.sdk import illuminate

    illuminate.event("file_write", summary="Wrote daily brief", detail={"path": "..."})
    illuminate.assertion("delivery_receipt_present", passed=True)
    illuminate.annotation("Rectification loop triggered — QC failed on first pass")
    illuminate.gap("step_skipped", description="QC step was not executed")
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any


class _Illuminate:
    """
    Thread-safe, fail-silent event emitter for use inside agents.

    Sends structured JSON events to the illuminator bridge receiver
    (default: http://127.0.0.1:19100/event). If the receiver is not
    available, events are silently dropped — the agent never crashes
    due to missing observability infrastructure.

    Configuration via environment variables:
        ILLUMINATOR_URL      Base URL of the bridge receiver
                             (default: http://127.0.0.1:19100)
        ILLUMINATOR_AGENT_ID Agent ID to tag events with
        ILLUMINATOR_RUN_ID   Run ID to associate events with
        ILLUMINATOR_DISABLED Set to "1" to disable all emission
    """

    def __init__(self) -> None:
        self._base_url = os.environ.get("ILLUMINATOR_URL", "http://127.0.0.1:19100")
        self._agent_id = os.environ.get("ILLUMINATOR_AGENT_ID", "")
        self._run_id = os.environ.get("ILLUMINATOR_RUN_ID", "")
        self._disabled = os.environ.get("ILLUMINATOR_DISABLED", "0") == "1"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def event(
        self,
        event_type: str,
        summary: str,
        status: str = "ok",
        detail: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Emit a trace event of any type."""
        self._send({
            "event_type": event_type,
            "summary": summary,
            "status": status,
            "detail": detail or {},
            "tags": tags or [],
            "agent_id": agent_id or self._agent_id,
            "run_id": run_id or self._run_id,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def assertion(
        self,
        label: str,
        passed: bool,
        detail: dict[str, Any] | None = None,
        agent_id: str | None = None,
    ) -> None:
        """Emit an assertion event — a named pass/fail check."""
        self._send({
            "event_type": "assertion",
            "summary": label,
            "status": "pass" if passed else "fail",
            "detail": {**(detail or {}), "label": label, "passed": passed},
            "agent_id": agent_id or self._agent_id,
            "run_id": self._run_id,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def annotation(
        self,
        note: str,
        tags: list[str] | None = None,
        agent_id: str | None = None,
    ) -> None:
        """Emit a free-text annotation — semantic context for a run."""
        self._send({
            "event_type": "annotation",
            "summary": note,
            "status": "ok",
            "detail": {"note": note},
            "tags": tags or [],
            "agent_id": agent_id or self._agent_id,
            "run_id": self._run_id,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def gap(
        self,
        gap_id: str,
        description: str,
        detail: dict[str, Any] | None = None,
        agent_id: str | None = None,
    ) -> None:
        """Emit a gap_detected event — a named failure or missing step."""
        self._send({
            "event_type": "gap_detected",
            "summary": f"Gap: {gap_id} — {description}",
            "status": "fail",
            "detail": {**(detail or {}), "gap_id": gap_id, "description": description},
            "agent_id": agent_id or self._agent_id,
            "run_id": self._run_id,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def delivery_receipt(
        self,
        destination: str,
        artifact: str,
        detail: dict[str, Any] | None = None,
        agent_id: str | None = None,
    ) -> None:
        """Emit a delivery_receipt — confirms a terminal artifact was delivered."""
        self._send({
            "event_type": "delivery_receipt",
            "summary": f"Delivered {artifact} → {destination}",
            "status": "ok",
            "detail": {
                **(detail or {}),
                "destination": destination,
                "artifact": artifact,
            },
            "agent_id": agent_id or self._agent_id,
            "run_id": self._run_id,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def configure(
        self,
        base_url: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        disabled: bool | None = None,
    ) -> None:
        """
        Override configuration at runtime.
        Useful when environment variables are not available.
        """
        if base_url is not None:
            self._base_url = base_url
        if agent_id is not None:
            self._agent_id = agent_id
        if run_id is not None:
            self._run_id = run_id
        if disabled is not None:
            self._disabled = disabled

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send(self, payload: dict) -> None:
        if self._disabled:
            return
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{self._base_url}/event",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=1)
        except (urllib.error.URLError, socket.timeout, OSError):
            # Fail silently — never crash the agent
            pass


# Module-level singleton — import and use directly
illuminate = _Illuminate()
