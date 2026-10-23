from __future__ import annotations

from datetime import datetime, timezone

from automation.learning_memory import LearningMemory
from automation.permission_manager import PermissionManager
from automation.proactive_decision_engine import DecisionState, ProactiveDecisionEngine
from automation.workflow_engine import Workflow, WorkflowEngine, WorkflowState, WorkflowStep, WorkflowStepState


class DummyRegistry:
    def __init__(self, *, weather_ok=True, transient_failures=None):
        self.calls = []
        self.weather_ok = weather_ok
        self.transient_failures = set(transient_failures or [])

    def has(self, action: str) -> bool:
        return action in {
            "weather_report",
            "reminder",
            "file_controller",
            "generate_briefing",
            "send_message",
            "retry_action",
            "open_app",
            "delete_file",
        }

    def run(self, name: str, parameters: dict | None = None, ctx: dict | None = None):
        self.calls.append((name, parameters or {}, ctx or {}))
        if name == "weather_report":
            if not self.weather_ok:
                return "Weather unavailable"
            return "Sunny 72F"
        if name == "reminder":
            return "Reminder loaded"
        if name == "file_controller":
            return "Task file updated"
        if name == "generate_briefing":
            return "Briefing generated"
        if name == "send_message":
            return "Message sent"
        if name == "open_app":
            return "Calculator opened"
        if name == "retry_action":
            attempt = int(parameters.get("attempt", 0))
            if attempt in self.transient_failures:
                raise RuntimeError("transient failure")
            return "recovered"
        if name == "delete_file":
            return "Deleted"
        raise RuntimeError(f"Unknown action: {name}")


def _morning_workflow():
    return [
        WorkflowStep(step_id="collect_context", description="Collect local context", action_id="reminder", parameters={}),
        WorkflowStep(step_id="collect_tasks", description="Collect tasks", action_id="file_controller", parameters={}, dependencies={"collect_context"}),
        WorkflowStep(step_id="gather_weather", description="Check weather", action_id="weather_report", parameters={}, dependencies={"collect_tasks"}),
        WorkflowStep(step_id="briefing", description="Generate briefing", action_id="generate_briefing", parameters={}, dependencies={"gather_weather"}),
        WorkflowStep(step_id="verify", description="Verify briefing delivery", action_id="file_controller", parameters={"operation": "verify"}, dependencies={"briefing"}),
    ]


def _test_permissions():
    permissions = PermissionManager()
    permissions.allow_automatically("file_controller")
    return permissions


def test_complete_morning_workflow_completes():
    registry = DummyRegistry(weather_ok=True)
    engine = WorkflowEngine(action_registry=registry, permissions=_test_permissions())
    workflow = engine.create_workflow("Prepare my morning.", steps=_morning_workflow())
    result = engine.execute_workflow(workflow)

    assert result.status == WorkflowState.COMPLETED
    assert all(step.status == WorkflowStepState.SUCCESS for step in workflow.steps)
    assert workflow.final_result["status"] == "COMPLETED"


def test_provider_failure_keeps_workflow_going():
    registry = DummyRegistry(weather_ok=False)
    engine = WorkflowEngine(action_registry=registry, permissions=_test_permissions())
    workflow = engine.create_workflow("Prepare my morning.", steps=_morning_workflow())
    result = engine.execute_workflow(workflow)

    assert result.status == WorkflowState.COMPLETED
    assert workflow.step_by_id("gather_weather").result["output"] == "Weather unavailable"
    assert workflow.step_by_id("briefing").status == WorkflowStepState.SUCCESS


def test_permission_pause_and_resume_workflow():
    registry = DummyRegistry(weather_ok=True)
    permissions = _test_permissions()
    engine = WorkflowEngine(action_registry=registry, permissions=permissions)
    workflow = engine.create_workflow(
        "Prepare and send my morning summary.",
        steps=[
            WorkflowStep(step_id="collect", description="Collect context", action_id="reminder", parameters={}),
            WorkflowStep(step_id="summary", description="Generate summary", action_id="generate_briefing", parameters={}, dependencies={"collect"}),
            WorkflowStep(step_id="prepare", description="Prepare message", action_id="file_controller", parameters={}, dependencies={"summary"}),
            WorkflowStep(step_id="send", description="Send message", action_id="send_message", parameters={"text": "Morning summary"}, dependencies={"prepare"}),
        ],
    )

    result = engine.execute_workflow(workflow)

    assert result.status in {WorkflowState.PAUSED, WorkflowState.AWAITING_PERMISSION}
    assert workflow.step_by_id("send").status == WorkflowStepState.WAITING_PERMISSION

    resumed = engine.resume_workflow(workflow, approved=True)
    assert resumed.status == WorkflowState.COMPLETED
    assert workflow.step_by_id("collect").status == WorkflowStepState.SUCCESS
    assert workflow.step_by_id("send").status == WorkflowStepState.SUCCESS


def test_transient_failure_retries_then_completes():
    registry = DummyRegistry(transient_failures={1})
    engine = WorkflowEngine(action_registry=registry, permissions=_test_permissions(), max_retries=2)
    workflow = Workflow(
        workflow_id="transient-flow",
        goal="Retry once",
        steps=[
            WorkflowStep(step_id="step1", description="Success", action_id="file_controller", parameters={}),
            WorkflowStep(step_id="step2", description="Retry action", action_id="retry_action", parameters={"attempt": 0}, dependencies={"step1"}),
            WorkflowStep(step_id="step3", description="Depends on recovery", action_id="generate_briefing", parameters={}, dependencies={"step2"}),
        ],
    )

    result = engine.execute_workflow(workflow)

    assert result.status == WorkflowState.COMPLETED
    assert workflow.step_by_id("step1").status == WorkflowStepState.SUCCESS
    assert workflow.step_by_id("step2").status == WorkflowStepState.SUCCESS
    assert workflow.step_by_id("step2").retry_count >= 1
    assert workflow.step_by_id("step3").status == WorkflowStepState.SUCCESS


