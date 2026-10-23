from __future__ import annotations

from typing import Any

from .models import FailureCategory


class FailureClassifier:
    """Deterministic classification for local execution failure states."""

    @staticmethod
    def classify(failure: Any) -> FailureCategory:
        if failure is None:
            return FailureCategory.UNKNOWN
        if isinstance(failure, FailureCategory):
            return failure
        if isinstance(failure, Exception):
            text = str(failure)
        elif isinstance(failure, dict):
            text = " ".join(str(value) for value in failure.values())
        else:
            text = str(failure)

        cleaned = text.lower()
        if any(token in cleaned for token in ["permission", "awaiting_permission", "requires_confirmation", "user confirmation", "denied by user policy"]):
            return FailureCategory.PERMISSION_REQUIRED
        if any(token in cleaned for token in ["missing capability", "missing_capability", "unknown action", "capability missing", "not_real_action"]):
            return FailureCategory.CAPABILITY_MISSING
        if any(token in cleaned for token in ["transient", "timeout", "temporar", "retry", "connection reset"]):
            return FailureCategory.TRANSIENT
        if any(token in cleaned for token in ["verification", "not confirmed", "verify", "invalid output"]):
            return FailureCategory.VERIFICATION_FAILED
        if any(token in cleaned for token in ["cycle", "dependency", "unknown objective", "invalid state", "state mismatch"]):
            return FailureCategory.DEPENDENCY_BLOCKED if "dependency" in cleaned or "cycle" in cleaned else FailureCategory.INVALID_STATE
        if any(token in cleaned for token in ["permanent failure", "permanent", "fatal", "non-recoverable"]):
            return FailureCategory.PERMANENT_FAILURE
        if any(token in cleaned for token in ["blocked", "plan invalid", "invalid plan"]):
            return FailureCategory.PLAN_INVALID
        if any(token in cleaned for token in ["failed", "error", "exception"]):
            return FailureCategory.ACTION_FAILED
        return FailureCategory.UNKNOWN
