from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .failure_classifier import FailureClassifier
from .models import FailureCategory, RecoveryAttempt, ReplanDecision, ReplanRequest, ReplanResult
from .recovery_policy import RecoveryPolicy


_SECRET_KEY_HINTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "header",
)


class Replanner:
    """Local, side-effect-free replanning layer for objective graphs."""

    def __init__(self, *, action_registry: Any = None, permissions: Any = None, learning_memory: Any = None, logger: Any = print, max_replans: int = 3):
        self.action_registry = action_registry
        self.permissions = permissions
        self.learning_memory = learning_memory
        self.logger = logger
        self.max_replans = max(1, int(max_replans))

    def classify_failure(self, failure: Any) -> FailureCategory:
        return FailureClassifier.classify(failure)

    def _sanitize_metadata(self, data: Dict[str, Any]) -> Dict[str, Any]:
        sanitized: Dict[str, Any] = {}
        for key, value in dict(data or {}).items():
            key_text = str(key).lower()
            if any(hint in key_text for hint in _SECRET_KEY_HINTS):
                sanitized[str(key)] = "[REDACTED]"
            elif isinstance(value, dict):
                sanitized[str(key)] = self._sanitize_metadata(value)
            elif isinstance(value, (list, tuple)):
                sanitized[str(key)] = [self._sanitize_metadata(item) if isinstance(item, dict) else item for item in value]
            else:
                sanitized[str(key)] = value
        return sanitized

    def _record_recovery_learning(self, request: ReplanRequest, decision: ReplanDecision) -> None:
        if self.learning_memory is None:
            return
        if decision.decision in {"STOP_SAFELY"}:
            return
        safe_metadata = {
            "failure_category": request.failure_category.value if isinstance(request.failure_category, FailureCategory) else str(request.failure_category),
            "strategy": decision.decision,
            "safe_to_continue": decision.safe_to_continue,
            "requires_permission": decision.requires_permission,
            "completed_objectives": list(request.completed_objectives),
            "remaining_objectives": [obj for obj in request.objective_ids if obj not in request.completed_objectives],
            "failure_details": self._sanitize_metadata(dict(request.failure_details or {})),
            "risk": decision.risk,
        }
        self.learning_memory.learn(
            name=f"recovery-{request.failed_objective or request.current_objective or 'workflow'}",
            goal=request.original_goal,
            steps=[{
                "action": request.failed_objective or request.current_objective or "recovery",
                "parameters": self._sanitize_metadata(safe_metadata),
                "description": decision.reason,
            }],
        )

    def _remaining_objectives(self, request: ReplanRequest) -> List[str]:
        completed = set(request.completed_objectives)
        return [obj for obj in request.objective_ids if obj not in completed]

    def _build_alternative_plan(self, request: ReplanRequest, decision: ReplanDecision) -> List[str]:
        remaining = self._remaining_objectives(request)
        if decision.decision == "RETRY":
            return list(request.objective_ids)
        if decision.alternative_objectives:
            return list(decision.alternative_objectives)
        if request.failed_objective:
            alt = [obj for obj in remaining if obj != request.failed_objective]
            return alt + ([request.failed_objective] if request.failed_objective in request.objective_ids else [])
        return remaining

    def replan(self, request: ReplanRequest) -> ReplanResult:
        if not isinstance(request, ReplanRequest):
            request = ReplanRequest.from_dict(dict(request))

        request.failure_category = self.classify_failure(request.failure_details or request.failure_category)
        decision = RecoveryPolicy.decide(request)

        if request.plan_revision >= self.max_replans and decision.decision in {"REPLAN", "RETRY"}:
            decision = ReplanDecision(
                decision="STOP_SAFELY",
                reason="The replanning loop limit was reached; stopping rather than looping indefinitely.",
                safe_to_continue=False,
                requires_permission=False,
                alternative_objectives=[],
                confidence=request.confidence,
                risk=request.risk,
            )

        alternative_plan = self._build_alternative_plan(request, decision)
        attempts: List[RecoveryAttempt] = []
        if decision.decision == "RETRY":
            attempts.append(
                RecoveryAttempt(
                    objective_id=request.failed_objective or request.current_objective or "unknown",
                    strategy="retry_existing_step",
                    status="PLANNED",
                    details={"reason": decision.reason},
                    attempt_number=request.retry_count + 1,
                )
            )
        elif decision.decision == "REPLAN":
            attempts.append(
                RecoveryAttempt(
                    objective_id=request.failed_objective or request.current_objective or "unknown",
                    strategy="replan_remaining_objectives",
                    status="PLANNED",
                    details={"remaining_objectives": alternative_plan, "reason": decision.reason},
                    attempt_number=request.plan_revision + 1,
                )
            )
        elif decision.decision == "REQUEST_PERMISSION":
            attempts.append(
                RecoveryAttempt(
                    objective_id=request.failed_objective or request.current_objective or "unknown",
                    strategy="pause_for_permission",
                    status="AWAITING_PERMISSION",
                    details={"reason": decision.reason},
                    attempt_number=request.retry_count + 1,
                )
            )

        remaining = [obj for obj in request.objective_ids if obj not in request.completed_objectives]
        result = ReplanResult(
            status="RETRY" if decision.decision == "RETRY" else "PAUSED" if decision.decision == "REQUEST_PERMISSION" else "REPLAN" if decision.decision == "REPLAN" else "STOP_SAFELY",
            decision=decision,
            alternative_plan=alternative_plan,
            remaining_objectives=remaining,
            completed_objectives=list(request.completed_objectives),
            recovery_attempts=attempts,
            executed=False,
            final_reason=decision.reason,
        )

        self._record_recovery_learning(request, decision)
        self.logger(f"[Replanner] decision={decision.decision} status={result.status}")
        return result

    def replan_from_failure(self, goal: str, *, failure: Any, completed_objectives: Iterable[str], objective_ids: Iterable[str], current_objective: Optional[str] = None, failed_objective: Optional[str] = None, dependencies: Optional[Dict[str, List[str]]] = None, permission_state: str = "OK", workflow_state: str = "FAILED", retry_count: int = 0, max_retries: int = 2, plan_revision: int = 0) -> ReplanResult:
        request = ReplanRequest(
            original_goal=goal,
            objective_ids=list(objective_ids),
            completed_objectives=list(completed_objectives),
            current_objective=current_objective,
            failed_objective=failed_objective,
            failure_category=self.classify_failure(failure),
            failure_details={"raw": failure} if not isinstance(failure, dict) else dict(failure),
            available_capabilities=[],
            blocked_capabilities=[],
            dependencies=dict(dependencies or {}),
            attempted_recovery_paths=[],
            confidence="MEDIUM",
            risk="MEDIUM",
            permission_state=permission_state,
            workflow_state=workflow_state,
            max_retries=max_retries,
            retry_count=retry_count,
            plan_revision=plan_revision,
        )
        return self.replan(request)


__all__ = ["Replanner"]
