"""
agentilluminator.cli
illuminator — command-line interface for Agent Illuminator.

Commands:
  init      Initialise trace directory structure in a workspace
  daemon    Start the capture daemon
  status    Show active runs
  show      Show a trace file for a specific run
  report    Aggregate traces into a reliability report
  prune     Remove or archive old trace files
  query     Query runs or events (SQLite backend only)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_init(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace)
    for subdir in ["traces/agent", "traces/pipeline", "traces/archive", "reports"]:
        (workspace / subdir).mkdir(parents=True, exist_ok=True)

    config_path = workspace / "agentilluminator.yaml"
    if not config_path.exists():
        config_path.write_text(
            f"""\
workspace: {workspace}
agent_id: ""

storage:
  backend: markdown        # markdown | sqlite
  trace_dir: {workspace / 'traces'}
  report_dir: {workspace / 'reports'}
  retention_days: 90
  on_expire: archive       # archive | delete

adapters:
  workspace:
    enabled: true
    watch_paths:
      - {workspace}
    ignore_patterns:
      - "*.tmp"
      - "traces/*"
      - "reports/*"
  cron:
    enabled: false
    journal_unit: cron
  bridge:
    enabled: false
    listen_port: 19100
  supervisor:
    enabled: false
    output_dir: {workspace / 'supervisor-output'}

