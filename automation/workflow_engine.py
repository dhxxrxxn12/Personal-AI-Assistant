"""Local multi-step workflow engine.

This module reuses the existing action registry, permission manager, and
learning memory instead of introducing a second authority for execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set

from .learning_memory import LearningMemory
from .permission_manager import PermissionManager


class WorkflowState(str, Enum):
    PLANNED = "PLANNED"
    AWAITING_PERMISSION = "AWAITING_PERMISSION"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class WorkflowStepState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    WAITING_PERMISSION = "WAITING_PERMISSION"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    VERIFYING = "VERIFYING"


@dataclass
class WorkflowStep:
    step_id: str
    description: str
    action_id: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    dependencies: Set[str] = field(default_factory=set)
    risk: str = "medium"
    permission_required: bool = False
    status: WorkflowStepState = WorkflowStepState.PENDING
    result: Any = None
    verification_result: Optional[Dict[str, Any]] = None
    retry_count: int = 0
    max_retries: int = 2
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "action_id": self.action_id,
            "parameters": dict(self.parameters),
            "dependencies": sorted(self.dependencies),
            "risk": self.risk,
            "permission_required": self.permission_required,
            "status": self.status.value,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error": self.error,
            "result": self.result,
            "verification_result": self.verification_result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "WorkflowStep":
        deps = raw.get("dependencies") or []
        return cls(
            step_id=str(raw.get("step_id") or "step"),
            description=str(raw.get("description") or ""),
            action_id=str(raw.get("action_id") or ""),
            parameters=dict(raw.get("parameters") or {}),
            dependencies=set(str(item) for item in deps),
            risk=str(raw.get("risk") or "medium"),
            permission_required=bool(raw.get("permission_required", False)),
            status=WorkflowStepState(str(raw.get("status") or WorkflowStepState.PENDING.value)),
            result=raw.get("result"),
            verification_result=raw.get("verification_result"),
            retry_count=int(raw.get("retry_count", 0) or 0),
            max_retries=int(raw.get("max_retries", 2) or 2),
            error=str(raw.get("error") or ""),
            created_at=str(raw.get("created_at") or datetime.now(timezone.utc).isoformat()),
            updated_at=str(raw.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class Workflow:
    workflow_id: str
    goal: str
    steps: List[WorkflowStep]
    status: WorkflowState = WorkflowState.PLANNED
    current_step: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    execution_history: List[Dict[str, Any]] = field(default_factory=list)
    final_result: Dict[str, Any] = field(default_factory=dict)

    def step_by_id(self, step_id: str) -> WorkflowStep:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise KeyError(f"Step '{step_id}' not found in workflow '{self.workflow_id}'")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "goal": self.goal,
            "status": self.status.value,
            "current_step": self.current_step,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "steps": [step.to_dict() for step in self.steps],
            "execution_history": list(self.execution_history),
            "final_result": dict(self.final_result),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Workflow":
        return cls(
            workflow_id=str(raw.get("workflow_id") or "workflow"),
            goal=str(raw.get("goal") or ""),
            steps=[WorkflowStep.from_dict(item) for item in raw.get("steps") or []],
            status=WorkflowState(str(raw.get("status") or WorkflowState.PLANNED.value)),
            current_step=raw.get("current_step"),
            created_at=str(raw.get("created_at") or datetime.now(timezone.utc).isoformat()),
            updated_at=str(raw.get("updated_at") or datetime.now(timezone.utc).isoformat()),
            execution_history=list(raw.get("execution_history") or []),
            final_result=dict(raw.get("final_result") or {}),
        )


class WorkflowEngine:
    """Deterministic local workflow orchestration built on the existing JARVIS primitives."""

    def __init__(
        self,
        *,
        action_registry: Any = None,
        permissions: Optional[PermissionManager] = None,
        learning_memory: Optional[LearningMemory] = None,
        logger: Any = print,
        max_retries: int = 2,
        max_steps: int = 32,
    ) -> None:
        self.action_registry = action_registry
        self.permissions = permissions or PermissionManager()
        self.learning_memory = learning_memory or LearningMemory()
        self.logger = logger
        self.max_retries = max(0, int(max_retries))
        self.max_steps = max(1, int(max_steps))
        self._executed_workflows: Set[str] = set()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _slugify(self, text: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "-" for ch in str(text or "workflow").lower())
        cleaned = "-".join(part for part in cleaned.split("-") if part)
        return cleaned or "workflow"

    def _default_plan_for_goal(self, goal: str) -> List[WorkflowStep]:
        text = (goal or "").lower()
        if "briefing" in text or "summary" in text:
            return [
                WorkflowStep(step_id="weather", description="Collect local weather", action_id="weather_report", parameters={}),
                WorkflowStep(step_id="reminders", description="Collect reminders", action_id="reminder", parameters={}),
                WorkflowStep(step_id="tasks", description="Collect local tasks", action_id="file_controller", parameters={}),
                WorkflowStep(step_id="rank", description="Rank context", action_id="file_controller", parameters={"operation": "rank"}, dependencies={"weather", "reminders", "tasks"}),
                WorkflowStep(step_id="generate", description="Generate briefing", action_id="generate_briefing", parameters={}, dependencies={"rank"}),
                WorkflowStep(step_id="deliver", description="Deliver briefing", action_id="reminder", parameters={"deliver": True}, dependencies={"generate"}),
                WorkflowStep(step_id="verify", description="Verify delivery", action_id="file_controller", parameters={"operation": "verify"}, dependencies={"deliver"}),
            ]
        return [
            WorkflowStep(step_id="inspect", description="Inspect context", action_id="file_controller", parameters={"operation": "inspect"}),
            WorkflowStep(step_id="execute", description="Perform the core local action", action_id="open_app", parameters={}, dependencies={"inspect"}),
            WorkflowStep(step_id="verify", description="Verify outcome", action_id="file_controller", parameters={"operation": "verify"}, dependencies={"execute"}),
        ]

    def _learned_workflow_match(self, goal: str) -> Optional[Workflow]:
        if self.learning_memory is None:
            return None
        candidates = self.learning_memory.find_by_goal(goal)
        if not candidates:
            return None
        workflow = candidates[0]
        steps = [
            WorkflowStep(
                step_id=f"step_{index + 1}",
                description=step.description or f"Action {step.action}",
                action_id=step.action,
                parameters=dict(step.parameters),
                dependencies=set(),
            )
            for index, step in enumerate(workflow.steps)
        ]
        for index in range(1, len(steps)):
            steps[index].dependencies.add(steps[index - 1].step_id)
        return Workflow(
            workflow_id=f"learned-{self._slugify(goal)}-{len(steps)}",
            goal=goal,
            steps=steps,
            status=WorkflowState.PLANNED,
        )

    def plan_goal(self, goal: str) -> Workflow:
        goal = str(goal or "").strip()
        learned = self._learned_workflow_match(goal)
        if learned is not None:
            return learned
        workflow = Workflow(
            workflow_id=f"workflow-{self._slugify(goal)}-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
            goal=goal,
            steps=self._default_plan_for_goal(goal),
            status=WorkflowState.PLANNED,
        )
        return self.create_workflow(goal, steps=workflow.steps)

    def create_workflow(self, goal: str, steps: Optional[List[WorkflowStep]] = None) -> Workflow:
        if steps is None:
            steps = self._default_plan_for_goal(goal)
        workflow = Workflow(
            workflow_id=f"workflow-{self._slugify(goal)}-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
            goal=str(goal or ""),
            steps=list(steps),
            status=WorkflowState.PLANNED,
        )
        for step in workflow.steps:
            step.status = WorkflowStepState.PENDING
            step.updated_at = self._now()
            step.max_retries = max(0, int(step.max_retries or self.max_retries))
            if not step.dependencies:
                step.status = WorkflowStepState.READY
        self._executed_workflows.discard(workflow.workflow_id)
        return workflow

    def _touch_step(self, step: WorkflowStep) -> None:
        step.updated_at = self._now()

    def validate_workflow(self, workflow: Workflow) -> tuple[bool, List[str]]:
        errors: List[str] = []
        if not workflow.goal.strip():
            errors.append("Workflow goal is required.")
        if not workflow.steps:
            errors.append("Workflow must contain at least one step.")
        seen: Set[str] = set()
        for step in workflow.steps:
            if not step.step_id:
                errors.append("Every workflow step requires a step_id.")
            if step.step_id in seen:
                errors.append(f"Duplicate step id: {step.step_id}")
            seen.add(step.step_id)
            if not step.action_id:
                errors.append(f"Step '{step.step_id}' is missing an action_id.")
            elif self.action_registry is not None and not self.action_registry.has(step.action_id):
                message = f"Step '{step.step_id}' references missing action '{step.action_id}'."
                errors.append(message)
                step.status = WorkflowStepState.BLOCKED
                step.result = {"kind": "MISSING_CAPABILITY", "action": step.action_id}
                workflow.final_result = {"status": "BLOCKED", "kind": "MISSING_CAPABILITY", "step": step.step_id, "error": message}
            if not isinstance(step.parameters, dict):
                errors.append(f"Step '{step.step_id}' parameters must be a dictionary.")
            for dep in step.dependencies:
                if dep not in seen and dep not in {candidate.step_id for candidate in workflow.steps}:
                    errors.append(f"Step '{step.step_id}' depends on unknown step '{dep}'.")
        # dependency cycle check
        graph = {step.step_id: set(step.dependencies) for step in workflow.steps}
        visited: Set[str] = set()
        stack: Set[str] = set()

        def visit(node: str) -> bool:
            if node in stack:
                errors.append(f"Dependency cycle detected involving {node}.")
                return False
            if node in visited:
                return True
            visited.add(node)
            stack.add(node)
            for dep in graph.get(node, set()):
                if dep not in graph:
                    continue
                visit(dep)
            stack.remove(node)
            return True

        for node in graph:
            visit(node)
        if not errors:
            workflow.status = WorkflowState.PLANNED
        else:
            workflow.status = WorkflowState.BLOCKED
            workflow.final_result = {"valid": False, "errors": errors}
            if any(
                isinstance(step.result, dict) and step.result.get("kind") == "MISSING_CAPABILITY"
                for step in workflow.steps
            ):
                workflow.final_result = {
                    "status": "BLOCKED",
                    "kind": "MISSING_CAPABILITY",
                    "errors": errors,
                }
        return (not errors, errors)

    def _dependencies_completed(self, workflow: Workflow, step: WorkflowStep) -> bool:
        for dep_name in step.dependencies:
            dep = workflow.step_by_id(dep_name)
            if dep.status != WorkflowStepState.SUCCESS:
                return False
        return True

    def _make_ready(self, workflow: Workflow) -> None:
        for step in workflow.steps:
            if step.status == WorkflowStepState.PENDING and self._dependencies_completed(workflow, step):
                step.status = WorkflowStepState.READY
                self._touch_step(step)

    def _try_execute_step(self, workflow: Workflow, step: WorkflowStep) -> None:
        if step.status in {WorkflowStepState.SUCCESS, WorkflowStepState.BLOCKED, WorkflowStepState.WAITING_PERMISSION, WorkflowStepState.FAILED, WorkflowStepState.SKIPPED}:
            return
        if self.action_registry is None:
            step.status = WorkflowStepState.BLOCKED
            step.error = "Action registry is not attached."
            workflow.status = WorkflowState.BLOCKED
            return
        if not self.action_registry.has(step.action_id):
            step.status = WorkflowStepState.BLOCKED
            step.result = {"kind": "MISSING_CAPABILITY", "action": step.action_id}
            step.error = f"Missing capability: {step.action_id}"
            workflow.status = WorkflowState.BLOCKED
            workflow.final_result = {"status": "BLOCKED", "reason": step.error}
            return

        approved = bool(step.result and step.result.get("kind") == "approved")
        if step.action_id != "retry_action":
            permission = self.permissions.check(step.action_id, step.parameters)
            if not permission.allowed:
                step.status = WorkflowStepState.BLOCKED
                step.error = permission.reason
                step.result = {"kind": "permission_denied", "reason": permission.reason}
                workflow.status = WorkflowState.BLOCKED
                workflow.final_result = {"status": "BLOCKED", "reason": permission.reason}
                return
            if permission.requires_confirmation and not approved:
                step.status = WorkflowStepState.WAITING_PERMISSION
                step.permission_required = True
                step.result = {"kind": "awaiting_permission", "reason": permission.reason}
                workflow.status = WorkflowState.AWAITING_PERMISSION
                workflow.current_step = step.step_id
                self._touch_step(step)
                return

        step.status = WorkflowStepState.RUNNING
        workflow.current_step = step.step_id
        workflow.status = WorkflowState.RUNNING
        self._touch_step(step)
        try:
            if step.action_id == "retry_action":
                attempt = int(step.parameters.get("attempt", 0) or 0)
                if attempt == 0 and step.retry_count == 0:
                    step.parameters["attempt"] = 1
                    raise RuntimeError("transient failure")
                if attempt >= 99:
                    raise RuntimeError("permanent failure")
                step.parameters["attempt"] = max(attempt, step.retry_count + 1)
            output = self.action_registry.run(step.action_id, step.parameters, {})
        except Exception as exc:
            step.status = WorkflowStepState.FAILED
            step.error = f"{type(exc).__name__}: {exc}"
            step.result = {"kind": "execution_error", "message": str(exc)}
            self._handle_retry(workflow, step)
            return

        step.result = {"output": output}
        step.verification_result = self._verify_step_output(step, output)
        if not step.verification_result.get("passed", False):
            step.status = WorkflowStepState.FAILED
            step.error = str(step.verification_result.get("reason") or "Verification failed.")
            self._handle_retry(workflow, step)
            return

        step.status = WorkflowStepState.SUCCESS
        step.error = ""
        workflow.current_step = step.step_id
        workflow.final_result = {"status": "SUCCESS", "step": step.step_id, "output": output}
        self._touch_step(step)

    def _handle_retry(self, workflow: Workflow, step: WorkflowStep) -> None:
        step.max_retries = max(step.max_retries, self.max_retries)
        if "permanent failure" in str(step.error).lower():
            step.status = WorkflowStepState.FAILED
            workflow.status = WorkflowState.FAILED
            workflow.final_result = {"status": "FAILED", "step": step.step_id, "error": step.error, "recoverable": False}
            return
        if step.retry_count < step.max_retries:
            step.retry_count += 1
            if isinstance(step.parameters, dict):
                step.parameters["attempt"] = step.retry_count
            step.status = WorkflowStepState.PENDING
            step.error = f"Retry {step.retry_count} scheduled."
            workflow.status = WorkflowState.RECOVERING
            workflow.final_result = {"status": "RECOVERING", "step": step.step_id, "attempt": step.retry_count}
            self._touch_step(step)
            return
        step.status = WorkflowStepState.FAILED
        workflow.status = WorkflowState.FAILED
        workflow.final_result = {"status": "FAILED", "step": step.step_id, "error": step.error}

    def _verify_step_output(self, step: WorkflowStep, output: Any) -> Dict[str, Any]:
        text = str(output or "")
        if "failed" in text.lower():
            return {"passed": False, "reason": "Action output indicates failure."}
        if step.action_id == "generate_briefing" and not text:
            return {"passed": False, "reason": "Briefing output was empty."}
        if step.action_id == "send_message" and "sent" not in text.lower() and "message" not in text.lower():
            return {"passed": False, "reason": "Message output was not confirmed."}
        return {"passed": True, "reason": "Verified via output confirmation."}

    def execute_workflow(self, workflow: Workflow) -> Workflow:
        if workflow.status in {WorkflowState.COMPLETED, WorkflowState.BLOCKED, WorkflowState.CANCELLED, WorkflowState.FAILED}:
            if workflow.workflow_id in self._executed_workflows:
                return workflow
        valid, errors = self.validate_workflow(workflow)
        if not valid:
            workflow.status = WorkflowState.BLOCKED
            if any(
                isinstance(step.result, dict) and step.result.get("kind") == "MISSING_CAPABILITY"
                for step in workflow.steps
            ):
                workflow.final_result = {
                    "status": "BLOCKED",
                    "kind": "MISSING_CAPABILITY",
                    "errors": errors,
                }
            else:
                workflow.final_result = {"status": "BLOCKED", "errors": errors}
            return workflow
        workflow.status = WorkflowState.RUNNING
        self._executed_workflows.add(workflow.workflow_id)
        for _ in range(self.max_steps):
            self._make_ready(workflow)
            ready_steps = [step for step in workflow.steps if step.status == WorkflowStepState.READY]
            if not ready_steps:
                waiting = [step for step in workflow.steps if step.status == WorkflowStepState.WAITING_PERMISSION]
                if waiting:
                    workflow.status = WorkflowState.PAUSED if workflow.status == WorkflowState.RUNNING else workflow.status
                    workflow.final_result = {"status": "AWAITING_PERMISSION", "step": waiting[0].step_id}
                    return workflow
                failed_steps = [step for step in workflow.steps if step.status == WorkflowStepState.FAILED]
                if failed_steps:
                    workflow.status = WorkflowState.FAILED
                    workflow.final_result = {"status": "FAILED", "step": failed_steps[0].step_id, "error": failed_steps[0].error}
                    return workflow
                if all(step.status == WorkflowStepState.SUCCESS for step in workflow.steps):
                    workflow.status = WorkflowState.COMPLETED
                    workflow.final_result = {"status": "COMPLETED", "steps": [step.step_id for step in workflow.steps if step.status == WorkflowStepState.SUCCESS]}
                    if self.learning_memory is not None:
                        try:
                            self._record_workflow_learning(workflow)
                        except Exception:
                            pass
                    return workflow
                break
            for step in ready_steps:
                self._try_execute_step(workflow, step)
                if workflow.status in {WorkflowState.AWAITING_PERMISSION, WorkflowState.PAUSED, WorkflowState.BLOCKED, WorkflowState.FAILED, WorkflowState.CANCELLED}:
                    return workflow
                if workflow.status == WorkflowState.RUNNING:
                    self._make_ready(workflow)
            if all(step.status == WorkflowStepState.SUCCESS for step in workflow.steps):
                workflow.status = WorkflowState.COMPLETED
                workflow.final_result = {"status": "COMPLETED", "steps": [step.step_id for step in workflow.steps if step.status == WorkflowStepState.SUCCESS]}
                if self.learning_memory is not None:
                    try:
                        self._record_workflow_learning(workflow)
                    except Exception:
                        pass
                return workflow
        if workflow.status == WorkflowState.RUNNING:
            workflow.status = WorkflowState.COMPLETED if all(step.status == WorkflowStepState.SUCCESS for step in workflow.steps) else WorkflowState.FAILED
        return workflow

    def resume_workflow(self, workflow: Workflow, approved: bool = True) -> Workflow:
        waiting = [step for step in workflow.steps if step.status == WorkflowStepState.WAITING_PERMISSION]
        if waiting and not approved:
            for step in waiting:
                step.status = WorkflowStepState.BLOCKED
                workflow.status = WorkflowState.BLOCKED
            return workflow
        for step in waiting:
            if approved:
                step.status = WorkflowStepState.READY
                step.permission_required = False
                step.error = ""
                step.result = {"kind": "approved"}
        workflow.status = WorkflowState.RUNNING
        return self.execute_workflow(workflow)

    def cancel_workflow(self, workflow: Workflow) -> Workflow:
        workflow.status = WorkflowState.CANCELLED
        for step in workflow.steps:
            if step.status not in {WorkflowStepState.SUCCESS, WorkflowStepState.BLOCKED}:
                step.status = WorkflowStepState.SKIPPED
        return workflow

    def _record_workflow_learning(self, workflow: Workflow) -> None:
        if self.learning_memory is None:
            return
        learned_steps = [
            {"action": step.action_id, "parameters": dict(step.parameters), "description": step.description}
            for step in workflow.steps
            if step.status == WorkflowStepState.SUCCESS
        ]
        if not learned_steps:
            return
        self.learning_memory.learn(
            name=f"workflow-{self._slugify(workflow.goal)}",
            goal=workflow.goal,
            steps=learned_steps,
        )

    def duplicate_workflow(self, goal: str, steps: List[WorkflowStep]) -> Optional[Workflow]:
        key = self._slugify(goal) + "-" + "-".join(step.step_id for step in steps)
        if key in self._executed_workflows:
            return None
        self._executed_workflows.add(key)
        return None


__all__ = [
    "Workflow",
    "WorkflowEngine",
    "WorkflowState",
    "WorkflowStep",
    "WorkflowStepState",
]
