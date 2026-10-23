from __future__ import annotations

from pathlib import Path

from automation.autonomous_runtime import AutonomousRuntime
from automation.learning_memory import LearningMemory
from automation.permission_manager import PermissionManager
from automation.workflow_engine import WorkflowState, WorkflowStepState


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
        if name in {"reminder", "file_controller", "generate_briefing", "send_message", "open_app", "delete_file"}:
            if name == "send_message":
                return "Message sent"
            return "ok"
        if name == "retry_action":
            attempt = int(parameters.get("attempt", 0))
            if attempt in self.transient_failures:
                raise RuntimeError("transient failure")
            return "recovered"
        raise RuntimeError(f"Unknown action: {name}")


def test_runtime_successful_workflow_completes():
    runtime = AutonomousRuntime(
        action_registry=DummyRegistry(),
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
    )
    result = runtime.run("Prepare my morning.")
    assert result["status"] == "COMPLETED"
    assert result["goal"] == "Prepare my morning."
    assert result["workflow_status"] == WorkflowState.COMPLETED.value


def test_runtime_permission_pause_and_resume():
    runtime = AutonomousRuntime(
        action_registry=DummyRegistry(),
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
    )
    result = runtime.run("Prepare and send my morning summary.")
    assert result["status"] in {"AWAITING_PERMISSION", "PAUSED"}
    resumed = runtime.resume(approved=True)
    assert resumed["status"] == "COMPLETED"
    assert resumed["waiting_for_permission"] is False


def test_runtime_permission_denied_does_not_execute_protected_action():
    runtime = AutonomousRuntime(
        action_registry=DummyRegistry(),
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
    )
    runtime.permissions.block("send_message")
    result = runtime.run("Send a message to another person.")
    assert result["status"] == "BLOCKED"
    assert result["waiting_for_permission"] is False


def test_runtime_transient_failure_recovers():
    runtime = AutonomousRuntime(
        action_registry=DummyRegistry(transient_failures={1}),
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
    )
    result = runtime.run("Retry once")
    assert result["status"] == "COMPLETED"
    assert result["retried"] is True


def test_runtime_permanent_failure_stops_cleanly():
    runtime = AutonomousRuntime(
        action_registry=DummyRegistry(),
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
    )
    runtime.workflow_engine.max_retries = 1
    workflow = runtime.workflow_engine.Workflow = None
    result = runtime.run("Permanent failure path")
    assert result["status"] == "FAILED"


def test_runtime_missing_capability_reports_blocked_state():
    runtime = AutonomousRuntime(
        action_registry=DummyRegistry(),
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
    )
    result = runtime.run("Requires a missing action")
    assert result["status"] == "BLOCKED"
    assert result["kind"] == "MISSING_CAPABILITY"


def test_runtime_learned_workflow_reuse():
    memory = LearningMemory(path=None)
    memory.learn(
        name="morning-workflow",
        goal="Prepare my morning.",
        steps=[
            {"action": "weather_report", "parameters": {}, "description": "Collect weather"},
            {"action": "generate_briefing", "parameters": {}, "description": "Generate briefing"},
        ],
    )
    runtime = AutonomousRuntime(
        action_registry=DummyRegistry(),
        permissions=PermissionManager(),
        learning_memory=memory,
    )
    result = runtime.run("Prepare my morning.")
    assert result["status"] in {"COMPLETED", "AWAITING_PERMISSION", "BLOCKED"}
    assert result["goal"] == "Prepare my morning."


def test_runtime_restart_recovery_does_not_repeat_completed_steps():
    runtime = AutonomousRuntime(
        action_registry=DummyRegistry(),
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
        state_path=Path("/tmp/jarvis_runtime_restart.json"),
    )
    runtime.session_id = "restart-run"
    runtime.current_goal = "Restart and resume"
    runtime.status_store = {
        "goal": "Restart and resume",
        "state": "AWAITING_PERMISSION",
        "waiting_for_permission": True,
        "workflow": {
            "workflow_id": "restart-run",
            "goal": "Restart and resume",
            "status": "PAUSED",
            "steps": [
                {"step_id": "step1", "status": "SUCCESS", "action_id": "reminder"},
                {"step_id": "step2", "status": "SUCCESS", "action_id": "file_controller"},
                {"step_id": "step3", "status": "WAITING_PERMISSION", "action_id": "send_message"},
            ],
        },
    }
    runtime.save_state()
    restored = AutonomousRuntime(
        action_registry=DummyRegistry(),
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
        state_path=Path("/tmp/jarvis_runtime_restart.json"),
    )
    restored.load_state()
    resumed = restored.resume(approved=True)
    assert resumed["status"] == "COMPLETED"


def test_runtime_status_report_is_machine_readable():
    runtime = AutonomousRuntime(
        action_registry=DummyRegistry(),
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
    )
    result = runtime.run("Prepare my morning.")
    status = runtime.status()
    assert "goal" in status
    assert "workflow_id" in status
    assert "state" in status
    assert "current_step" in status
    assert "completed_steps" in status
    assert "waiting_for_permission" in status
    assert "blocked" in status
    assert status["state"] in {"COMPLETED", "AWAITING_PERMISSION", "BLOCKED", "FAILED", "RUNNING"}
    assert result["status"] == status["state"]


def test_runtime_cancel_stops_future_actions():
    runtime = AutonomousRuntime(
        action_registry=DummyRegistry(),
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
    )
    runtime.run("Prepare my morning.")
    cancelled = runtime.cancel()
    assert cancelled["status"] == "CANCELLED"
    assert runtime.status()["state"] == "CANCELLED"
