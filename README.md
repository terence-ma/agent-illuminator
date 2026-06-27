# Agent Illuminator

<p align="center">
  <img src="assets/logo.png" width="280" alt="Agent Illuminator — I will devour all"/>
</p>

```
    your agent said: "task complete."
    your agent did: who knows.
    until now.
```

**Workflow-level observability for autonomous agents.**  
*Because "it said it worked" is not a monitoring strategy.*

---

Most agent observability tools watch LLM calls. Prompt in, completion out, tokens counted, latency logged. Useful. Also: completely blind to whether your agent actually *did anything*.

Agent Illuminator watches what your agent **does** — files written, tools called, tasks transitioned, crons fired, supervisors verified, deliverables confirmed sent. It captures all of it from outside the agent, deposits structured trace files into your workspace, and doesn't care whether your agent cooperated.

No cloud. No database required. No asking your agents nicely to instrument themselves.

---

## The problem, plainly

Your pipeline ran. The agent session closed with `status: complete`. The deliverable never arrived.

You open four log files, reconstruct a timeline manually, and spend forty minutes figuring out that the cron fired three minutes late, the supervisor marked it passing before the delivery receipt existed, and your agent technically did everything except the part that mattered.

Agent Illuminator gives you a single trace file with a timestamped record of every file touched, every tool called, every supervisor verdict, every cron event — in order. You find the break in thirty seconds.

---

## Who this is for

You're probably the right audience if any of these land:

- You've used the phrase "I thought the agent handled that" and been wrong
- Your pipeline has three or more agents and you debug it like an archaeologist
- You have an "independent supervisor" that you've started to suspect isn't very independent
- You've written the words `task complete` in a cron log and immediately felt uneasy
- You've ever explained to a stakeholder that the agent "said it worked"

If your agents are stateless single-call API wrappers, you probably want Langfuse. Scroll down for the honest comparison.

---

## Quickstart

```bash
pip install agentilluminator
illuminator init --workspace /path/to/workspace
illuminator daemon --config agentilluminator.yaml
```

That's it. The daemon watches your workspace and starts capturing. No agent changes required.

```bash
# What happened in this run?
illuminator show run_20260626_0745_qin_abc123

# How reliable has qin been this week?
illuminator report --since 7d

# Clean up old traces
illuminator prune
```

---

## What a trace looks like

```markdown
---
run_id: run_20260626_074512_qin_abc123
agent_id: qin
triggered_by: cron:morning-brief-supervisor-0745
status: complete
duration_sec: 1112
---

## Timeline

| Time     | Type               | Summary                            | Status |
|----------|--------------------|------------------------------------|--------|
| 07:45:12 | agent_wake         | Triggered by cron                  | ok     |
| 07:45:18 | file_read          | Read source-of-truth.md            | ok     |
| 07:46:02 | tool_invocation    | bash: verify_contract.py           | pass   |
| 07:51:14 | file_write         | brief-2026-06-26.md → delivery/    | ok     |
| 07:51:20 | delivery_receipt   | Telegram delivery confirmed        | ok     |
| 07:51:22 | supervisor_verdict | Pipeline complete — all steps pass | pass   |

## Assertions
- [PASS] Delivery receipt present before session close
- [PASS] No steps skipped

## Gaps
None detected this run.
```

One file. One run. Everything that happened, in order.

---

## Agent-Side SDK (optional)

For when you want your agents to add context — not because you need them to, but because it's nice.

```python
from agentilluminator.sdk import illuminate

illuminate.event("file_write", summary="Wrote daily brief", detail={"path": "output/brief.md"})
illuminate.assertion("delivery_receipt_present", passed=True)
illuminate.annotation("Rectification loop triggered — QC failed on first pass, corrected")
illuminate.delivery_receipt(destination="telegram", artifact="morning-brief-2026-06-26.md")
```

The SDK fails silently if the daemon isn't running. Your agent will never crash because someone forgot to start the observability daemon. We've all been there.

---

## Capture Adapters

Agent Illuminator captures from five sources. None of them require agent cooperation to function.

| Adapter | What It Captures | Needs Agent Changes? |
|---|---|---|
| `WorkspaceAdapter` | File writes/reads via inotify (Linux) / fswatch (macOS) | No |
| `CronAdapter` | Cron fires, missed crons, late crons via systemd journal | No |
| `BridgeAdapter` | Tool calls, agent wake/close via HTTP middleware | Minimal |
| `SupervisorAdapter` | Supervisor verdicts, assertions, gaps | No |
| `AgentSideAdapter` | Annotations, assertions, custom events | Agent imports SDK |

Adding support for a new agent runtime is one class:

```python
from agentilluminator.adapters.base import TraceAdapter, TraceEvent

class MyRuntimeAdapter(TraceAdapter):
    def poll(self) -> list[TraceEvent]:
        ...
```

---

## Storage

**Markdown (default):** Traces are plain files. Human-readable, git-friendly, zero dependencies beyond PyYAML. Works everywhere. No database to set up, maintain, or accidentally corrupt.

**SQLite (optional):** For when "find the file with the run ID in the name" stops being a viable search strategy.

```yaml
storage:
  backend: sqlite          # flip when you're ready
  db_path: .illuminator/illuminator.db
```

---

## Configuration

