from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class GoalStatuses:
    READY = "READY"
    BLOCKED = "BLOCKED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    FAILED = "FAILED"


@dataclass
class Goal:
    goal_id: str
    original_goal: str
    normalized_goal: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "original_goal": self.original_goal,
            "normalized_goal": self.normalized_goal,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Goal":
        return cls(
            goal_id=str(data.get("goal_id") or "goal"),
            original_goal=str(data.get("original_goal") or ""),
            normalized_goal=str(data.get("normalized_goal") or data.get("original_goal") or ""),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Objective:
    objective_id: str
    description: str
    purpose: str = ""
    dependencies: List[str] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    risk: str = "READ_ONLY"
    priority: str = "MEDIUM"
    status: str = "PENDING"
    execution_goal: Optional[str] = None
    input_data: Dict[str, Any] = field(default_factory=dict)
    confidence: str = "MEDIUM"
    missing_capability: bool = False
    blocked_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "description": self.description,
            "purpose": self.purpose,
            "dependencies": list(self.dependencies),
            "required_capabilities": list(self.required_capabilities),
            "risk": self.risk,
            "priority": self.priority,
            "status": self.status,
            "execution_goal": self.execution_goal,
            "input_data": dict(self.input_data),
            "confidence": self.confidence,
            "missing_capability": self.missing_capability,
            "blocked_reason": self.blocked_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Objective":
        return cls(
            objective_id=str(data.get("objective_id") or "objective"),
            description=str(data.get("description") or ""),
            purpose=str(data.get("purpose") or ""),
            dependencies=[str(item) for item in list(data.get("dependencies") or [])],
            required_capabilities=[str(item) for item in list(data.get("required_capabilities") or [])],
            risk=str(data.get("risk") or "READ_ONLY"),
            priority=str(data.get("priority") or "MEDIUM"),
            status=str(data.get("status") or "PENDING"),
            execution_goal=data.get("execution_goal"),
            input_data=dict(data.get("input_data") or {}),
            confidence=str(data.get("confidence") or "MEDIUM"),
            missing_capability=bool(data.get("missing_capability", False)),
            blocked_reason=data.get("blocked_reason"),
        )


DecompositionPlan = None


@dataclass
class GoalPlan:
    plan_id: str
    goal: Goal
    objectives: List[Objective]
    dependency_graph: Dict[str, List[str]] = field(default_factory=dict)
    execution_order: List[str] = field(default_factory=list)
    status: str = GoalStatuses.READY
    confidence: str = "MEDIUM"
    unresolved_items: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    executed: bool = False
    final_result: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal.to_dict(),
            "objectives": [obj.to_dict() for obj in self.objectives],
            "dependency_graph": {key: list(value) for key, value in self.dependency_graph.items()},
            "execution_order": list(self.execution_order),
            "status": self.status,
            "confidence": self.confidence,
            "unresolved_items": list(self.unresolved_items),
            "created_at": self.created_at,
            "executed": self.executed,
            "final_result": dict(self.final_result),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoalPlan":
        goal_data = data.get("goal") or {}
        return cls(
            plan_id=str(data.get("plan_id") or "plan"),
            goal=Goal.from_dict(goal_data),
            objectives=[Objective.from_dict(item) for item in data.get("objectives") or []],
            dependency_graph={
                str(key): [str(item) for item in value]
                for key, value in dict(data.get("dependency_graph") or {}).items()
            },
            execution_order=[str(item) for item in list(data.get("execution_order") or [])],
            status=str(data.get("status") or GoalStatuses.READY),
            confidence=str(data.get("confidence") or "MEDIUM"),
            unresolved_items=[str(item) for item in list(data.get("unresolved_items") or [])],
            created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
            executed=bool(data.get("executed", False)),
            final_result=dict(data.get("final_result") or {}),
        )

    def validate(self) -> tuple[bool, List[str]]:
        errors: List[str] = []
        if not self.goal.original_goal.strip():
            errors.append("Goal is required.")
        if not self.objectives:
            errors.append("At least one objective is required.")
        seen = set()
        for objective in self.objectives:
            if not objective.objective_id:
                errors.append("Every objective requires an objective_id.")
            if objective.objective_id in seen:
                errors.append(f"Duplicate objective id: {objective.objective_id}")
            seen.add(objective.objective_id)
            if not objective.description:
                errors.append(f"Objective '{objective.objective_id}' requires a description.")
            for dep in objective.dependencies:
                if dep not in seen and dep not in {obj.objective_id for obj in self.objectives}:
                    errors.append(f"Objective '{objective.objective_id}' depends on unknown objective '{dep}'.")
        graph = {obj.objective_id: set(obj.dependencies) for obj in self.objectives}
        visited: set[str] = set()
        stack: set[str] = set()

        def visit(node: str) -> None:
            if node in stack:
                errors.append(f"Dependency cycle detected involving '{node}'.")
                return
            if node in visited:
                return
            visited.add(node)
            stack.add(node)
            for dep in graph.get(node, set()):
                if dep in graph:
                    visit(dep)
            stack.remove(node)

        for node in graph:
            visit(node)
        return (not errors, errors)


DecompositionPlan = GoalPlan
