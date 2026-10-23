import json

from automation.autonomous_runtime import AutonomousRuntime
from automation.learning_memory import LearningMemory
from automation.permission_manager import PermissionManager
from automation.replanning import FailureCategory, ReplanRequest, Replanner
from automation.workflow_engine import Workflow, WorkflowStep, WorkflowStepState


class DummyRegistry:
    def __init__(self):
        self.actions = {"open_app", "weather_report", "file_controller", "generate_briefing", "send_message"}

    def has(self, action: str) -> bool:
        return action in self.actions


def test_transient_failure_retries_successfully():
    replanner = Replanner(action_registry=DummyRegistry())
    request = ReplanRequest(
        original_goal="Open Calculator and prepare summary",
        objective_ids=["open_calculator", "collect_context", "generate_summary"],
        completed_objectives=["open_calculator"],
        current_objective="collect_context",
        failed_objective="collect_context",
        failure_category=FailureCategory.TRANSIENT,
        failure_details={"message": "transient timeout"},
        retry_count=0,
        max_retries=2,
    )
    result = replanner.replan(request)
    assert result.status == "RETRY"
    assert not result.executed


def test_replan_limit_stops_safely():
    replanner = Replanner(action_registry=DummyRegistry(), max_replans=1)
    request = ReplanRequest(
        original_goal="Open Calculator and prepare summary",
        objective_ids=["open_calculator", "collect_context", "generate_summary"],
        completed_objectives=["open_calculator"],
        current_objective="collect_context",
        failed_objective="collect_context",
        failure_category=FailureCategory.TRANSIENT,
        failure_details={"message": "transient timeout"},
        retry_count=2,
        max_retries=2,
        plan_revision=2,
    )
    result = replanner.replan(request)
    assert result.status == "STOP_SAFELY"


def test_permission_failure_pauses():
    replanner = Replanner(action_registry=DummyRegistry())
    request = ReplanRequest(
        original_goal="Send a message",
        objective_ids=["prepare_message", "send_message"],
        completed_objectives=["prepare_message"],
        current_objective="send_message",
        failed_objective="send_message",
        failure_category=FailureCategory.PERMISSION_REQUIRED,
        failure_details={"message": "user confirmation required"},
        retry_count=0,
        max_retries=1,
    )
    result = replanner.replan(request)
    assert result.status == "PAUSED"
    assert result.decision.requires_permission is True


def test_missing_capability_stops_safely():
    replanner = Replanner(action_registry=DummyRegistry())
    request = ReplanRequest(
        original_goal="Use a missing capability",
        objective_ids=["missing_step"],
        failed_objective="missing_step",
        failure_category=FailureCategory.CAPABILITY_MISSING,
        failure_details={"message": "missing capability: unsupported_action"},
    )
    result = replanner.replan(request)
    assert result.status == "STOP_SAFELY"


def test_replanner_does_not_execute_actions_during_planning():
    replanner = Replanner(action_registry=DummyRegistry())
    request = ReplanRequest(
        original_goal="Prepare a system summary",
        objective_ids=["collect_context", "generate_summary"],
        completed_objectives=["collect_context"],
        current_objective="generate_summary",
        failed_objective="generate_summary",
        failure_category=FailureCategory.VERIFICATION_FAILED,
        failure_details={"message": "verification failed"},
    )
    result = replanner.replan(request)
    assert result.executed is False
    assert result.status in {"REPLAN", "STOP_SAFELY"}


def test_dependency_blocked_replan_uses_remaining_objectives():
    replanner = Replanner(action_registry=DummyRegistry())
    request = ReplanRequest(
        original_goal="Handle the summary workflow",
        objective_ids=["a", "b", "c"],
        completed_objectives=["a"],
        current_objective="b",
        failed_objective="b",
        failure_category=FailureCategory.DEPENDENCY_BLOCKED,
        failure_details={"message": "dependency blocked"},
        dependencies={"b": ["a"], "c": ["b"]},
    )
    result = replanner.replan(request)
    assert "b" in result.alternative_plan or "c" in result.alternative_plan


def test_runtime_recovery_request_preserves_completed_steps():
    runtime = AutonomousRuntime(
        action_registry=DummyRegistry(),
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
    )
    workflow = Workflow(
        workflow_id="m9-runtime-check",
        goal="Open calculator and produce summary",
        steps=[
            WorkflowStep(step_id="step1", description="done", action_id="open_app", parameters={}),
            WorkflowStep(step_id="step2", description="failed", action_id="generate_briefing", parameters={}),
        ],
    )
    workflow.steps[0].status = WorkflowStepState.SUCCESS
    workflow.steps[1].status = WorkflowStepState.FAILED
    workflow.current_step = "step2"
    workflow.final_result = {"status": "FAILED", "step": "step2", "message": "verification failed"}
    runtime.current_goal = workflow.goal
    runtime.current_workflow = workflow

    request = runtime._recovery_request_for_workflow(workflow)
    assert request.completed_objectives == ["step1"]
    assert request.failed_objective == "step2"


def test_recovery_learning_redacts_secrets():
    memory = LearningMemory(path=None)
    replanner = Replanner(learning_memory=memory)
    request = ReplanRequest(
        original_goal="Send a safe message",
        objective_ids=["draft", "send"],
        completed_objectives=["draft"],
        current_objective="send",
        failed_objective="send",
        failure_category=FailureCategory.PERMISSION_REQUIRED,
        failure_details={"message": "user confirmation required", "api_key": "super-secret", "token": "abc123"},
    )
    result = replanner.replan(request)
    assert result.status == "PAUSED"
    recovery_records = [
        payload for name, payload in memory.workflows.items() if isinstance(name, str) and name.startswith("recovery-")
    ]
    assert recovery_records, "expected a recovery learning record to be saved"
    serialized = json.dumps(recovery_records[0], sort_keys=True)
    assert "super-secret" not in serialized
    assert "abc123" not in serialized
    assert "[REDACTED]" in serialized
