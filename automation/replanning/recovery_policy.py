from __future__ import annotations

from .models import FailureCategory, ReplanDecision, ReplanRequest


class RecoveryPolicy:
    """Deterministic local policy for adapting to execution failures."""

    @staticmethod
    def decide(request: ReplanRequest) -> ReplanDecision:
        category = request.failure_category
        if isinstance(category, str):
            try:
                category = FailureCategory(category)
            except ValueError:
                category = FailureCategory.UNKNOWN

        if category == FailureCategory.TRANSIENT:
            if request.retry_count < request.max_retries:
                return ReplanDecision(
                    decision="RETRY",
                    reason="Transient failure is retryable under the existing local retry policy.",
                    safe_to_continue=True,
                    requires_permission=False,
                    alternative_objectives=list(request.objective_ids),
                    confidence=request.confidence,
                    risk=request.risk,
                )
            return ReplanDecision(
                decision="REPLAN",
                reason="Transient failure exceeded the retry budget; alternative plan generation is safer.",
                safe_to_continue=True,
                requires_permission=False,
                alternative_objectives=[obj for obj in request.objective_ids if obj not in set(request.completed_objectives)],
                confidence=request.confidence,
                risk=request.risk,
            )

        if category == FailureCategory.PERMISSION_REQUIRED:
            return ReplanDecision(
                decision="REQUEST_PERMISSION",
                reason="The failing objective requires a permission decision before execution can continue safely.",
                safe_to_continue=False,
                requires_permission=True,
                alternative_objectives=[obj for obj in request.objective_ids if obj not in set(request.completed_objectives)],
                confidence=request.confidence,
                risk=request.risk,
            )

        if category == FailureCategory.CAPABILITY_MISSING:
            return ReplanDecision(
                decision="STOP_SAFELY",
                reason="The current objective requires a missing capability and no automatic capability creation is allowed in M9.",
                safe_to_continue=False,
                requires_permission=False,
                alternative_objectives=[],
                confidence=request.confidence,
                risk=request.risk,
            )

        if category in {FailureCategory.DEPENDENCY_BLOCKED, FailureCategory.PLAN_INVALID, FailureCategory.INVALID_STATE}:
            return ReplanDecision(
                decision="REPLAN",
                reason="The remaining objective graph is invalid or blocked by dependency state; re-evaluate the remaining path.",
                safe_to_continue=True,
                requires_permission=False,
                alternative_objectives=[obj for obj in request.objective_ids if obj not in set(request.completed_objectives)],
                confidence=request.confidence,
                risk=request.risk,
            )

        if category == FailureCategory.VERIFICATION_FAILED:
            return ReplanDecision(
                decision="REPLAN",
                reason="The previous execution did not verify cleanly; inspect the current state and attempt a safe alternative path.",
                safe_to_continue=True,
                requires_permission=False,
                alternative_objectives=[obj for obj in request.objective_ids if obj not in set(request.completed_objectives)],
                confidence=request.confidence,
                risk=request.risk,
            )

        if category == FailureCategory.PERMANENT_FAILURE:
            return ReplanDecision(
                decision="STOP_SAFELY",
                reason="The failure is permanent and no safe alternative path is allowed without violating the local safety model.",
                safe_to_continue=False,
                requires_permission=False,
                alternative_objectives=[],
                confidence=request.confidence,
                risk=request.risk,
            )

        if category == FailureCategory.UNKNOWN:
            return ReplanDecision(
                decision="STOP_SAFELY",
                reason="The failure category is unknown and the safe action is to stop rather than invent an unsafe recovery path.",
                safe_to_continue=False,
                requires_permission=False,
                alternative_objectives=[],
                confidence=request.confidence,
                risk=request.risk,
            )

        return ReplanDecision(
            decision="STOP_SAFELY",
            reason="No safe local recovery policy is available for the discovered failure category.",
            safe_to_continue=False,
            requires_permission=False,
            alternative_objectives=[],
            confidence=request.confidence,
            risk=request.risk,
        )
