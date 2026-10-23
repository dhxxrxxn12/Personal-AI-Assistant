from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class FailureCategory(str, Enum):
    TRANSIENT = "TRANSIENT"
    PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    CAPABILITY_MISSING = "CAPABILITY_MISSING"
    INVALID_STATE = "INVALID_STATE"
    DEPENDENCY_BLOCKED = "DEPENDENCY_BLOCKED"
    ACTION_FAILED = "ACTION_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    PLAN_INVALID = "PLAN_INVALID"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    UNKNOWN = "UNKNOWN"


@dataclass
class ReplanRequest:
    original_goal: str
    objective_ids: List[str]
    completed_objectives: List[str] = field(default_factory=list)
    current_objective: Optional[str] = None
    failed_objective: Optional[str] = None
    failure_category: FailureCategory | str = FailureCategory.UNKNOWN
    failure_details: Dict[str, Any] = field(default_factory=dict)
    available_capabilities: List[str] = field(default_factory=list)
    blocked_capabilities: List[str] = field(default_factory=list)
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    attempted_recovery_paths: List[str] = field(default_factory=list)
    selected_alternative: Optional[str] = None
    confidence: str = "MEDIUM"
    risk: str = "MEDIUM"
    permission_state: str = "OK"
    workflow_state: str = "FAILED"
    max_retries: int = 2
    retry_count: int = 0
    plan_revision: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_goal": self.original_goal,
            "objective_ids": list(self.objective_ids),
            "completed_objectives": list(self.completed_objectives),
            "current_objective": self.current_objective,
            "failed_objective": self.failed_objective,
            "failure_category": self.failure_category.value if isinstance(self.failure_category, FailureCategory) else str(self.failure_category),
            "failure_details": dict(self.failure_details),
            "available_capabilities": list(self.available_capabilities),
            "blocked_capabilities": list(self.blocked_capabilities),
            "dependencies": {key: list(value) for key, value in self.dependencies.items()},
            "attempted_recovery_paths": list(self.attempted_recovery_paths),
            "selected_alternative": self.selected_alternative,
            "confidence": self.confidence,
            "risk": self.risk,
            "permission_state": self.permission_state,
            "workflow_state": self.workflow_state,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
            "plan_revision": self.plan_revision,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReplanRequest":
        category = data.get("failure_category")
        if isinstance(category, FailureCategory):
            failure_category = category
        else:
            try:
                failure_category = FailureCategory(str(category or FailureCategory.UNKNOWN.value))
            except ValueError:
                failure_category = FailureCategory.UNKNOWN
        return cls(
            original_goal=str(data.get("original_goal") or ""),
            objective_ids=[str(item) for item in data.get("objective_ids") or []],
            completed_objectives=[str(item) for item in data.get("completed_objectives") or []],
            current_objective=data.get("current_objective"),
            failed_objective=data.get("failed_objective"),
            failure_category=failure_category,
            failure_details=dict(data.get("failure_details") or {}),
            available_capabilities=[str(item) for item in data.get("available_capabilities") or []],
            blocked_capabilities=[str(item) for item in data.get("blocked_capabilities") or []],
            dependencies={str(key): [str(item) for item in value] for key, value in dict(data.get("dependencies") or {}).items()},
            attempted_recovery_paths=[str(item) for item in data.get("attempted_recovery_paths") or []],
            selected_alternative=data.get("selected_alternative"),
            confidence=str(data.get("confidence") or "MEDIUM"),
            risk=str(data.get("risk") or "MEDIUM"),
            permission_state=str(data.get("permission_state") or "OK"),
            workflow_state=str(data.get("workflow_state") or "FAILED"),
            max_retries=int(data.get("max_retries") or 2),
            retry_count=int(data.get("retry_count") or 0),
            plan_revision=int(data.get("plan_revision") or 0),
        )


@dataclass
class ReplanDecision:
    decision: str
    reason: str
    safe_to_continue: bool
    requires_permission: bool = False
    alternative_objectives: List[str] = field(default_factory=list)
    confidence: str = "MEDIUM"
    risk: str = "MEDIUM"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "safe_to_continue": self.safe_to_continue,
            "requires_permission": self.requires_permission,
            "alternative_objectives": list(self.alternative_objectives),
            "confidence": self.confidence,
            "risk": self.risk,
        }


@dataclass
class RecoveryAttempt:
    objective_id: str
    strategy: str
    status: str
    details: Dict[str, Any] = field(default_factory=dict)
    attempt_number: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "strategy": self.strategy,
            "status": self.status,
            "details": dict(self.details),
            "attempt_number": self.attempt_number,
        }


@dataclass
class ReplanResult:
    status: str
    decision: ReplanDecision
    alternative_plan: List[str] = field(default_factory=list)
    remaining_objectives: List[str] = field(default_factory=list)
    completed_objectives: List[str] = field(default_factory=list)
    recovery_attempts: List[RecoveryAttempt] = field(default_factory=list)
    executed: bool = False
    final_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "decision": self.decision.to_dict(),
            "alternative_plan": list(self.alternative_plan),
            "remaining_objectives": list(self.remaining_objectives),
            "completed_objectives": list(self.completed_objectives),
            "recovery_attempts": [item.to_dict() for item in self.recovery_attempts],
            "executed": self.executed,
            "final_reason": self.final_reason,
        }