def test_permanent_failure_stops_safe_workflow():
    registry = DummyRegistry()
    engine = WorkflowEngine(action_registry=registry, permissions=_test_permissions(), max_retries=1)
    workflow = Workflow(
        workflow_id="permanent-failure",
        goal="Permanent failure path",
        steps=[
            WorkflowStep(step_id="success", description="Succeed", action_id="reminder", parameters={}),
            WorkflowStep(step_id="fail", description="Permanent failure", action_id="retry_action", parameters={"attempt": 99}, dependencies={"success"}),
            WorkflowStep(step_id="depends", description="Should not run", action_id="generate_briefing", parameters={}, dependencies={"fail"}),
        ],
    )

    result = engine.execute_workflow(workflow)

    assert result.status == WorkflowState.FAILED
    assert workflow.step_by_id("fail").status == WorkflowStepState.FAILED
    assert workflow.step_by_id("depends").status != WorkflowStepState.SUCCESS


def test_restart_resume_waiting_permission_does_not_repeat_completed_steps():
    registry = DummyRegistry(weather_ok=True)
    engine = WorkflowEngine(action_registry=registry, permissions=_test_permissions())
    workflow = Workflow(
        workflow_id="restart-flow",
        goal="Restart and resume",
        steps=[
            WorkflowStep(step_id="step1", description="Collect context", action_id="reminder", parameters={}),
            WorkflowStep(step_id="step2", description="Collect tasks", action_id="file_controller", parameters={}, dependencies={"step1"}),
            WorkflowStep(step_id="step3", description="Send summary", action_id="send_message", parameters={"text": "Final update"}, dependencies={"step2"}),
        ],
    )
    workflow.step_by_id("step1").status = WorkflowStepState.SUCCESS
    workflow.step_by_id("step2").status = WorkflowStepState.SUCCESS
    workflow.step_by_id("step3").status = WorkflowStepState.WAITING_PERMISSION
    workflow.status = WorkflowState.PAUSED

    resumed = engine.resume_workflow(workflow, approved=True)

    assert resumed.status == WorkflowState.COMPLETED
    assert workflow.step_by_id("step1").status == WorkflowStepState.SUCCESS
    assert workflow.step_by_id("step2").status == WorkflowStepState.SUCCESS
    assert workflow.step_by_id("step3").status == WorkflowStepState.SUCCESS


def test_learned_workflow_reuse_is_guarded_and_validated():
    memory = LearningMemory(path=None)
    memory.learn(
        name="morning-workflow",
        goal="Prepare my morning.",
        steps=[
            {"action": "reminder", "description": "Collect context", "parameters": {}},
            {"action": "generate_briefing", "description": "Generate briefing", "parameters": {}},
        ],
    )
    engine = WorkflowEngine(action_registry=DummyRegistry(), permissions=_test_permissions(), learning_memory=memory)
    planned = engine.plan_goal("Prepare my morning.")

    assert planned.goal == "Prepare my morning."
    assert any(step.action_id == "reminder" for step in planned.steps)
    assert any(step.action_id == "generate_briefing" for step in planned.steps)


def test_missing_capability_stays_blocked_and_routes_to_capability_gap():
    engine = WorkflowEngine(action_registry=DummyRegistry(), permissions=PermissionManager())
    workflow = Workflow(
        workflow_id="missing-cap",
        goal="Requires a missing action",
        steps=[WorkflowStep(step_id="missing", description="Missing capability", action_id="not_real_action", parameters={})],
    )

    result = engine.execute_workflow(workflow)

    assert result.status == WorkflowState.BLOCKED
    assert workflow.step_by_id("missing").status == WorkflowStepState.BLOCKED
    assert "MISSING_CAPABILITY" in str(result.final_result).upper()


def test_dangerous_action_requires_permission_and_cannot_send_without_approval():
    engine = ProactiveDecisionEngine(action_registry=DummyRegistry(), permissions=PermissionManager())
    decision = engine.decide({"items": [{"source": "system", "title": "Send message to another person", "content": "Send message to another person", "category": "system", "priority": "high"}]})
    assert decision.state == DecisionState.ASK_PERMISSION
    assert decision.requires_user_approval is True
    assert engine.execute_decision(decision).status == "AWAITING_PERMISSION"


def test_proactive_loop_moves_from_context_to_workflow_to_learning():
    registry = DummyRegistry(weather_ok=True)
    permissions = _test_permissions()
    decision_engine = ProactiveDecisionEngine(action_registry=registry, permissions=permissions, learning_memory=LearningMemory(path=None))
    decisions = decision_engine.decide({"items": [{"source": "system", "title": "Open Calculator", "content": "Open Calculator", "category": "system", "priority": "medium"}]})

    assert decisions.state == DecisionState.EXECUTE

    workflow = WorkflowEngine(action_registry=registry, permissions=permissions, learning_memory=decision_engine.learning_memory)
    generated = workflow.create_workflow("Prepare my morning.", steps=_morning_workflow())
    finished = workflow.execute_workflow(generated)

    assert finished.status == WorkflowState.COMPLETED
    assert workflow.learning_memory.names()
