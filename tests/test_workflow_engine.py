from __future__ import annotations

from automation.learning_memory import LearningMemory
from automation.permission_manager import PermissionManager
from automation.workflow_engine import (
    Workflow,
    WorkflowEngine,
    WorkflowState,
    WorkflowStep,
    WorkflowStepState,
)


class DummyRegistry:
    def __init__(self):
        self.calls = []
        self.failures = {}

    def has(self, action: str) -> bool:
        return action in {
            "weather_report",
            "reminder",
            "file_controller",
            "generate_briefing",
            "send_message",
            "retry_action",
            "open_app",
        }

    def run(self, name: str, parameters: dict | None = None, ctx: dict | None = None):
        self.calls.append((name, parameters or {}, ctx or {}))
        if name == "retry_action":
            attempt = int(parameters.get("attempt", 0))
            if attempt == 0:
                raise RuntimeError("transient failure")
            return "recovered"
        if name == "send_message":
            return "Message sent"
        if name == "weather_report":
            return "Sunny 72F"
        if name == "generate_briefing":
            return "Briefing generated"
        if name == "reminder":
            return "Reminder loaded"
        if name == "file_controller":
            return "Task file updated"
        if name == "open_app":
            return "Calculator opened"
        raise RuntimeError(f"Unknown action: {name}")


def test_workflow_creation_and_validation():
    registry = DummyRegistry()
    engine = WorkflowEngine(action_registry=registry, permissions=PermissionManager())
    workflow = engine.create_workflow(
        "Prepare morning briefing",
        steps=[
            WorkflowStep(step_id="weather", description="Collect weather", action_id="weather_report", parameters={}),
            WorkflowStep(step_id="reminders", description="Collect reminders", action_id="reminder", parameters={}),
            WorkflowStep(step_id="briefing", description="Generate briefing", action_id="generate_briefing", parameters={}, dependencies={"weather", "reminders"}),
        ],
    )
    valid, errors = engine.validate_workflow(workflow)
    assert valid is True
    assert errors == []
    assert workflow.status == WorkflowState.PLANNED


def test_invalid_dependency_cycle_is_blocked():
    registry = DummyRegistry()
    engine = WorkflowEngine(action_registry=registry, permissions=PermissionManager())
    workflow = Workflow(
        workflow_id="cycle",
        goal="Broken plan",
        steps=[
            WorkflowStep(step_id="a", description="A", action_id="weather_report", parameters={}, dependencies={"b"}),
            WorkflowStep(step_id="b", description="B", action_id="reminder", parameters={}, dependencies={"a"}),
        ],
    )
    valid, errors = engine.validate_workflow(workflow)
    assert valid is False
    assert any("cycle" in error.lower() for error in errors)


def test_permission_pause_and_resume():
    registry = DummyRegistry()
    engine = WorkflowEngine(action_registry=registry, permissions=PermissionManager())
    workflow = engine.create_workflow(
        "Prepare and send my summary",
        steps=[
            WorkflowStep(step_id="collect", description="Collect context", action_id="weather_report", parameters={}),
            WorkflowStep(step_id="summary", description="Generate summary", action_id="generate_briefing", parameters={}, dependencies={"collect"}),
            WorkflowStep(step_id="send", description="Send message", action_id="send_message", parameters={"text": "Hi"}, dependencies={"summary"}),
        ],
    )
    engine.execute_workflow(workflow)
    assert workflow.status in {WorkflowState.PAUSED, WorkflowState.AWAITING_PERMISSION}
    assert workflow.step_by_id("send").status == WorkflowStepState.WAITING_PERMISSION
    engine.resume_workflow(workflow)
    assert workflow.status == WorkflowState.COMPLETED


def test_retry_recovery():
    registry = DummyRegistry()
    engine = WorkflowEngine(action_registry=registry, permissions=PermissionManager(), max_retries=2)
    workflow = Workflow(
        workflow_id="retry-flow",
        goal="Retry once",
        steps=[
            WorkflowStep(step_id="retry", description="Retry action", action_id="retry_action", parameters={"attempt": 0}),
        ],
    )
    engine.execute_workflow(workflow)
    assert workflow.status == WorkflowState.COMPLETED
    assert workflow.step_by_id("retry").retry_count >= 1
    assert workflow.step_by_id("retry").status == WorkflowStepState.SUCCESS


def test_missing_capability_is_blocked():
    engine = WorkflowEngine(action_registry=DummyRegistry(), permissions=PermissionManager())
    workflow = Workflow(
        workflow_id="missing-cap",
        goal="Use a missing action",
        steps=[WorkflowStep(step_id="missing", description="Needs capability", action_id="not_real_action", parameters={})],
    )
    engine.execute_workflow(workflow)
    assert workflow.status == WorkflowState.BLOCKED
    assert workflow.step_by_id("missing").status == WorkflowStepState.BLOCKED


def test_resume_after_permission_does_not_repeat_completed_steps():
    registry = DummyRegistry()
    engine = WorkflowEngine(action_registry=registry, permissions=PermissionManager())
    workflow = engine.create_workflow(
        "Prepare and send summary",
        steps=[
            WorkflowStep(step_id="collect", description="Collect context", action_id="weather_report", parameters={}),
            WorkflowStep(step_id="send", description="Send message", action_id="send_message", parameters={"text": "Hi"}, dependencies={"collect"}),
        ],
    )
    workflow.steps[0].status = WorkflowStepState.SUCCESS
    workflow.steps[0].result = {"status": "ok"}
    workflow.steps[1].status = WorkflowStepState.WAITING_PERMISSION
    workflow.status = WorkflowState.PAUSED
    engine.resume_workflow(workflow)
    assert workflow.step_by_id("collect").status == WorkflowStepState.SUCCESS
    assert workflow.step_by_id("send").status == WorkflowStepState.SUCCESS


def test_learned_workflow_is_reused_when_relevant():
    registry = DummyRegistry()
    memory = LearningMemory(path=None)
    memory.learn(
        name="briefing-workflow",
        goal="Prepare morning briefing",
        steps=[
            {"action": "weather_report", "parameters": {}, "description": "Collect weather"},
            {"action": "generate_briefing", "parameters": {}, "description": "Generate briefing"},
        ],
    )
    engine = WorkflowEngine(action_registry=registry, permissions=PermissionManager(), learning_memory=memory)
    workflow = engine.plan_goal("Prepare morning briefing")
    assert workflow is not None
    assert any(step.action_id == "weather_report" for step in workflow.steps)


def test_duplicate_execution_prevention():
    registry = DummyRegistry()
    engine = WorkflowEngine(action_registry=registry, permissions=PermissionManager())
    workflow = engine.create_workflow(
        "Prepare morning briefing",
        steps=[WorkflowStep(step_id="weather", description="Get weather", action_id="weather_report", parameters={})],
    )
    first = engine.execute_workflow(workflow)
    second = engine.execute_workflow(workflow)
    assert first.status == WorkflowState.COMPLETED
    assert second.status == WorkflowState.COMPLETED
    assert first.workflow_id == second.workflow_id
