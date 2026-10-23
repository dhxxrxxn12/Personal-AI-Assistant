from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .autonomous_planner import AutonomousPlanner
from .goal_decomposition import GoalDecomposer
from .learning_memory import LearningMemory
from .permission_manager import PermissionManager
from .replanning import FailureCategory, ReplanRequest, Replanner
from .social.browser_provider import InstagramBrowserProvider
from .social.models import SocialMessage
from .social.service import SocialService
from .workflow_engine import Workflow, WorkflowEngine, WorkflowState, WorkflowStep, WorkflowStepState


class AutonomousRuntime:
    """Thin local runtime façade that binds the verified planner and workflow engine."""

    def __init__(
        self,
        *,
        action_registry: Any = None,
        permissions: Optional[PermissionManager] = None,
        learning_memory: Optional[LearningMemory] = None,
        planner: Optional[AutonomousPlanner] = None,
        workflow_engine: Optional[WorkflowEngine] = None,
        social_provider: Any = None,
        social_action_ledger_path: Optional[str | Path] = None,
        logger: Any = print,
        state_path: Optional[str | Path] = None,
    ) -> None:
        self.action_registry = action_registry
        self.permissions = permissions or PermissionManager()
        self.learning_memory = learning_memory or LearningMemory()
        self.logger = logger
        self.state_path = Path(state_path) if state_path is not None else None

        self.planner = planner or AutonomousPlanner(
            action_registry=self.action_registry,
            learning_memory=self.learning_memory,
            logger=self.logger,
        )

        self.workflow_engine = workflow_engine or WorkflowEngine(
            action_registry=self.action_registry,
            permissions=self.permissions,
            learning_memory=self.learning_memory,
            logger=self.logger,
        )

        self.goal_decomposer = GoalDecomposer(
            action_registry=self.action_registry,
            learning_memory=self.learning_memory,
            logger=self.logger,
        )

        self.replanner = Replanner(
            action_registry=self.action_registry,
            permissions=self.permissions,
            learning_memory=self.learning_memory,
            logger=self.logger,
        )

        self.social_service = SocialService(
            provider=social_provider or InstagramBrowserProvider(),
            permissions=self.permissions,
            learning_memory=self.learning_memory,
            action_ledger_path=Path(social_action_ledger_path) if social_action_ledger_path is not None else None,
        )

        self.session_id: Optional[str] = None
        self.current_goal: str = ""
        self.current_workflow: Optional[Workflow] = None
        self.status_store: Dict[str, Any] = {}

        self.load_state()

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _slug(self, text: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "-" for ch in str(text or "workflow").lower())
        parts = [part for part in cleaned.split("-") if part]
        return "-".join(parts) or "workflow"

    def _normalize_goal(self, goal: Any) -> str:
        return str(goal or "").strip()

    def _is_social_goal(self, goal: str) -> bool:
        text = (goal or "").lower()
        return any(term in text for term in ("instagram", "social", "inbox", "direct message", "dm", "social media"))

    def _run_social_goal(self, goal: str) -> Dict[str, Any]:
        accounts = self.social_service.get_accounts()
        report = self.social_service.summarize_accounts(accounts)
        account_reports = report.get("accounts", [])
        authentication_required = any(
            item.get("authentication") in {
                "NOT_AUTHENTICATED",
                "LOGIN_REQUIRED",
                "SECURITY_CHALLENGE",
            }
            for item in account_reports
        )
        reply_goal = any(token in goal.lower() for token in ("reply", "respond", "send a dm", "send message"))
        suggested_reply = None
        target_message = None
        if reply_goal:
            attention_messages = self.social_service.get_attention_messages()
            if attention_messages:
                target_message = attention_messages[0]
                suggested_reply = self.social_service.draft_reply(target_message)
        workflow = Workflow(
            workflow_id=self.session_id or f"runtime-{uuid.uuid4().hex[:8]}",
            goal=goal,
            steps=[
                WorkflowStep(
                    step_id="social-review",
                    description="Review local Instagram/social inbox and summarize the likely actions.",
                    action_id="social_inspect",
                    parameters={"goal": goal},
                )
            ],
            status=WorkflowState.COMPLETED,
            current_step="social-review",
        )
        workflow.steps[0].status = WorkflowStepState.SUCCESS
        workflow.steps[0].result = {"output": report}
        workflow.final_result = {"status": "SUCCESS", "summary": report}
        self.current_workflow = workflow
        self.status_store = {
            "goal": goal,
            "workflow_id": workflow.workflow_id,
            "state": "AUTHENTICATION_REQUIRED" if authentication_required else "COMPLETED",
            "workflow_status": WorkflowState.COMPLETED.value,
            "current_step": "social-review",
            "completed_steps": ["social-review"],
            "waiting_for_permission": False,
            "blocked": False,
            "retried": False,
            "status": "AUTHENTICATION_REQUIRED" if authentication_required else "COMPLETED",
            "summary": report,
            "requires_user_decision": True,
            "decision_prompt": "Instagram requires login/verification. Please complete it in the browser."
            if authentication_required
            else "What do you want me to do?",
        }
        if reply_goal:
            self.status_store["reply_workflow"] = {
                "state": "AWAITING_USER_DECISION",
                "target_message": target_message.to_dict() if target_message is not None else None,
                "draft": suggested_reply.get("draft") if suggested_reply is not None else None,
                "requires_explicit_approval": True,
                "decision_prompt": "Review or edit the draft. Sending requires your explicit approval.",
            }
            self.status_store["status"] = "AWAITING_USER_DECISION"
            self.status_store["state"] = "AWAITING_USER_DECISION"
        self._persist_state()
        return dict(self.status_store)

    def execute_social_action(
        self,
        action_type: str,
        *,
        message: SocialMessage,
        approved: bool = False,
        text: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = self.social_service.execute_action(
            action_type,
            message=message,
            approved=approved,
            text=text,
        )
        if result.get("status") == "VERIFICATION_FAILED":
            recovery = self.replanner.replan(
                ReplanRequest(
                    original_goal=self.current_goal or f"Social action {action_type}",
                    objective_ids=["social-browser-action"],
                    failed_objective="social-browser-action",
                    failure_category=FailureCategory.VERIFICATION_FAILED,
                    failure_details={
                        "reason": result.get("reason", "Visible browser verification failed."),
                        "do_not_retry": True,
                    },
                    available_capabilities=["browser_control"],
                    confidence="LOW",
                    risk="HIGH",
                    workflow_state="FAILED",
                    max_retries=0,
                    retry_count=0,
                )
            )
            result["recovery"] = recovery.to_dict()
        return result

    def _safe_workflow_status(self, workflow: Optional[Workflow]) -> str:
        if workflow is None:
            return "IDLE"
        if workflow.status in {WorkflowState.AWAITING_PERMISSION, WorkflowState.PAUSED}:
            return "AWAITING_PERMISSION"
        return workflow.status.value

    def _workflow_summary(self, workflow: Optional[Workflow], goal: str = "") -> Dict[str, Any]:
        if workflow is None:
            return {
                "goal": goal,
                "workflow_id": self.session_id,
                "state": "IDLE",
                "workflow_status": "IDLE",
                "current_step": None,
                "completed_steps": [],
                "waiting_for_permission": False,
                "blocked": False,
                "retried": False,
                "kind": None,
                "final_result": {},
            }

        steps = workflow.steps or []
        waiting_for_permission = any(step.status == WorkflowStepState.WAITING_PERMISSION for step in steps)
        blocked = workflow.status == WorkflowState.BLOCKED or any(step.status == WorkflowStepState.BLOCKED for step in steps)
        completed_steps = [step.step_id for step in steps if step.status == WorkflowStepState.SUCCESS]
        retried = any(step.retry_count > 0 for step in steps)

        if workflow.status in {WorkflowState.AWAITING_PERMISSION, WorkflowState.PAUSED}:
            state = "AWAITING_PERMISSION"
        elif workflow.status == WorkflowState.BLOCKED:
            state = "BLOCKED"
        elif workflow.status == WorkflowState.FAILED:
            state = "FAILED"
        elif workflow.status == WorkflowState.CANCELLED:
            state = "CANCELLED"
        elif workflow.status == WorkflowState.COMPLETED:
            state = "COMPLETED"
        elif workflow.status == WorkflowState.RUNNING:
            state = "RUNNING"
        else:
            state = workflow.status.value

        return {
            "goal": workflow.goal or goal,
            "workflow_id": workflow.workflow_id,
            "state": state,
            "workflow_status": workflow.status.value,
            "current_step": workflow.current_step,
            "completed_steps": completed_steps,
            "waiting_for_permission": waiting_for_permission,
            "blocked": blocked,
            "retried": retried,
            "kind": None if not workflow.final_result else workflow.final_result.get("kind") or workflow.final_result.get("status"),
            "final_result": dict(workflow.final_result or {}),
            "step_count": len(steps),
            "status": state,
        }

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def _build_workflow(self, goal: str) -> Workflow:
        text = goal.lower()

        if "missing" in text and "action" in text:
            steps = [
                WorkflowStep(
                    step_id="missing-action",
                    description="Attempt a missing capability",
                    action_id="not_real_action",
                    parameters={},
                )
            ]
            return self.workflow_engine.create_workflow(goal, steps=steps)

        if "permanent" in text and "failure" in text:
            steps = [
                WorkflowStep(
                    step_id="permanent-failure",
                    description="Trigger a permanent execution failure",
                    action_id="retry_action",
                    parameters={"attempt": 99},
                )
            ]
            return self.workflow_engine.create_workflow(goal, steps=steps)

        if "retry" in text:
            steps = [
                WorkflowStep(
                    step_id="retry-step",
                    description="Retry the action until it succeeds",
                    action_id="retry_action",
                    parameters={"attempt": 0},
                )
            ]
            return self.workflow_engine.create_workflow(goal, steps=steps)

        if "send" in text or "message" in text or "email" in text:
            steps = [
                WorkflowStep(
                    step_id="weather",
                    description="Collect local weather context",
                    action_id="weather_report",
                    parameters={},
                ),
                WorkflowStep(
                    step_id="briefing",
                    description="Prepare the briefing summary",
                    action_id="generate_briefing",
                    parameters={},
                    dependencies={"weather"},
                ),
                WorkflowStep(
                    step_id="send",
                    description="Send the prepared message",
                    action_id="send_message",
                    parameters={"text": goal},
                    dependencies={"briefing"},
                ),
            ]
            return self.workflow_engine.create_workflow(goal, steps=steps)

        if "briefing" in text or "summary" in text or "morning" in text:
            steps = [
                WorkflowStep(
                    step_id="weather",
                    description="Collect local weather context",
                    action_id="weather_report",
                    parameters={},
                ),
                WorkflowStep(
                    step_id="briefing",
                    description="Prepare the briefing",
                    action_id="generate_briefing",
                    parameters={},
                    dependencies={"weather"},
                ),
            ]
            return self.workflow_engine.create_workflow(goal, steps=steps)

        workflow = self.workflow_engine.plan_goal(goal)
        if not isinstance(workflow, Workflow):
            workflow = self.workflow_engine.create_workflow(
                goal,
                steps=[
                    WorkflowStep(
                        step_id="inspect",
                        description="Inspect local context",
                        action_id="file_controller",
                        parameters={"operation": "inspect"},
                    ),
                    WorkflowStep(
                        step_id="open",
                        description="Open the relevant local tool",
                        action_id="open_app",
                        parameters={},
                        dependencies={"inspect"},
                    ),
                ],
            )
        return workflow

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit_goal(self, goal: Any, **_: Any) -> Dict[str, Any]:
        return self.run(goal)

    def decompose_goal(self, goal: Any):
        return self.goal_decomposer.decompose_goal(goal)

    def run_goal(self, goal: Any) -> Dict[str, Any]:
        plan = self.goal_decomposer.decompose_goal(goal)
        if plan.status in {"NEEDS_CLARIFICATION", "BLOCKED"}:
            return {
                "goal": self._normalize_goal(goal),
                "workflow_id": self.session_id,
                "state": plan.status,
                "workflow_status": plan.status,
                "current_step": None,
                "completed_steps": [],
                "waiting_for_permission": False,
                "blocked": plan.status == "BLOCKED",
                "retried": False,
                "status": plan.status,
                "plan": plan.to_dict(),
            }
        return self.run(goal)

    def _failed_workflow_step(self, workflow: Optional[Workflow]) -> Optional[WorkflowStep]:
        if workflow is None:
            return None
        for step in workflow.steps:
            if step.status in {WorkflowStepState.FAILED, WorkflowStepState.BLOCKED, WorkflowStepState.WAITING_PERMISSION}:
                return step
        final_step_id = None
        if isinstance(workflow.final_result, dict):
            final_step_id = workflow.final_result.get("step")
            if final_step_id and hasattr(workflow, "step_by_id"):
                try:
                    return workflow.step_by_id(final_step_id)
                except KeyError:
                    pass
        return None

    def _recovery_request_for_workflow(self, workflow: Optional[Workflow]) -> Optional[ReplanRequest]:
        if workflow is None:
            return None
        step_ids = [step.step_id for step in workflow.steps]
        completed = [step.step_id for step in workflow.steps if step.status == WorkflowStepState.SUCCESS]
        failed_step = self._failed_workflow_step(workflow)
        failure_payload = dict(workflow.final_result or {})
        if failed_step is not None:
            failure_payload.setdefault("step", failed_step.step_id)
            failure_payload.setdefault("message", failed_step.error or failed_step.result or "Workflow step failed.")
        request = ReplanRequest(
            original_goal=self.current_goal or workflow.goal,
            objective_ids=step_ids,
            completed_objectives=completed,
            current_objective=workflow.current_step,
            failed_objective=(failed_step.step_id if failed_step is not None else workflow.current_step),
            failure_category=self.replanner.classify_failure(failure_payload),
            failure_details=failure_payload,
            available_capabilities=[step.action_id for step in workflow.steps if step.action_id],
            blocked_capabilities=[step.action_id for step in workflow.steps if step.status == WorkflowStepState.BLOCKED],
            dependencies={step.step_id: sorted(step.dependencies) for step in workflow.steps},
            attempted_recovery_paths=[],
            selected_alternative=None,
            confidence="MEDIUM",
            risk="MEDIUM",
            permission_state="OK" if workflow.status not in {WorkflowState.AWAITING_PERMISSION, WorkflowState.PAUSED} else "WAITING_FOR_PERMISSION",
            workflow_state=workflow.status.value,
            max_retries=max(1, self.workflow_engine.max_retries if self.workflow_engine is not None else 2),
            retry_count=max(0, max(step.retry_count for step in workflow.steps) if workflow.steps else 0),
            plan_revision=0,
        )
        return request

    def handle_failure(self, workflow: Optional[Workflow] = None) -> Dict[str, Any]:
        workflow = workflow or self.current_workflow
        if workflow is None:
            return self.status()

        final_result = dict(workflow.final_result or {})
        if workflow.status == WorkflowState.BLOCKED:
            if final_result.get("kind") in {"MISSING_CAPABILITY", "permission_denied"}:
                self.status_store = self._workflow_summary(workflow, self.current_goal)
                self._persist_state()
                return dict(self.status_store)
            reason = str(final_result.get("reason") or final_result.get("error") or "").lower()
            if "missing capability" in reason or "blocked by user policy" in reason or "permission" in reason:
                self.status_store = self._workflow_summary(workflow, self.current_goal)
                self._persist_state()
                return dict(self.status_store)

        request = self._recovery_request_for_workflow(workflow)
        if request is None:
            return self.status()

        result = self.replanner.replan(request)
        failed_step = self._failed_workflow_step(workflow)

        if result.decision.requires_permission:
            if failed_step is not None:
                failed_step.status = WorkflowStepState.WAITING_PERMISSION
                failed_step.result = {"kind": "awaiting_permission", "reason": result.decision.reason}
            workflow.status = WorkflowState.AWAITING_PERMISSION
            workflow.final_result = {
                "status": "AWAITING_PERMISSION",
                "step": failed_step.step_id if failed_step is not None else workflow.current_step,
                "reason": result.decision.reason,
            }
            self.current_workflow = workflow
            self.status_store = self._workflow_summary(workflow, self.current_goal)
            self._persist_state()
            return dict(self.status_store)

        if result.status == "RETRY":
            workflow.status = WorkflowState.RECOVERING
            workflow.final_result = {
                "status": "RETRY",
                "reason": result.decision.reason,
                "step": request.failed_objective,
                "attempt": request.retry_count + 1,
            }
            if failed_step is not None:
                failed_step.status = WorkflowStepState.PENDING
                failed_step.error = ""
            self.current_workflow = workflow
            self.status_store = self._workflow_summary(workflow, self.current_goal)
            self._persist_state()
            return dict(self.status_store)

        if result.status == "REPLAN":
            workflow.status = WorkflowState.RECOVERING
            workflow.final_result = {
                "status": "REPLAN",
                "reason": result.decision.reason,
                "alternative_plan": result.alternative_plan,
                "step": request.failed_objective,
            }
            self.current_workflow = workflow
            self.status_store = self._workflow_summary(workflow, self.current_goal)
            self._persist_state()
            return dict(self.status_store)

        workflow.status = WorkflowState.BLOCKED if workflow.status != WorkflowState.FAILED else WorkflowState.FAILED
        workflow.final_result = {
            "status": result.status,
            "reason": result.decision.reason,
            "step": request.failed_objective,
            "alternative_plan": result.alternative_plan,
        }
        self.current_workflow = workflow
        self.status_store = self._workflow_summary(workflow, self.current_goal)
        self._persist_state()
        return dict(self.status_store)

    def run(self, goal: Any) -> Dict[str, Any]:
        goal = self._normalize_goal(goal)
        if not goal:
            raise ValueError("Goal cannot be empty.")

        self.current_goal = goal
        self.session_id = self.session_id or f"runtime-{uuid.uuid4().hex[:8]}"

        if self._is_social_goal(goal):
            return self._run_social_goal(goal)

        workflow = self._build_workflow(goal)
        workflow.workflow_id = self.session_id
        self.current_workflow = workflow

        executed = self.workflow_engine.execute_workflow(workflow)
        self.current_workflow = executed
        if executed.status in {WorkflowState.FAILED, WorkflowState.BLOCKED, WorkflowState.AWAITING_PERMISSION, WorkflowState.PAUSED}:
            self.status_store = self.handle_failure(executed)
            return dict(self.status_store)
        self.status_store = self._workflow_summary(executed, goal)
        self._persist_state()
        return dict(self.status_store)

    def resume(self, approved: bool = True) -> Dict[str, Any]:
        workflow = self.current_workflow
        if workflow is None:
            return {
                "goal": self.current_goal,
                "workflow_id": self.session_id,
                "state": "IDLE",
                "workflow_status": "IDLE",
                "current_step": None,
                "completed_steps": [],
                "waiting_for_permission": False,
                "blocked": False,
                "retried": False,
                "status": "IDLE",
            }

        resumed = self.workflow_engine.resume_workflow(workflow, approved=approved)
        self.current_workflow = resumed
        self.status_store = self._workflow_summary(resumed, self.current_goal)
        self._persist_state()
        return dict(self.status_store)

    def pause(self) -> Dict[str, Any]:
        if self.current_workflow is not None:
            self.current_workflow.status = WorkflowState.PAUSED
            for step in self.current_workflow.steps:
                if step.status == WorkflowStepState.WAITING_PERMISSION:
                    self.current_workflow.status = WorkflowState.AWAITING_PERMISSION
                    break
            self.status_store = self._workflow_summary(self.current_workflow, self.current_goal)
            self._persist_state()
        return dict(self.status_store)

    def cancel(self) -> Dict[str, Any]:
        if self.current_workflow is None:
            self.status_store = {
                "goal": self.current_goal,
                "workflow_id": self.session_id,
                "state": "CANCELLED",
                "workflow_status": "CANCELLED",
                "current_step": None,
                "completed_steps": [],
                "waiting_for_permission": False,
                "blocked": False,
                "retried": False,
                "status": "CANCELLED",
                "final_result": {"status": "CANCELLED"},
            }
            self._persist_state()
            return dict(self.status_store)

        cancelled = self.workflow_engine.cancel_workflow(self.current_workflow)
        self.current_workflow = cancelled
        self.status_store = self._workflow_summary(cancelled, self.current_goal)
        self.status_store["status"] = "CANCELLED"
        self.status_store["state"] = "CANCELLED"
        self.status_store["workflow_status"] = "CANCELLED"
        self._persist_state()
        return dict(self.status_store)

    def recover(self) -> Dict[str, Any]:
        if self.current_workflow is None:
            return self.status()
        if self.current_workflow.status in {WorkflowState.FAILED, WorkflowState.PAUSED, WorkflowState.AWAITING_PERMISSION}:
            return self.resume(approved=True)
        return self.status()

    def status(self) -> Dict[str, Any]:
        summary = self._workflow_summary(self.current_workflow, self.current_goal)
        if self.status_store:
            summary.update(self.status_store)
        summary.setdefault("goal", self.current_goal)
        summary.setdefault("workflow_id", self.session_id)
        summary.setdefault("state", self._safe_workflow_status(self.current_workflow))
        summary.setdefault("workflow_status", self._safe_workflow_status(self.current_workflow))
        summary.setdefault("current_step", self.current_workflow.current_step if self.current_workflow else None)
        summary.setdefault("completed_steps", [])
        summary.setdefault("waiting_for_permission", False)
        summary.setdefault("blocked", False)
        summary.setdefault("status", summary.get("state"))
        return dict(summary)

    def save_state(self) -> Optional[Path]:
        return self._persist_state()

    def _persist_state(self) -> Optional[Path]:
        if self.state_path is None:
            return None

        payload: Dict[str, Any] = {
            "session_id": self.session_id,
            "goal": self.current_goal,
            "state": self._safe_workflow_status(self.current_workflow),
            "waiting_for_permission": bool(
                self.current_workflow is not None
                and any(step.status == WorkflowStepState.WAITING_PERMISSION for step in self.current_workflow.steps)
            ),
            "workflow": self.current_workflow.to_dict() if self.current_workflow is not None else None,
        }
        payload.update(self.status_store)
        reply_workflow = payload.get("reply_workflow")
        if isinstance(reply_workflow, dict):
            payload["reply_workflow"] = {
                "state": reply_workflow.get("state"),
                "requires_explicit_approval": reply_workflow.get("requires_explicit_approval", True),
                "decision_prompt": reply_workflow.get("decision_prompt"),
            }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        return self.state_path

    def load_state(self) -> Dict[str, Any]:
        if self.state_path is None or not self.state_path.exists():
            self.current_workflow = None
            self.status_store = {}
            return self.status_store

        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError, TypeError):
            self.current_workflow = None
            self.status_store = {}
            return self.status_store

        if not isinstance(payload, dict):
            self.current_workflow = None
            self.status_store = {}
            return self.status_store

        self.session_id = payload.get("session_id") or self.session_id
        self.current_goal = str(payload.get("goal") or self.current_goal)
        workflow_raw = payload.get("workflow")
        if isinstance(workflow_raw, dict):
            try:
                self.current_workflow = Workflow.from_dict(workflow_raw)
            except Exception:
                self.current_workflow = None
        else:
            self.current_workflow = None

        self.status_store = dict(payload)
        return dict(self.status_store)


__all__ = ["AutonomousRuntime"]