```yaml
workspace: /path/to/your/workspace
agent_id: my-agent

storage:
  backend: markdown
  trace_dir: traces
  report_dir: reports
  retention_days: 90
  on_expire: archive

adapters:
  workspace:
    enabled: true
    watch_paths:
      - /path/to/your/workspace
    ignore_patterns:
      - "*.tmp"
      - "traces/*"
  cron:
    enabled: true
    journal_unit: cron
    expected_crons:
      - id: morning-brief
        schedule: "0 7 * * *"
  bridge:
    enabled: false
    listen_port: 19100
  supervisor:
    enabled: false
    output_dir: /path/to/supervisor/output
```

---

## Use Cases

**Failure diagnosis in multi-agent pipelines.**
Three agents. One deliverable. It didn't arrive. Open the pipeline trace. Find the break. Stop digging through four log files at midnight.

**Verifying your independent supervisor.**
Your supervisor says the pipeline passed. Did it check before or after the delivery receipt existed? Agent Illuminator captured the verdict timestamp. You're welcome.

**Cron reliability.**
Your 07:45 brief didn't run at 07:45. Or it did, but only technically. CronAdapter captures fires, detects misses against declared schedules, and flags late starts. "The cron ran" and "the cron ran on time" are different statements.

**Debugging context loss after compaction.**
Long sessions compact. Agents forget things. They then report tools as unavailable, skip steps, or confidently describe work they haven't done. Compaction events appear in the trace timeline, correlated with whatever went wrong immediately after.

**Onboarding new agents.**
Before promoting a new agent to production: did it read its SOPs? Write the expected artifacts? Complete without orphaned runs? The trace tells you. The agent doesn't get a vote.

**Audit trails.**
For workflows where you need to prove a process was followed — investment research, compliance, client deliverables — traces give you a timestamped record that isn't based on the agent's own account of events.

**Weekly reliability reporting.**
```bash
illuminator report --since 7d
```
Pass rate per agent. Gap frequency. Cron reliability. Mean session duration. All from flat files. No database query required.

---

## How It Compares

Agent Illuminator is not trying to replace anything. It fills a gap the others leave open.

### vs. Langfuse / LangSmith

| | Langfuse / LangSmith | Agent Illuminator |
|---|---|---|
| Traces | LLM API calls | Workflow behaviour |
| Unit of observation | The API call | The run |
| Agent changes required | Yes | No (baseline) |
| Self-hosted | Langfuse yes, LangSmith no | Always |
| Database required | Yes | No |
| Answers | "Was the prompt good?" | "Did the agent do its job?" |

**Use both.** They answer different questions. Langfuse tells you the LLM responded well. Agent Illuminator tells you the agent behaved correctly. Your pipeline can fail both tests independently.

### vs. OpenTelemetry / Jaeger

Built for synchronous request/response services. Does not model agents that wake on demand, run for forty minutes, write files, and hand off to other agents. Agent Illuminator does.

### vs. Prometheus / Grafana

Metrics answer "how many times did X happen." Traces answer "what happened in this specific run, in what order, and where did it break." Both useful. Neither replaces the other.

### vs. Logging

Logs are what the agent chose to print. Agent Illuminator captures what the agent actually did. These are not always the same thing.

---

## Event Reference

| Event | What It Means |
|---|---|
| `agent_wake` | Session started — triggered by what, when, which model |
| `agent_close` | Session ended — normal, compaction, error, or timeout |
| `file_write` | Agent wrote a file |
| `file_read` | Agent read a file |
| `tool_invocation` | Tool called — inputs, outputs, pass/fail |
| `cron_fire` | Cron triggered — on time, late, or reconstructed |
| `cron_miss` | Expected cron didn't fire within tolerance |
| `task_transition` | Task state changed (assigned → active → complete / failed) |
| `supervisor_verdict` | Supervisor checked the pipeline — pass or fail |
| `delivery_receipt` | Terminal artifact confirmed delivered |
| `assertion` | Named condition checked — pass or fail |
| `gap_detected` | Something missing or broken logged to gap registry |
| `annotation` | Human or agent added a semantic note |

---

## Retention

Old traces don't delete themselves. Tell Ambit what to do with them:

```yaml
storage:
  retention_days: 90
  on_expire: archive    # moves to traces/archive/YYYY-MM/
                        # or: delete (gone forever, no drama)
```

```bash
illuminator prune --dry-run    # see what would go
illuminator prune              # do it
```

---

## Examples

- [`examples/generic/`](examples/generic/) — minimal config, any agent runtime
- [`examples/openclaw/`](examples/openclaw/) — reference config for OpenClaw + Paperclip infrastructure

---

## Roadmap

- [ ] SQLite query CLI (`illuminator query`)
- [ ] Pipeline trace aggregation across workspaces
- [ ] JSON-L output for external consumers
- [ ] Prometheus exporter
- [ ] Static HTML report viewer (no server)
- [ ] Adapter examples for AutoGen, CrewAI

---

## Contributing

MIT licensed. The best contribution is a new adapter for a runtime you're actually using.

```bash
git clone https://github.com/terence-ma/agent-illuminator
cd agent-illuminator
pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT. See [LICENSE](LICENSE).

---

*Part of a suite of agent infrastructure primitives. See also:*  
*[Agent Ambit](https://github.com/terence-ma/agent-ambit) — policy-enforced state and memory for agent teams*  
*[agentic-workflow-integrity](https://github.com/terence-ma/agentic-workflow-integrity) — failure taxonomy and verification patterns*
