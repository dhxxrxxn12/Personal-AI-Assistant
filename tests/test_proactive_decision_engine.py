from __future__ import annotations

from datetime import datetime, timezone

import pytest

from automation.learning_memory import LearningMemory
from automation.permission_manager import PermissionManager
from automation.proactive_decision_engine import (
    ActionResult,
    DecisionState,
    ProactiveDecisionEngine,
    RiskLevel,
    Situation,
)
from automation.briefing.models import BriefingContext, ProviderReport, ProviderState


class DummyRegistry:
    def __init__(self):
        self.calls = []

    def has(self, action: str) -> bool:
        return action in {"open_app", "send_message", "browser_control", "file_controller", "delete_file"}

    def run(self, name: str, parameters: dict | None = None, ctx: dict | None = None):
        self.calls.append((name, parameters or {}, ctx or {}))
        if name == "open_app":
            return "Calculator opened successfully"
        if name == "send_message":
            return "Message sent"
        if name == "file_controller":
            return "File updated"
        if name == "delete_file":
            return "Deleted"
        raise RuntimeError(f"Unknown action: {name}")


def _context(*, reminder=None, task=None, system=None, weather=None):
    items = []
    if reminder:
        items.append({"source": "reminders", "title": reminder, "content": reminder, "category": "reminders", "priority": "high"})
    if task:
        items.append({"source": "tasks", "title": task, "content": task, "category": "tasks", "priority": "medium"})
    if system:
        items.append({"source": "system", "title": system, "content": system, "category": "system", "priority": "high"})
    if weather:
        items.append({"source": "weather", "title": weather, "content": weather, "category": "weather", "priority": "low"})
    return BriefingContext(
        collected_at=datetime.now(timezone.utc).isoformat(),
        reports=(
            ProviderReport(provider="reminders", state=ProviderState.AVAILABLE.value, items=({"reminder": reminder},) if reminder else ()),
            ProviderReport(provider="tasks", state=ProviderState.AVAILABLE.value, items=({"workflow": task},) if task else ()),
            ProviderReport(provider="system", state=ProviderState.AVAILABLE.value, items=({"summary": system},) if system else ()),
        ),
        items=tuple(
            Situation.normalize_item(item) for item in items
        ),
    )


def _fresh_permissions(tmp_path):
    return PermissionManager(path=tmp_path / "permissions.json")


def test_no_situation_means_no_action(tmp_path):
    engine = ProactiveDecisionEngine(action_registry=DummyRegistry(), permissions=_fresh_permissions(tmp_path))
    decision = engine.decide(_context())
    assert decision.state == DecisionState.NO_ACTION
    assert decision.risk == RiskLevel.LOW


def test_reminder_approaching_decision_is_inform(tmp_path):
    engine = ProactiveDecisionEngine(action_registry=DummyRegistry(), permissions=_fresh_permissions(tmp_path))
    decision = engine.decide(_context(reminder="Submit assignment in 15 minutes"))
    assert decision.state == DecisionState.INFORM
    assert "assignment" in decision.situation.summary.lower()


def test_low_risk_action_executes(tmp_path):
    registry = DummyRegistry()
    engine = ProactiveDecisionEngine(action_registry=registry, permissions=_fresh_permissions(tmp_path))
    decision = engine.decide({"items": [{"source": "system", "title": "Open Calculator", "content": "Open Calculator", "category": "system", "priority": "medium"}]})
    assert decision.state == DecisionState.EXECUTE
    assert decision.action == "open_app"
    result = engine.execute_decision(decision)
    assert result.status == "SUCCESS"
    assert registry.calls[0][0] == "open_app"


def test_high_risk_action_requires_permission(tmp_path):
    engine = ProactiveDecisionEngine(action_registry=DummyRegistry(), permissions=_fresh_permissions(tmp_path))
    decision = engine.decide({"items": [{"source": "system", "title": "Send message", "content": "Send message to user", "category": "system", "priority": "high"}]})
    assert decision.state == DecisionState.ASK_PERMISSION
    assert decision.risk == RiskLevel.HIGH


