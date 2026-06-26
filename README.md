# Agent Illuminator

**Workflow-level observability for autonomous agents. See what your agents actually did — not what they reported.**

---

Most agent observability tools trace LLM API calls: prompt in, completion out, tokens used, latency recorded. That is useful. It is also insufficient.

When your agent is a persistent process that wakes on a schedule, reads files, writes artifacts, hands off to another agent, and is supposed to deliver something by a deadline — the LLM call is the least interesting thing to observe. What matters is: did it read the right input? Did it write the correct output? Did it complete the handoff? Did the next agent wake? Was the deliverable actually sent?

**Agent Illuminator captures all of this.** It observes agents from outside their own self-reporting, treating file writes, task transitions, cron events, supervisor verdicts, and delivery receipts as first-class trace events. It deposits structured, human-readable trace files into your workspace. It requires no database, no cloud service, and no changes to your agents to get started.

---

## The Problem in Plain Terms

Your agent session closed with status `complete`. But the deliverable never arrived.

Without observability, you dig through logs, reconstruct a timeline manually, and try to figure out where the chain broke. Did the agent write the file? Did the supervisor check it? Did the cron that was supposed to trigger the next step actually fire?

With Agent Illuminator, you open a single trace file and see the full timeline: every file touched, every tool called, every task state transition, every supervisor verdict, every cron event — in order, with timestamps. You find the break in seconds.

This is the problem Agent Illuminator solves.

---

## Who This Is For

- Teams running **persistent, self-hosted agent infrastructure** where agents wake on demand, coordinate via task management systems, and operate on file workspaces
- Engineers building **multi-agent pipelines** where agents hand off to each other and a failure anywhere in the chain is hard to diagnose
- Anyone who needs an **audit trail** proving that an agent-driven process was followed — for compliance, for reporting, for debugging
- Developers who want **reliability metrics** across agent runs over time: which agent fails most often, which step breaks most frequently, which cron is chronically late

