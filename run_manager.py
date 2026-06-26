"""
agentilluminator.core.run_manager
Manages run lifecycle: open, append, close, orphan detection.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Callable

from .events import AgentRun, EventType, PipelineRun, RunStatus, TraceEvent


class RunManager:
    def __init__(self, orphan_timeout_sec: int = 7200) -> None:
        self._agent_runs: dict[str, AgentRun] = {}
        self._pipeline_runs: dict[str, PipelineRun] = {}
        self._orphan_timeout_sec = orphan_timeout_sec
        self._on_run_closed: list[Callable[[AgentRun], None]] = []

    # ------------------------------------------------------------------
    # Agent runs
    # ------------------------------------------------------------------

    def open_agent_run(
        self,
        agent_id: str,
        triggered_by: str = "",
        model: str = "",
        pipeline_run_id: str = "",
        tags: list[str] | None = None,
    ) -> AgentRun:
        run_id = self._make_run_id(agent_id)
        run = AgentRun(
            run_id=run_id,
            agent_id=agent_id,
            triggered_by=triggered_by,
            model=model,
            pipeline_run_id=pipeline_run_id,
            tags=tags or [],
        )
        self._agent_runs[run_id] = run
        return run

    def append_event(self, run_id: str, event: TraceEvent) -> None:
        run = self._agent_runs.get(run_id)
        if run is None:
            raise KeyError(f"No open run with id {run_id!r}")
        run.add_event(event)

        # Auto-close on agent_close event
        if event.event_type == EventType.AGENT_CLOSE:
            status = RunStatus.COMPLETE if event.status.value in ("ok", "pass") else RunStatus.FAILED
            self._close_agent_run(run_id, status)

    def close_agent_run(self, run_id: str, status: RunStatus = RunStatus.COMPLETE) -> AgentRun:
        return self._close_agent_run(run_id, status)

    def _close_agent_run(self, run_id: str, status: RunStatus) -> AgentRun:
        run = self._agent_runs.pop(run_id, None)
        if run is None:
            raise KeyError(f"No open run with id {run_id!r}")
        run.closed_at = datetime.utcnow()
        run.status = status
        for cb in self._on_run_closed:
            cb(run)
        return run

    def get_active_agent_runs(self) -> list[AgentRun]:
        return list(self._agent_runs.values())

    def detect_orphans(self) -> list[AgentRun]:
        cutoff = datetime.utcnow() - timedelta(seconds=self._orphan_timeout_sec)
        orphans = []
        for run_id, run in list(self._agent_runs.items()):
            if run.triggered_at < cutoff:
                run.status = RunStatus.ORPHANED
                run.closed_at = datetime.utcnow()
                orphans.append(self._agent_runs.pop(run_id))
                for cb in self._on_run_closed:
                    cb(run)
        return orphans

    def on_run_closed(self, callback: Callable[[AgentRun], None]) -> None:
        """Register a callback fired whenever a run closes (used by TraceWriter)."""
        self._on_run_closed.append(callback)

    # ------------------------------------------------------------------
    # Pipeline runs
    # ------------------------------------------------------------------

    def open_pipeline_run(
        self,
        pipeline_id: str,
        tags: list[str] | None = None,
    ) -> PipelineRun:
        run_id = self._make_run_id(pipeline_id)
        run = PipelineRun(run_id=run_id, pipeline_id=pipeline_id, tags=tags or [])
        self._pipeline_runs[run_id] = run
        return run

    def close_pipeline_run(
        self,
        run_id: str,
        status: RunStatus = RunStatus.COMPLETE,
        verdict: str = "",
    ) -> PipelineRun:
        run = self._pipeline_runs.pop(run_id, None)
        if run is None:
            raise KeyError(f"No open pipeline run with id {run_id!r}")
        run.completed_at = datetime.utcnow()
        run.status = status
        run.verdict = verdict
        return run

    def get_active_pipeline_runs(self) -> list[PipelineRun]:
        return list(self._pipeline_runs.values())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_run_id(agent_id: str) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        short = str(uuid.uuid4())[:6]
        safe_id = agent_id.replace(" ", "-").lower()
        return f"run_{ts}_{safe_id}_{short}"
