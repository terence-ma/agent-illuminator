"""
Basic tests for Agent Illuminator core components.
"""

import time
from datetime import datetime

import pytest

from agentilluminator.core.events import (
    AgentRun,
    EventStatus,
    EventType,
    RunStatus,
    TraceEvent,
)
from agentilluminator.core.run_manager import RunManager


# ---------------------------------------------------------------------------
# TraceEvent
# ---------------------------------------------------------------------------

class TestTraceEvent:
    def test_defaults(self):
        e = TraceEvent(event_type=EventType.FILE_WRITE, summary="wrote file.md")
        assert e.status == EventStatus.OK
        assert e.event_id
        assert e.detail == {}
        assert e.tags == []

    def test_to_dict(self):
        e = TraceEvent(event_type=EventType.ASSERTION, summary="check passed", status=EventStatus.PASS)
        d = e.to_dict()
        assert d["event_type"] == "assertion"
        assert d["status"] == "pass"
        assert d["summary"] == "check passed"


# ---------------------------------------------------------------------------
# AgentRun
# ---------------------------------------------------------------------------

class TestAgentRun:
    def test_add_event(self):
        run = AgentRun(run_id="run_test", agent_id="qin")
        e = TraceEvent(event_type=EventType.FILE_WRITE, summary="wrote brief")
        run.add_event(e)
        assert len(run.events) == 1
        assert e.run_id == "run_test"
        assert e.agent_id == "qin"

    def test_has_delivery_receipt(self):
        run = AgentRun(run_id="run_test", agent_id="qin")
        assert not run.has_delivery_receipt()
        run.add_event(TraceEvent(event_type=EventType.DELIVERY_RECEIPT, summary="delivered"))
        assert run.has_delivery_receipt()

    def test_assertions(self):
        run = AgentRun(run_id="run_test", agent_id="qin")
        run.add_event(TraceEvent(event_type=EventType.FILE_WRITE, summary="wrote file"))
        run.add_event(TraceEvent(event_type=EventType.ASSERTION, summary="check", status=EventStatus.PASS))
        assert len(run.assertions()) == 1

    def test_duration(self):
        run = AgentRun(run_id="run_test", agent_id="qin")
        assert run.duration_sec is None
        run.closed_at = datetime.utcnow()
        assert run.duration_sec is not None
        assert run.duration_sec >= 0


# ---------------------------------------------------------------------------
# RunManager
# ---------------------------------------------------------------------------

class TestRunManager:
    def test_open_and_close(self):
        mgr = RunManager()
        run = mgr.open_agent_run("qin", triggered_by="cron:test")
        assert run.status == RunStatus.OPEN
        assert len(mgr.get_active_agent_runs()) == 1

        closed = mgr.close_agent_run(run.run_id)
        assert closed.status == RunStatus.COMPLETE
        assert len(mgr.get_active_agent_runs()) == 0

    def test_append_event(self):
        mgr = RunManager()
        run = mgr.open_agent_run("warren")
        e = TraceEvent(event_type=EventType.TOOL_INVOCATION, summary="ran script")
        mgr.append_event(run.run_id, e)
        assert len(run.events) == 1

    def test_auto_close_on_agent_close_event(self):
        mgr = RunManager()
        closed_runs = []
        mgr.on_run_closed(lambda r: closed_runs.append(r))
        run = mgr.open_agent_run("qin")
        mgr.append_event(
            run.run_id,
            TraceEvent(event_type=EventType.AGENT_CLOSE, summary="session closed", status=EventStatus.OK)
        )
        assert len(closed_runs) == 1
        assert closed_runs[0].run_id == run.run_id
        assert len(mgr.get_active_agent_runs()) == 0

    def test_orphan_detection(self):
        mgr = RunManager(orphan_timeout_sec=0)
        run = mgr.open_agent_run("xiao-qin")
        time.sleep(0.01)
        orphans = mgr.detect_orphans()
        assert len(orphans) == 1
        assert orphans[0].status == RunStatus.ORPHANED
        assert len(mgr.get_active_agent_runs()) == 0

    def test_unknown_run_id_raises(self):
        mgr = RunManager()
        with pytest.raises(KeyError):
            mgr.append_event("nonexistent-run-id",
                             TraceEvent(event_type=EventType.ANNOTATION, summary="test"))

    def test_run_id_format(self):
        mgr = RunManager()
        run = mgr.open_agent_run("my-agent")
        assert run.run_id.startswith("run_")
        assert "my-agent" in run.run_id


# ---------------------------------------------------------------------------
# Storage: MarkdownBackend
# ---------------------------------------------------------------------------

class TestMarkdownBackend:
    def test_write_and_read(self, tmp_path):
        from agentilluminator.storage import MarkdownBackend
        backend = MarkdownBackend(str(tmp_path / "traces"), str(tmp_path / "reports"))

        run = AgentRun(run_id="run_test_abc", agent_id="qin", triggered_by="cron:test")
        run.add_event(TraceEvent(event_type=EventType.FILE_WRITE, summary="wrote brief"))
        run.add_event(TraceEvent(event_type=EventType.DELIVERY_RECEIPT, summary="delivered",
                                  status=EventStatus.OK))
        run.closed_at = datetime.utcnow()
        run.status = RunStatus.COMPLETE

        fpath = backend.write_agent_run(run)
        assert fpath.exists()
        content = fpath.read_text()
        assert "run_test_abc" in content
        assert "qin" in content
        assert "FILE_WRITE" in content or "file_write" in content
        assert "delivery_receipt" in content

    def test_prune_archive(self, tmp_path):
        from agentilluminator.storage import MarkdownBackend
        backend = MarkdownBackend(str(tmp_path / "traces"), str(tmp_path / "reports"))

        run = AgentRun(run_id="run_old", agent_id="qin")
        run.closed_at = datetime.utcnow()
        run.status = RunStatus.COMPLETE
        fpath = backend.write_agent_run(run)
        assert fpath.exists()

        # Prune with 0-day retention — should archive everything
        count = backend.prune(retention_days=0, on_expire="archive")
        assert count >= 1
        assert not fpath.exists()


# ---------------------------------------------------------------------------
# SDK
# ---------------------------------------------------------------------------

class TestSDK:
    def test_illuminate_fails_silently_when_no_daemon(self):
        from agentilluminator.sdk import illuminate
        # Should not raise even with no daemon running
        illuminate.configure(base_url="http://127.0.0.1:19199")  # nothing listening
        illuminate.event("file_write", summary="test event")
        illuminate.assertion("test_check", passed=True)
        illuminate.annotation("test annotation")
        illuminate.gap("test_gap", description="test gap description")

    def test_illuminate_disabled(self):
        from agentilluminator.sdk import _Illuminate
        ill = _Illuminate()
        ill.configure(disabled=True)
        # Should be a no-op — no error
        ill.event("file_write", summary="should not send")