If your agents are stateless API wrappers running in a single synchronous call, you probably want Langfuse instead (see [How It Compares](#how-it-compares)).

---

## How It Works

Agent Illuminator runs a lightweight daemon alongside your agent infrastructure. The daemon polls capture adapters — filesystem watchers, cron monitors, HTTP middleware hooks, supervisor output — and normalises events into a common schema. When a run closes, a structured trace file is written to your workspace.

```
your-workspace/
  traces/
    agent/
      2026-06-26_0745_qin_abc123.md       ← one file per agent session
    pipeline/
      2026-06-26_0745_morning-brief.md    ← one file per pipeline run
  reports/
      weekly-summary-2026-06-23.md        ← aggregated reliability report
```

No database required. Traces are Markdown with YAML frontmatter — readable by humans, parseable by machines, portable across environments. An optional SQLite backend is available for teams who want queryable history at scale.

---

## Quickstart

```bash
pip install agentilluminator
```

```bash
# Initialise trace directory in your workspace
illuminator init --workspace /path/to/workspace

# Start the capture daemon
illuminator daemon --config agentilluminator.yaml

# View active runs
illuminator status

# Inspect a specific trace
illuminator show run_20260626_0745_qin_abc123

# Aggregate the last 7 days into a report
illuminator report --since 7d
```

---

## Agent-Side SDK (Optional)

For richer traces, agents can emit structured events directly using the lightweight SDK. It has zero external dependencies and fails silently if no daemon is running — so it never crashes your agent.

```python
from agentilluminator.sdk import illuminate

# Emit a trace event from inside your agent
illuminate.event("file_write", summary="Wrote daily brief", detail={"path": "output/brief.md"})
illuminate.assertion("delivery_receipt_present", passed=True)
illuminate.annotation("Rectification loop triggered — QC failed on first pass")
```

Agent-side instrumentation is entirely optional. Baseline tracing works with zero agent changes — the daemon captures from the outside via filesystem and system-level adapters.

---

## Capture Adapters

Agent Illuminator ships with five runtime-agnostic adapters. Implementing support for a new agent runtime requires one adapter class.

| Adapter | What It Captures | Agent Changes Required |
|---|---|---|
| `WorkspaceAdapter` | File writes and reads via inotify (Linux) / fswatch (macOS) | None |
| `CronAdapter` | Cron fires, missed crons, late crons via systemd journal | None |
| `BridgeAdapter` | Tool invocations, agent wake/close via HTTP middleware | Minimal — route agent traffic through bridge |
| `SupervisorAdapter` | Supervisor verdicts, assertions, gap detections | None — reads supervisor stdout |
| `AgentSideAdapter` | Any event type, annotations, assertions | Agent imports SDK |

Custom adapters implement a simple interface:

```python
from agentilluminator.adapters.base import TraceAdapter, TraceEvent

class MyRuntimeAdapter(TraceAdapter):
    def poll(self) -> list[TraceEvent]:
        # Return new events since last poll
        ...
```

---

## Trace File Format

Each agent run produces one Markdown file with YAML frontmatter.

```markdown
---
run_id: run_20260626_074512_qin_abc123
agent_id: qin
model: claude-sonnet-4-6
triggered_by: cron:morning-brief-supervisor-0745
triggered_at: 2026-06-26T07:45:12+08:00
closed_at: 2026-06-26T08:03:44+08:00
duration_sec: 1112
status: complete
pipeline_run_id: run_20260626_074500_morning-brief
---

# Run: qin — 2026-06-26 07:45

## Timeline

| Time     | Type               | Summary                              | Status  |
|----------|--------------------|--------------------------------------|---------|
| 07:45:12 | agent_wake         | Triggered by cron                    | ok      |
| 07:45:18 | file_read          | Read source-of-truth.md              | ok      |
| 07:46:02 | tool_invocation    | bash: verify_contract.py             | pass    |
| 07:51:14 | file_write         | brief-2026-06-26.md → delivery/      | ok      |
| 07:51:20 | delivery_receipt   | Telegram delivery confirmed          | ok      |
| 07:51:22 | supervisor_verdict | Pipeline complete — all steps pass   | pass    |
| 08:03:44 | agent_close        | Session closed normally              | ok      |

## Assertions

- [PASS] Delivery receipt present before session close
- [PASS] All required cron jobs live at startup
- [PASS] No pipeline steps skipped

## Gaps

None detected this run.
```

---

## Storage Backends

### Markdown (default)
Traces are plain Markdown files on disk. Human-readable, zero dependencies, portable. Works everywhere.

```yaml
storage:
  backend: markdown
  trace_dir: traces
  report_dir: reports
```

### SQLite (optional)
For queryable history across many runs. Traces are still written as Markdown; SQLite is an additional index.

```yaml
storage:
  backend: sqlite
  db_path: .illuminator/illuminator.db
  trace_dir: traces
  report_dir: reports
```

Query example:
```bash
illuminator query --agent qin --status failed --since 30d
illuminator query --pipeline morning-brief --event delivery_receipt --missing
```

---

## Configuration

```yaml
# agentilluminator.yaml

workspace: /path/to/your/workspace
agent_id: my-agent            # default agent id if not specified per-run

storage:
  backend: markdown            # markdown | sqlite
  trace_dir: traces
  report_dir: reports
  retention_days: 90           # traces older than this are archived
  on_expire: archive           # archive | delete

adapters:
  workspace:
    enabled: true
    watch_paths:
      - /path/to/your/workspace
    ignore_patterns:
      - "*.tmp"
      - "traces/*"
      - "reports/*"
  cron:
    enabled: true
    journal_unit: cron         # systemd unit to monitor
    expected_crons:            # optional: declare crons to detect misses
      - id: morning-brief
        schedule: "0 7 * * *"
  bridge:
    enabled: false
    listen_port: 19100         # illuminator opens an HTTP receiver
  supervisor:
    enabled: false
    output_dir: /path/to/supervisor/output

pipelines:
  morning-brief:
    agents: [warren, qin]
    timeout_sec: 7200
    terminal_assertion: delivery_receipt
```

---

## Use Cases

### 1. Failure diagnosis in multi-agent pipelines
Three agents run in sequence. The final deliverable never arrives. Open the pipeline trace and see exactly which agent ran last, what it wrote, and where the chain broke — in seconds, not hours.

### 2. Compliance and audit trails
Your agent-driven process produces reports for stakeholders. You need to prove the process was followed. Agent Illuminator provides a timestamped, tamper-evident record of every step, independent of agent self-reporting.

### 3. Cron and scheduling reliability
Agents triggered on a schedule are only as reliable as their cron configuration. Agent Illuminator captures every cron fire, detects missed fires against a declared schedule, and flags late starts. Know immediately when your 07:45 brief didn't fire at 07:45.

### 4. Verifying independent supervisors
Your supervisor checks the pipeline and marks it complete. But did it actually check? Agent Illuminator captures supervisor verdicts from outside the supervisor itself — so you can verify the verifier. A supervisor verdict without a corresponding delivery receipt in the trace is a flag, not an approval.

### 5. Debugging context loss and compaction
Long-running agent sessions compact and lose context. Agents then report tools as unavailable or skip steps they completed before compaction. Agent Illuminator makes compaction events visible in the trace timeline, correlating them with subsequent behaviour changes.

### 6. Agent reliability scoring
Run `illuminator report --since 30d` to get a scorecard across all agents and pipelines: pass rates, gap frequencies, common failure types, cron reliability, mean session duration. All from flat files — no database required for the default backend.

### 7. Onboarding and validation
Before promoting a new agent to production, run it through a validation pipeline and inspect its trace. Did it read its configuration? Write the expected artifacts? Close without orphaned runs? The trace answers these without manual log review.

### 8. Regression detection across deployments
Compare trace profiles before and after a model or prompt update. Did the new version touch different files? Skip steps? Run longer? Produce more gaps? Traces give you a before/after record grounded in behaviour, not self-assessment.

---

## How It Compares

Agent Illuminator is not a replacement for existing observability tools. It fills a gap they leave open.

### vs. Langfuse / LangSmith

| | Langfuse / LangSmith | Agent Illuminator |
|---|---|---|
| **What it traces** | LLM API calls (prompt, completion, tokens, latency) | Agent workflow behaviour (files, tasks, crons, deliveries) |
| **Unit of observation** | The API call | The run (session or pipeline) |
| **Agent changes required** | Yes — SDK instrumentation | No — baseline from outside |
| **Self-hosted** | Langfuse yes, LangSmith no | Yes, always |
| **Database required** | Yes | No (Markdown default) |
| **Best for** | Prompt debugging, cost tracking, model eval | Workflow compliance, failure diagnosis, reliability tracking |

**Use both.** Langfuse tells you whether the LLM responded well. Agent Illuminator tells you whether the agent behaved correctly. These are different questions.

### vs. OpenTelemetry / Jaeger

OpenTelemetry traces distributed service calls. It works well for synchronous request/response architectures. It does not model persistent agents that wake on demand, operate asynchronously over minutes or hours, and communicate via file artifacts and task assignment rather than direct RPC. Agent Illuminator is purpose-built for that model.

### vs. Prometheus / Grafana

Prometheus captures metrics — counts, gauges, histograms. It answers "how many times did X happen" and "is Y above threshold." It does not capture the narrative of a specific run: what happened, in what order, and where the chain broke. Agent Illuminator produces run-level traces, not aggregate metrics. They complement each other: pipe Agent Illuminator's structured output into Prometheus if you want dashboards.

### vs. Logging (stdout / log files)

Logs are unstructured and agent-reported. They capture what the agent chose to print, not what it actually did. Agent Illuminator captures from outside the agent — filesystem events, system journal, HTTP middleware — so the trace reflects ground truth regardless of what the agent logged.

---

## Event Reference

| Event Type | Description |
|---|---|
| `agent_wake` | Agent session starts — triggered by what, at what time, with which model |
| `agent_close` | Agent session ends — normal, compaction, error, or timeout |
| `file_write` | Agent writes a file to its workspace |
| `file_read` | Agent reads a file |
| `tool_invocation` | Tool called — inputs, outputs, pass/fail |
| `cron_fire` | Scheduled cron triggers — on time, late, or reconstructed from journal |
| `cron_miss` | Expected cron did not fire within tolerance window |
| `task_transition` | Task state changes (e.g. assigned → active → complete / failed) |
| `supervisor_verdict` | Independent supervisor checks a pipeline and emits pass/fail |
| `delivery_receipt` | Terminal artifact confirmed delivered to its destination |
| `assertion` | Agent or supervisor asserts a condition — pass or fail |
| `gap_detected` | A gap or failure is logged to the gap registry |
| `annotation` | Human or agent adds a semantic label to a run |

---

## Retention

Traces older than `retention_days` (default: 90) are handled according to `on_expire`:

- `archive` (default): moved to `traces/archive/YYYY-MM/`
- `delete`: permanently removed

The daemon checks retention on startup and once daily. To run manually:

```bash
illuminator prune --dry-run    # show what would be pruned
illuminator prune              # execute
```

---

## Examples

See [`examples/`](examples/) for working configurations:

- [`examples/generic/`](examples/generic/) — minimal setup for any agent runtime
- [`examples/openclaw/`](examples/openclaw/) — reference implementation for OpenClaw + Paperclip agent infrastructure

---

## Roadmap

- [ ] SQLite query CLI (`illuminator query`)
- [ ] Pipeline trace aggregation across workspaces
- [ ] JSON-L output format for external consumers
- [ ] Prometheus exporter (metrics from trace data)
- [ ] Web viewer (static HTML report, no server required)
- [ ] Additional adapter examples (AutoGen, CrewAI)

---

## Contributing

Agent Illuminator is MIT licensed. Contributions welcome — especially new adapter implementations for other agent runtimes.

```bash
git clone https://github.com/terence-ma/agent-illuminator
cd agent-illuminator
pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT. See [LICENSE](LICENSE).
