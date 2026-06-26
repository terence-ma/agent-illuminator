# Contributing to Agent Illuminator

Thank you for your interest in contributing.

## The most valuable contributions

**New adapter implementations.** Agent Illuminator ships with five built-in adapters targeting common self-hosted infrastructure patterns. Every new adapter extends the tool's reach to a different agent runtime or coordination system. If you're running agents on AutoGen, CrewAI, a custom AMQP-based task queue, or anything else — a new adapter is the highest-value contribution you can make.

To add an adapter, implement the `TraceAdapter` interface in `agentilluminator/adapters/` and add an example config entry. See `adapters/workspace.py` for a complete reference.

**Bug reports with trace files.** If something is captured incorrectly or not captured at all, a real trace file (with sensitive content redacted) is worth more than any description.

**Example configurations.** If you've got Agent Illuminator working with a specific agent runtime, a config example in `examples/` helps others do the same.

## Setup

```bash
git clone https://github.com/terence-ma/agent-illuminator
cd agent-illuminator
pip install -e ".[dev]"
pytest
```

## Code style

- Python 3.11+
- Type annotations throughout
- No external dependencies in core (`agentilluminator/core/`, `agentilluminator/sdk/`)
- Adapters may have optional dependencies — declare them in `pyproject.toml` as extras
- The SDK must remain zero-dependency and fail silently under all conditions

## Tests

```bash
pytest                    # run all tests
pytest tests/ -v          # verbose
pytest --cov=agentilluminator  # with coverage
```

All PRs should include tests for new behaviour.

## Adapter interface

```python
from agentilluminator.adapters.base import TraceAdapter, TraceEvent

class MyAdapter(TraceAdapter):
    def poll(self) -> list[TraceEvent]:
        # Return new events since last call. Manage your own cursor.
        ...

    def setup(self) -> None:
        # Called once by the daemon before first poll.
        ...

    def teardown(self) -> None:
        # Called once on daemon shutdown.
        ...
```

## Pull requests

- One logical change per PR
- Update the relevant example config if you add a new adapter option
- If you're adding a new adapter, add it to the adapter table in README.md
