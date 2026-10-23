"""Serializable contracts shared by the self-engineering subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class FileInfo:
    path: str
    kind: str
    size: int

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "size": self.size}


@dataclass(frozen=True)
class PythonModuleInfo:
    path: str
    imports: List[str] = field(default_factory=list)
    classes: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    routes: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    tool_name: Optional[str] = None
    tool_description: str = ""
    parse_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "imports": list(self.imports),
            "classes": list(self.classes),
            "functions": list(self.functions),
            "routes": list(self.routes),
            "actions": list(self.actions),
            "tool_name": self.tool_name,
            "tool_description": self.tool_description,
            "parse_error": self.parse_error,
        }


@dataclass
class RepositorySnapshot:
    root: str
    generated_at: str
    tree: List[str] = field(default_factory=list)
    files: List[FileInfo] = field(default_factory=list)
    python_modules: List[PythonModuleInfo] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    configs: List[str] = field(default_factory=list)
    deployment_files: List[str] = field(default_factory=list)
    tracked_files: List[str] = field(default_factory=list)
    git_status: List[str] = field(default_factory=list)
    git_diff_summary: str = ""
    recent_commits: List[str] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "generated_at": self.generated_at,
            "tree": list(self.tree),
            "files": [item.to_dict() for item in self.files],
            "python_modules": [item.to_dict() for item in self.python_modules],
            "tests": list(self.tests),
            "configs": list(self.configs),
            "deployment_files": list(self.deployment_files),
            "tracked_files": list(self.tracked_files),
            "git_status": list(self.git_status),
            "git_diff_summary": self.git_diff_summary,
            "recent_commits": list(self.recent_commits),
            "truncated": self.truncated,
        }


@dataclass
class CapabilityAnalysis:
    goal: str
    current_capabilities: Dict[str, bool]
    missing_capabilities: List[str]
    dependencies: List[str]
    risks: List[str]
    evidence: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "current_capabilities": dict(self.current_capabilities),
            "missing_capabilities": list(self.missing_capabilities),
            "dependencies": list(self.dependencies),
            "risks": list(self.risks),
            "evidence": {key: list(value) for key, value in self.evidence.items()},
        }


@dataclass
class EngineeringStep:
    id: str
    description: str
    operation: str
    files: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    risk: str = "low"
    requires_approval: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "operation": self.operation,
            "files": list(self.files),
            "dependencies": list(self.dependencies),
            "tests": list(self.tests),
            "risk": self.risk,
            "requires_approval": self.requires_approval,
        }


@dataclass
class EngineeringPlan:
    goal: str
    analysis: str
    steps: List[EngineeringStep]
    required_approvals: List[str] = field(default_factory=list)
    verification_plan: List[str] = field(default_factory=list)
    source: str = "deterministic"
    version: int = 1
    assumptions: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)
    proposed_changes: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    risk: str = "low"
    rollback_strategy: str = "Remove the isolated worktree without changing production."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "analysis": self.analysis,
            "steps": [step.to_dict() for step in self.steps],
            "required_approvals": list(self.required_approvals),
            "verification_plan": list(self.verification_plan),
            "source": self.source,
            "version": self.version,
            "assumptions": list(self.assumptions),
            "affected_files": list(self.affected_files),
            "proposed_changes": list(self.proposed_changes),
            "dependencies": list(self.dependencies),
            "tests": list(self.tests),
            "risk": self.risk,
            "rollback_strategy": self.rollback_strategy,
        }


@dataclass
class TraceEvent:
    stage: str
    message: str
    timestamp: str = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "message": self.message,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


class EngineeringTaskStatus(str, Enum):
    """Persisted lifecycle states for one controlled engineering task."""

    CREATED = "created"
    INSPECTING = "inspecting"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    SANDBOX_CREATING = "sandbox_creating"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    REPAIRING = "repairing"
    VERIFYING = "verifying"
    CHECKPOINTED = "checkpointed"
    AWAITING_DEPLOY_APPROVAL = "awaiting_deploy_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"


def _json_value(value: Any) -> Any:
    """Convert the small set of model values to dashboard-safe JSON data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


@dataclass
class EngineeringTask:
    """Durable, reportable state for an end-to-end engineering run.

    The task is deliberately separate from the normal Mac execution queue. It
    records the engineering lifecycle while a worktree protects the
    production checkout from autonomous edits.
    """

    task_id: str
    goal: str
    status: str = EngineeringTaskStatus.CREATED.value
    created_at: str = field(default_factory=utc_now)
    current_phase: str = EngineeringTaskStatus.CREATED.value
    repository_inspection: Optional[Dict[str, Any]] = None
    capability_analysis: Optional[Dict[str, Any]] = None
    plan: Optional[Dict[str, Any]] = None
    affected_files: List[str] = field(default_factory=list)
    proposed_changes: List[str] = field(default_factory=list)
    test_results: List[Dict[str, Any]] = field(default_factory=list)
    repair_attempts: List[Dict[str, Any]] = field(default_factory=list)
    git_checkpoint: Optional[Dict[str, Any]] = None
    sandbox_id: Optional[str] = None
    sandbox_path: Optional[str] = None
    verification_result: Optional[Dict[str, Any]] = None
    risk_level: str = "low"
    required_approval: bool = False
    approval_reason: Optional[str] = None
    production_impact: str = "unchanged"
    final_report: Optional[str] = None
    error: Optional[str] = None
    events: List[TraceEvent] = field(default_factory=list)

    def record(self, phase: str, message: str, **metadata: Any) -> None:
        normalized = str(phase).lower()
        self.current_phase = normalized
        self.events.append(TraceEvent(normalized, message, metadata=metadata))

    def set_status(self, status: str | EngineeringTaskStatus) -> None:
        value = status.value if isinstance(status, EngineeringTaskStatus) else str(status).lower()
        self.status = value
        self.current_phase = value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status,
            "created_at": self.created_at,
            "current_phase": self.current_phase,
            "repository_inspection": _json_value(self.repository_inspection),
            "capability_analysis": _json_value(self.capability_analysis),
            "plan": _json_value(self.plan),
            "affected_files": list(self.affected_files),
            "proposed_changes": list(self.proposed_changes),
            "test_results": _json_value(self.test_results),
            "repair_attempts": _json_value(self.repair_attempts),
            "git_checkpoint": _json_value(self.git_checkpoint),
            "sandbox_id": self.sandbox_id,
            "sandbox_path": self.sandbox_path,
            "verification_result": _json_value(self.verification_result),
            "risk_level": self.risk_level,
            "required_approval": self.required_approval,
            "approval_reason": self.approval_reason,
            "production_impact": self.production_impact,
            "final_report": self.final_report,
            "error": self.error,
            "events": [event.to_dict() for event in self.events],
        }


@dataclass
class EngineeringRun:
    goal: str
    status: str = "created"
    snapshot: Optional[RepositorySnapshot] = None
    analysis: Optional[CapabilityAnalysis] = None
    plan: Optional[EngineeringPlan] = None
    events: List[TraceEvent] = field(default_factory=list)
    result: Dict[str, Any] = field(default_factory=dict)

    def record(self, stage: str, message: str, **metadata: Any) -> None:
        self.events.append(TraceEvent(stage, message, metadata=metadata))
        self.status = stage.lower()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "status": self.status,
            "snapshot": self.snapshot.to_dict() if self.snapshot else None,
            "analysis": self.analysis.to_dict() if self.analysis else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "events": [event.to_dict() for event in self.events],
            "result": dict(self.result),
        }