def test_critical_action_is_blocked(tmp_path):
    engine = ProactiveDecisionEngine(action_registry=DummyRegistry(), permissions=_fresh_permissions(tmp_path))
    decision = engine.decide({"items": [{"source": "system", "title": "Delete data", "content": "Delete selected file", "category": "system", "priority": "critical"}]})
    assert decision.state == DecisionState.BLOCKED
    assert decision.risk == RiskLevel.CRITICAL


def test_permission_denied_blocks_execution(tmp_path):
    registry = DummyRegistry()
    permissions = _fresh_permissions(tmp_path)
    engine = ProactiveDecisionEngine(action_registry=registry, permissions=permissions)
    engine.permissions.set_permission("open_app", "blocked")
    decision = engine.decide({"items": [{"source": "system", "title": "Open Calculator", "content": "Open Calculator", "category": "system", "priority": "medium"}]})
    assert decision.state == DecisionState.BLOCKED
    assert engine.execute_decision(decision).status == "BLOCKED"
    assert registry.calls == []


def test_failed_verification_never_reports_success(tmp_path):
    registry = DummyRegistry()
    engine = ProactiveDecisionEngine(action_registry=registry, permissions=_fresh_permissions(tmp_path))
    decision = engine.decide({"items": [{"source": "system", "title": "Open Calculator", "content": "Open Calculator", "category": "system", "priority": "medium"}]})
    result = engine.execute_decision(decision, verification=lambda: False)
    assert result.status == "FAILED"
    assert result.verification_passed is False


def test_duplicate_situation_is_not_repeated(tmp_path):
    engine = ProactiveDecisionEngine(action_registry=DummyRegistry(), permissions=_fresh_permissions(tmp_path))
    first = engine.decide({"items": [{"source": "system", "title": "Open Calculator", "content": "Open Calculator", "category": "system", "priority": "medium"}]})
    second = engine.decide({"items": [{"source": "system", "title": "Open Calculator", "content": "Open Calculator", "category": "system", "priority": "medium"}]})
    assert first.state == DecisionState.EXECUTE
    assert second.state == DecisionState.NO_ACTION
    assert second.reason.startswith("already handled")


def test_provider_failure_does_not_break_decision_engine(tmp_path):
    engine = ProactiveDecisionEngine(action_registry=DummyRegistry(), permissions=_fresh_permissions(tmp_path))
    context = BriefingContext(
        collected_at="2024-01-01T00:00:00+00:00",
        reports=(ProviderReport(provider="system", state=ProviderState.UNAVAILABLE.value, reason="failed"),),
        items=(),
    )
    decision = engine.decide(context)
    assert decision.state == DecisionState.NO_ACTION


def test_learning_memory_records_outcome(tmp_path):
    registry = DummyRegistry()
    memory = LearningMemory(path=None)
    engine = ProactiveDecisionEngine(action_registry=registry, learning_memory=memory, permissions=_fresh_permissions(tmp_path))
    decision = engine.decide({"items": [{"source": "system", "title": "Open Calculator", "content": "Open Calculator", "category": "system", "priority": "medium"}]})
    result = engine.execute_decision(decision)
    assert result.status == "SUCCESS"
    assert memory.names() or engine.learning_memory.names()


def test_action_registry_is_used_for_execution(tmp_path):
    registry = DummyRegistry()
    engine = ProactiveDecisionEngine(action_registry=registry, permissions=_fresh_permissions(tmp_path))
    decision = engine.decide({"items": [{"source": "system", "title": "Open Calculator", "content": "Open Calculator", "category": "system", "priority": "medium"}]})
    assert decision.action == "open_app"
    assert engine.execute_decision(decision).details["action_id"] == "open_app"
