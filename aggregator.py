"""
agentilluminator.core.aggregator
Reads trace files from a workspace and produces a Markdown reliability report.
No database required — works from flat Markdown files.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


class Aggregator:
    """
    Scans trace files in a workspace and produces a Markdown summary report.

    Metrics produced:
    - Total runs per agent, pass/fail breakdown
    - Gap frequency per agent
    - Delivery receipt rate per pipeline
    - Cron miss count
    - Mean run duration per agent
    - Most common failure event types
    """

    def __init__(self, trace_dir: str, report_dir: str) -> None:
        self._trace_dir = Path(trace_dir)
        self._report_dir = Path(report_dir)
        self._report_dir.mkdir(parents=True, exist_ok=True)

    def aggregate(self, since_days: int = 7, output_name: str | None = None) -> Path:
        since = datetime.utcnow() - timedelta(days=since_days)
        agent_files = list((self._trace_dir / "agent").glob("*.md"))
        pipeline_files = list((self._trace_dir / "pipeline").glob("*.md"))

        # Parse agent runs
        runs = [self._parse_agent_trace(f) for f in agent_files]
        runs = [r for r in runs if r and r.get("triggered_at") and
                datetime.fromisoformat(r["triggered_at"]) >= since]

        # Parse pipeline runs
        pipelines = [self._parse_pipeline_trace(f) for f in pipeline_files]
        pipelines = [p for p in pipelines if p and p.get("triggered_at") and
                     datetime.fromisoformat(p["triggered_at"]) >= since]

        report = self._render_report(runs, pipelines, since_days)

        ts = datetime.utcnow().strftime("%Y-%m-%d")
        fname = output_name or f"report-{ts}.md"
        fpath = self._report_dir / fname
        fpath.write_text(report, encoding="utf-8")
        return fpath

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_agent_trace(self, fpath: Path) -> dict | None:
        try:
            text = fpath.read_text(encoding="utf-8")
        except OSError:
            return None

        fm = self._extract_frontmatter(text)
        if not fm:
            return None

        # Count event types from timeline table
        event_counts: Counter = Counter()
        gap_count = 0
        has_receipt = False
        for line in text.splitlines():
            if "| " in line and " | " in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4:
                    event_type = parts[2]
                    if event_type and event_type not in ("Type", "---"):
                        event_counts[event_type] += 1
                        if event_type == "gap_detected":
                            gap_count += 1
                        if event_type == "delivery_receipt":
                            has_receipt = True

        fm["event_counts"] = dict(event_counts)
        fm["gap_count"] = gap_count
        fm["has_delivery_receipt"] = has_receipt
        return fm

    def _parse_pipeline_trace(self, fpath: Path) -> dict | None:
        try:
            text = fpath.read_text(encoding="utf-8")
        except OSError:
            return None
        fm = self._extract_frontmatter(text)
        return fm

    def _extract_frontmatter(self, text: str) -> dict | None:
        match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if not match:
            return None
        try:
            import yaml
            return yaml.safe_load(match.group(1)) or {}
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_report(self, runs: list[dict], pipelines: list[dict], since_days: int) -> str:
        lines = []
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        lines += [
            f"# Agent Illuminator — Reliability Report",
            f"*Generated {ts} | Last {since_days} days*",
            "",
        ]

        if not runs:
            lines.append("No trace data found for the selected period.")
            return "\n".join(lines)

        # Per-agent summary
        lines += ["## Agent Summary", ""]
        lines += ["| Agent | Runs | Pass | Fail | Gaps | Receipt Rate | Avg Duration |"]
        lines += ["|-------|------|------|------|------|--------------|-------------|"]

        by_agent: dict[str, list[dict]] = defaultdict(list)
        for r in runs:
            by_agent[r.get("agent_id", "unknown")].append(r)

        for agent_id, agent_runs in sorted(by_agent.items()):
            total = len(agent_runs)
            passed = sum(1 for r in agent_runs if r.get("status") == "complete")
            failed = total - passed
            gaps = sum(r.get("gap_count", 0) for r in agent_runs)
            receipts = sum(1 for r in agent_runs if r.get("has_delivery_receipt"))
            receipt_rate = f"{receipts}/{total}" if total else "—"
            durations = [r["duration_sec"] for r in agent_runs if r.get("duration_sec")]
            avg_dur = f"{int(sum(durations)/len(durations))}s" if durations else "—"
            lines.append(f"| {agent_id} | {total} | {passed} | {failed} | {gaps} | {receipt_rate} | {avg_dur} |")

        lines.append("")

        # Pipeline summary
        if pipelines:
            lines += ["## Pipeline Summary", ""]
            lines += ["| Pipeline | Runs | Pass | Fail |"]
            lines += ["|----------|------|------|------|"]
            by_pipeline: dict[str, list[dict]] = defaultdict(list)
            for p in pipelines:
                by_pipeline[p.get("pipeline_id", "unknown")].append(p)
            for pid, pruns in sorted(by_pipeline.items()):
                total = len(pruns)
                passed = sum(1 for p in pruns if p.get("verdict") == "pass")
                failed = total - passed
                lines.append(f"| {pid} | {total} | {passed} | {failed} |")
            lines.append("")

        # Common failure event types
        all_events: Counter = Counter()
        for r in runs:
            for etype, count in r.get("event_counts", {}).items():
                if etype not in ("ok", "agent_wake", "agent_close"):
                    all_events[etype] += count

        fail_events = {
            k: v for k, v in all_events.items()
            if k in ("gap_detected", "cron_miss", "supervisor_verdict")
        }
        if fail_events:
            lines += ["## Notable Event Counts", ""]
            for etype, count in sorted(fail_events.items(), key=lambda x: -x[1]):
                lines.append(f"- `{etype}`: {count}")
            lines.append("")

        lines += [
            "---",
            f"*Agent Illuminator — https://github.com/terence-ma/agent-illuminator*",
        ]

        return "\n".join(lines)