pipelines: {{}}
""",
            encoding="utf-8",
        )
        print(f"Wrote config: {config_path}")
    else:
        print(f"Config already exists: {config_path}")

    print(f"Initialised workspace: {workspace}")
    print("  traces/agent/      — per-agent run traces")
    print("  traces/pipeline/   — per-pipeline run traces")
    print("  traces/archive/    — retained traces on prune")
    print("  reports/           — aggregated reports")


def cmd_daemon(args: argparse.Namespace) -> None:
    import signal
    import time

    config = _load_config(args.config)
    adapters = _build_adapters(config)
    storage = _build_storage(config)

    from agentilluminator.core.run_manager import RunManager
    manager = RunManager()
    manager.on_run_closed(storage.write_agent_run)

    print(f"[illuminator] Starting daemon with {len(adapters)} adapter(s).")
    for adapter in adapters:
        adapter.setup()
        print(f"  + {adapter.name}")

    stop = False

    def handle_signal(sig, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    poll_interval = config.get("poll_interval_sec", 5)
    orphan_interval = 300
    last_orphan_check = time.time()

    while not stop:
        for adapter in adapters:
            try:
                for event in adapter.poll():
                    _route_event(event, manager)
            except Exception as exc:
                print(f"[illuminator] Adapter error ({adapter.name}): {exc}", file=sys.stderr)

        # Periodic orphan detection
        now = time.time()
        if now - last_orphan_check > orphan_interval:
            orphans = manager.detect_orphans()
            for run in orphans:
                storage.write_agent_run(run)
                print(f"[illuminator] Orphaned run closed: {run.run_id}")
            last_orphan_check = now

        time.sleep(poll_interval)

    print("[illuminator] Shutting down.")
    for adapter in adapters:
        adapter.teardown()


def cmd_status(args: argparse.Namespace) -> None:
    print("Active runs: (daemon must be running to reflect live state)")
    print("Use `illuminator daemon` to start the capture daemon.")


def cmd_show(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    trace_dir = Path(config.get("storage", {}).get("trace_dir", "traces"))
    run_id = args.run_id

    matches = list(trace_dir.rglob(f"*{run_id}*.md"))
    if not matches:
        print(f"No trace found for run_id: {run_id}")
        sys.exit(1)
    print(matches[0].read_text(encoding="utf-8"))


def cmd_report(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    storage_cfg = config.get("storage", {})
    trace_dir = storage_cfg.get("trace_dir", "traces")
    report_dir = storage_cfg.get("report_dir", "reports")

    from agentilluminator.core.aggregator import Aggregator
    agg = Aggregator(trace_dir, report_dir)
    since_days = args.since_days if hasattr(args, "since_days") else 7
    fpath = agg.aggregate(since_days=since_days)
    print(f"Report written: {fpath}")


def cmd_prune(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    storage_cfg = config.get("storage", {})
    retention = storage_cfg.get("retention_days", 90)
    on_expire = storage_cfg.get("on_expire", "archive")

    storage = _build_storage(config)
    if args.dry_run:
        print(f"[dry-run] Would prune traces older than {retention} days (action: {on_expire})")
        return
    count = storage.prune(retention, on_expire)
    print(f"Pruned {count} trace file(s) (action: {on_expire}).")


def cmd_query(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    storage = _build_storage(config)

    from agentilluminator.storage import SQLiteBackend
    if not isinstance(storage, SQLiteBackend):
        print("Query requires the SQLite backend. Set storage.backend: sqlite in your config.")
        sys.exit(1)

    from datetime import datetime, timedelta
    since = datetime.utcnow() - timedelta(days=args.since_days) if hasattr(args, "since_days") else None

    if args.events:
        results = storage.query_events(
            event_type=getattr(args, "event_type", None),
            agent_id=getattr(args, "agent", None),
            status=getattr(args, "status", None),
        )
    else:
        results = storage.query_runs(
            agent_id=getattr(args, "agent", None),
            status=getattr(args, "status", None),
            since=since,
        )

    if not results:
        print("No results.")
        return

    for row in results:
        print(row)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _build_storage(config: dict):
    storage_cfg = config.get("storage", {})
    backend = storage_cfg.get("backend", "markdown")
    trace_dir = storage_cfg.get("trace_dir", "traces")
    report_dir = storage_cfg.get("report_dir", "reports")

    from agentilluminator.storage import MarkdownBackend, SQLiteBackend
    if backend == "sqlite":
        db_path = storage_cfg.get("db_path", ".illuminator/illuminator.db")
        return SQLiteBackend(db_path, trace_dir, report_dir)
    return MarkdownBackend(trace_dir, report_dir)


def _build_adapters(config: dict) -> list:
    adapters = []
    adapter_cfg = config.get("adapters", {})
    agent_id = config.get("agent_id", "")

    ws_cfg = adapter_cfg.get("workspace", {})
    if ws_cfg.get("enabled", False):
        from agentilluminator.adapters.workspace import WorkspaceAdapter
        adapters.append(WorkspaceAdapter(
            watch_paths=ws_cfg.get("watch_paths", []),
            ignore_patterns=ws_cfg.get("ignore_patterns", []),
            agent_id=agent_id,
        ))

    cron_cfg = adapter_cfg.get("cron", {})
    if cron_cfg.get("enabled", False):
        from agentilluminator.adapters.cron import CronAdapter
        adapters.append(CronAdapter(
            journal_unit=cron_cfg.get("journal_unit", "cron"),
            agent_id=agent_id,
        ))

    bridge_cfg = adapter_cfg.get("bridge", {})
    if bridge_cfg.get("enabled", False):
        from agentilluminator.adapters.bridge import BridgeAdapter
        adapters.append(BridgeAdapter(
            listen_port=bridge_cfg.get("listen_port", 19100),
            agent_id=agent_id,
        ))

    sup_cfg = adapter_cfg.get("supervisor", {})
    if sup_cfg.get("enabled", False):
        from agentilluminator.adapters.bridge import SupervisorAdapter
        adapters.append(SupervisorAdapter(
            output_dir=sup_cfg.get("output_dir", "supervisor-output"),
            agent_id=agent_id,
        ))

    return adapters


def _route_event(event, manager) -> None:
    """Route an incoming event to the appropriate open run, or open a new one."""
    from agentilluminator.core.events import EventType

    if event.event_type == EventType.AGENT_WAKE:
        run = manager.open_agent_run(
            agent_id=event.agent_id or "unknown",
            triggered_by=event.detail.get("triggered_by", ""),
            model=event.detail.get("model", ""),
        )
        event.run_id = run.run_id

    elif event.run_id:
        try:
            manager.append_event(event.run_id, event)
        except KeyError:
            pass  # Event arrived for a run we don't know about — drop gracefully
    else:
        # No run_id — attach to first open run for this agent, or drop
        active = [r for r in manager.get_active_agent_runs()
                  if r.agent_id == event.agent_id]
        if active:
            active[0].add_event(event)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="illuminator",
        description="Agent Illuminator — workflow observability for autonomous agents",
    )
    parser.add_argument(
        "--config", default="agentilluminator.yaml",
        help="Path to config file (default: agentilluminator.yaml)"
    )
    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Initialise workspace")
    p_init.add_argument("--workspace", required=True, help="Path to workspace directory")

    # daemon
    sub.add_parser("daemon", help="Start capture daemon")

    # status
    sub.add_parser("status", help="Show active runs")

    # show
    p_show = sub.add_parser("show", help="Show trace for a run")
    p_show.add_argument("run_id", help="Run ID (partial match supported)")

    # report
    p_report = sub.add_parser("report", help="Aggregate traces into a report")
    p_report.add_argument("--since", dest="since_days", type=int, default=7,
                          help="Aggregate traces from last N days (default: 7)")

    # prune
    p_prune = sub.add_parser("prune", help="Prune old trace files")
    p_prune.add_argument("--dry-run", action="store_true", help="Show what would be pruned")

    # query
    p_query = sub.add_parser("query", help="Query runs or events (SQLite backend only)")
    p_query.add_argument("--agent", help="Filter by agent_id")
    p_query.add_argument("--status", help="Filter by status")
    p_query.add_argument("--since", dest="since_days", type=int, default=30)
    p_query.add_argument("--events", action="store_true", help="Query events instead of runs")
    p_query.add_argument("--event-type", dest="event_type", help="Filter events by type")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "daemon": cmd_daemon,
        "status": cmd_status,
        "show": cmd_show,
        "report": cmd_report,
        "prune": cmd_prune,
        "query": cmd_query,
    }

    if args.command not in commands:
        parser.print_help()
        sys.exit(1)

    commands[args.command](args)


if __name__ == "__main__":
    main()
