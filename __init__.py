"""
agentilluminator.storage
Storage backends for trace files.

Two backends:
  MarkdownBackend (default) — writes Markdown files with YAML frontmatter.
    Human-readable, zero dependencies, fully portable.
  SQLiteBackend — additionally indexes runs and events in SQLite for
    queryable history. Traces are still written as Markdown.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path

import yaml  # PyYAML — only external dependency

from agentilluminator.core.events import AgentRun, EventStatus, PipelineRun, RunStatus


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class StorageBackend(ABC):

    @abstractmethod
    def write_agent_run(self, run: AgentRun) -> Path:
        """Persist a completed agent run. Returns path to the trace file."""
        raise NotImplementedError

    @abstractmethod
    def write_pipeline_run(self, run: PipelineRun, agent_runs: list[AgentRun]) -> Path:
        """Persist a completed pipeline run. Returns path to the trace file."""
        raise NotImplementedError

    @abstractmethod
    def prune(self, retention_days: int, on_expire: str = "archive") -> int:
        """Remove or archive traces older than retention_days. Returns count."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Markdown backend
# ---------------------------------------------------------------------------

class MarkdownBackend(StorageBackend):
    """
    Write trace files as Markdown with YAML frontmatter.

    Directory layout:
        {trace_dir}/agent/YYYY-MM-DD_HH-MM_{agent_id}_{run_id}.md
        {trace_dir}/pipeline/YYYY-MM-DD_HH-MM_{pipeline_id}_{run_id}.md
        {trace_dir}/archive/YYYY-MM/...  (on retention)
    """

    def __init__(self, trace_dir: str, report_dir: str) -> None:
        self.trace_dir = Path(trace_dir)
        self.report_dir = Path(report_dir)
        self._agent_dir = self.trace_dir / "agent"
        self._pipeline_dir = self.trace_dir / "pipeline"
        self._archive_dir = self.trace_dir / "archive"
        for d in (self._agent_dir, self._pipeline_dir, self._archive_dir, self.report_dir):
            d.mkdir(parents=True, exist_ok=True)

    def write_agent_run(self, run: AgentRun) -> Path:
        ts = run.triggered_at.strftime("%Y-%m-%d_%H-%M")
        fname = f"{ts}_{run.agent_id}_{run.run_id[-6:]}.md"
        fpath = self._agent_dir / fname
        fpath.write_text(self._render_agent_run(run), encoding="utf-8")
        return fpath

    def write_pipeline_run(self, run: PipelineRun, agent_runs: list[AgentRun]) -> Path:
        ts = run.triggered_at.strftime("%Y-%m-%d_%H-%M")
        fname = f"{ts}_{run.pipeline_id}_{run.run_id[-6:]}.md"
        fpath = self._pipeline_dir / fname
        fpath.write_text(self._render_pipeline_run(run, agent_runs), encoding="utf-8")
        return fpath

    def prune(self, retention_days: int, on_expire: str = "archive") -> int:
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        count = 0
        for fpath in list(self._agent_dir.glob("*.md")) + list(self._pipeline_dir.glob("*.md")):
            mtime = datetime.utcfromtimestamp(fpath.stat().st_mtime)
            if mtime < cutoff:
                if on_expire == "delete":
                    fpath.unlink()
                else:
                    month_dir = self._archive_dir / mtime.strftime("%Y-%m")
                    month_dir.mkdir(parents=True, exist_ok=True)
                    fpath.rename(month_dir / fpath.name)
                count += 1
        return count

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_agent_run(self, run: AgentRun) -> str:
        frontmatter = {
            "run_id": run.run_id,
            "agent_id": run.agent_id,
            "model": run.model,
            "triggered_by": run.triggered_by,
            "triggered_at": run.triggered_at.isoformat(),
            "closed_at": run.closed_at.isoformat() if run.closed_at else None,
            "duration_sec": run.duration_sec,
            "status": run.status.value,
            "pipeline_run_id": run.pipeline_run_id,
            "tags": run.tags,
        }
        lines = ["---", yaml.dump(frontmatter, default_flow_style=False).rstrip(), "---", ""]

        ts_label = run.triggered_at.strftime("%Y-%m-%d %H:%M")
        lines += [f"# Run: {run.agent_id} — {ts_label}", ""]

        # Timeline table
        lines += ["## Timeline", ""]
        lines += ["| Time | Type | Summary | Status |"]
        lines += ["|------|------|---------|--------|"]
        for e in run.events:
            t = e.timestamp.strftime("%H:%M:%S")
            lines.append(f"| {t} | {e.event_type.value} | {e.summary} | {e.status.value} |")
        lines.append("")

        # Assertions
        assertions = run.assertions()
        lines += ["## Assertions", ""]
        if assertions:
            for a in assertions:
                icon = "PASS" if a.status == EventStatus.PASS else "FAIL"
                lines.append(f"- [{icon}] {a.summary}")
        else:
            lines.append("None recorded.")
        lines.append("")

        # Gaps
        gaps = run.gaps()
        lines += ["## Gaps", ""]
        if gaps:
            for g in gaps:
                lines.append(f"- {g.summary}")
        else:
            lines.append("None detected this run.")
        lines.append("")

        return "\n".join(lines)

    def _render_pipeline_run(self, run: PipelineRun, agent_runs: list[AgentRun]) -> str:
        frontmatter = {
            "run_id": run.run_id,
            "pipeline_id": run.pipeline_id,
            "triggered_at": run.triggered_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "status": run.status.value,
            "verdict": run.verdict,
            "agent_run_ids": run.agent_run_ids,
            "tags": run.tags,
        }
        lines = ["---", yaml.dump(frontmatter, default_flow_style=False).rstrip(), "---", ""]

        ts_label = run.triggered_at.strftime("%Y-%m-%d %H:%M")
        lines += [f"# Pipeline Run: {run.pipeline_id} — {ts_label}", ""]

        # Agent summary table
        lines += ["## Agent Runs", ""]
        lines += ["| Agent | Status | Duration |"]
        lines += ["|-------|--------|----------|"]
        for ar in agent_runs:
            dur = f"{ar.duration_sec}s" if ar.duration_sec else "—"
            lines.append(f"| {ar.agent_id} | {ar.status.value} | {dur} |")
        lines.append("")

        # Verdict
        lines += ["## Verdict", ""]
        icon = "PASS" if run.verdict == "pass" else "FAIL" if run.verdict == "fail" else "—"
        lines.append(f"[{icon}] {run.verdict or 'No verdict recorded.'}")
        lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

