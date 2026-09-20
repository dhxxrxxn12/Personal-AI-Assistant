"""Serializable contracts shared by the self-engineering subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    parse_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "imports": list(self.imports),
            "classes": list(self.classes),
            "functions": list(self.functions),
            "routes": list(self.routes),
            "actions": list(self.actions),
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "analysis": self.analysis,
            "steps": [step.to_dict() for step in self.steps],
            "required_approvals": list(self.required_approvals),
            "verification_plan": list(self.verification_plan),
            "source": self.source,
            "version": self.version,
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