class SQLiteBackend(StorageBackend):
    """
    SQLite-backed storage. Traces are still written as Markdown (via the
    embedded MarkdownBackend). SQLite provides an additional index for
    querying runs by agent, status, time range, and event type.

    Schema:
        runs(run_id, agent_id, pipeline_run_id, triggered_at, closed_at,
             status, model, triggered_by, trace_path)
        events(event_id, run_id, event_type, summary, status, timestamp,
               agent_id, detail_json)
        pipeline_runs(run_id, pipeline_id, triggered_at, completed_at,
                      status, verdict, trace_path)
    """

    def __init__(self, db_path: str, trace_dir: str, report_dir: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._md = MarkdownBackend(trace_dir, report_dir)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._init_schema()

    def write_agent_run(self, run: AgentRun) -> Path:
        trace_path = self._md.write_agent_run(run)
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO runs
                   (run_id, agent_id, pipeline_run_id, triggered_at, closed_at,
                    status, model, triggered_by, trace_path)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    run.run_id, run.agent_id, run.pipeline_run_id,
                    run.triggered_at.isoformat(),
                    run.closed_at.isoformat() if run.closed_at else None,
                    run.status.value, run.model, run.triggered_by,
                    str(trace_path),
                )
            )
            for e in run.events:
                self._conn.execute(
                    """INSERT OR IGNORE INTO events
                       (event_id, run_id, event_type, summary, status,
                        timestamp, agent_id, detail_json)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        e.event_id, run.run_id, e.event_type.value,
                        e.summary, e.status.value,
                        e.timestamp.isoformat(), e.agent_id,
                        json.dumps(e.detail),
                    )
                )
        return trace_path

    def write_pipeline_run(self, run: PipelineRun, agent_runs: list[AgentRun]) -> Path:
        trace_path = self._md.write_pipeline_run(run, agent_runs)
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO pipeline_runs
                   (run_id, pipeline_id, triggered_at, completed_at,
                    status, verdict, trace_path)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    run.run_id, run.pipeline_id,
                    run.triggered_at.isoformat(),
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.status.value, run.verdict, str(trace_path),
                )
            )
        return trace_path

    def prune(self, retention_days: int, on_expire: str = "archive") -> int:
        md_count = self._md.prune(retention_days, on_expire)
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).isoformat()
        with self._conn:
            cur = self._conn.execute(
                "SELECT run_id FROM runs WHERE triggered_at < ?", (cutoff,)
            )
            old_run_ids = [r[0] for r in cur.fetchall()]
            if old_run_ids:
                placeholders = ",".join("?" * len(old_run_ids))
                self._conn.execute(f"DELETE FROM events WHERE run_id IN ({placeholders})", old_run_ids)
                self._conn.execute(f"DELETE FROM runs WHERE run_id IN ({placeholders})", old_run_ids)
        return md_count

    def query_runs(
        self,
        agent_id: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses = []
        params = []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if since:
            clauses.append("triggered_at >= ?")
            params.append(since.isoformat())
        if until:
            clauses.append("triggered_at <= ?")
            params.append(until.isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        cur = self._conn.execute(
            f"SELECT * FROM runs {where} ORDER BY triggered_at DESC LIMIT ?", params
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_events(
        self,
        event_type: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        run_id: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        clauses = []
        params = []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        cur = self._conn.execute(
            f"SELECT * FROM events {where} ORDER BY timestamp DESC LIMIT ?", params
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    agent_id TEXT,
                    pipeline_run_id TEXT,
                    triggered_at TEXT,
                    closed_at TEXT,
                    status TEXT,
                    model TEXT,
                    triggered_by TEXT,
                    trace_path TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    event_type TEXT,
                    summary TEXT,
                    status TEXT,
                    timestamp TEXT,
                    agent_id TEXT,
                    detail_json TEXT
                );
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    pipeline_id TEXT,
                    triggered_at TEXT,
                    completed_at TEXT,
                    status TEXT,
                    verdict TEXT,
                    trace_path TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_runs_agent ON runs(agent_id);
                CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
                CREATE INDEX IF NOT EXISTS idx_runs_triggered ON runs(triggered_at);
                CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
                CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent_id);
            """)
