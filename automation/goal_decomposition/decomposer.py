from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import Goal, GoalPlan, GoalStatuses, Objective


class GoalDecomposer:
    """Deterministic natural-language goal decomposition for the local runtime."""

    def __init__(self, action_registry: Any = None, learning_memory: Any = None, logger: Any = print):
        self.action_registry = action_registry
        self.learning_memory = learning_memory
        self.logger = logger

    @staticmethod
    def normalize_goal(goal: Any) -> str:
        text = str(goal or "").strip()
        text = re.sub(r"\s+", " ", text)
        return text

    def _match_keyword(self, goal: str, *keywords: str) -> bool:
        lowered = goal.lower()
        return any(keyword.lower() in lowered for keyword in keywords)

    def _capability_for_goal(self, goal: str) -> List[str]:
        lowered = goal.lower()
        if "calculator" in lowered:
            return ["open_app"]
        if "weather" in lowered:
            return ["weather_report"]
        if "brief" in lowered or "summary" in lowered:
            return ["generate_briefing"]
        if "message" in lowered or "email" in lowered or "send" in lowered:
            return ["send_message"]
        if "reminder" in lowered or "task" in lowered:
            return ["reminder", "file_controller"]
        if "file" in lowered or "system" in lowered or "inspect" in lowered:
            return ["file_controller"]
        return ["file_controller"]

    def _risk_for_capability(self, capability: str) -> str:
        if capability in {"send_message", "delete_file"}:
            return "CONFIRMATION_REQUIRED" if capability != "delete_file" else "BLOCKED"
        if capability in {"browser_control", "computer_control", "file_controller"}:
            return "LOW_RISK"
        return "READ_ONLY"

    def _build_objective(self, objective_id: str, description: str, capability: str, *, depends_on: Optional[Sequence[str]] = None, purpose: str = "") -> Objective:
        dep_list = list(depends_on or [])
        risk = self._risk_for_capability(capability)
        return Objective(
            objective_id=objective_id,
            description=description,
            purpose=purpose or description,
            dependencies=dep_list,
            required_capabilities=[capability],
            risk=risk,
            priority="MEDIUM",
            status="PENDING",
            execution_goal=description,
            confidence="MEDIUM",
            missing_capability=False,
            blocked_reason=None,
        )

    def _generic_objectives(self, goal: str) -> List[Objective]:
        lowered = goal.lower()
        objectives: List[Objective] = []

        if "open" in lowered and "calculator" in lowered:
            objectives.append(self._build_objective("open_calculator", "Open the Calculator application.", "open_app"))

        if "summary" in lowered or "brief" in lowered or "report" in lowered:
            if any(item in lowered for item in ["system", "summary", "brief", "report"]):
                depends_on = ["collect_system_information"] if "collect_system_information" in [obj.objective_id for obj in objectives] else []
                objectives.append(self._build_objective("generate_system_summary", "Generate a concise summary of the relevant system context.", "generate_briefing", depends_on=depends_on, purpose="Summarize available local context."))

        if "system" in lowered or "status" in lowered or "check" in lowered:
            if not any(obj.objective_id == "collect_system_information" for obj in objectives):
                objectives.append(self._build_objective("collect_system_information", "Collect the relevant local system and task context.", "file_controller", purpose="Gather local status and context."))

        if "weather" in lowered:
            objectives.append(self._build_objective("check_weather", "Check the current weather context.", "weather_report"))

        if "message" in lowered or "email" in lowered or "send" in lowered:
            objectives.append(self._build_objective("send_message", "Prepare and send the requested message.", "send_message"))

        if "reminder" in lowered or "task" in lowered:
            objectives.append(self._build_objective("check_reminders", "Check local reminders and planned tasks.", "reminder"))

        if not objectives:
            objectives.append(self._build_objective("inspect_context", "Inspect the relevant local context.", "file_controller"))

        # Apply simple dependency ordering: any generation/summary depends on relevant collection step.
        for obj in objectives:
            if obj.objective_id == "generate_system_summary" and "collect_system_information" in [existing.objective_id for existing in objectives]:
                obj.dependencies = ["collect_system_information"]
            elif obj.objective_id == "send_message" and "generate_system_summary" in [existing.objective_id for existing in objectives]:
                obj.dependencies = ["generate_system_summary"]
        return objectives

    def _ensure_valid_ids(self, objectives: List[Objective]) -> List[Objective]:
        for index, objective in enumerate(objectives):
            if not objective.objective_id:
                objective.objective_id = f"objective_{index + 1}"
        return objectives

    def _detect_missing_capabilities(self, objectives: List[Objective], goal: str) -> Tuple[List[str], List[str]]:
        missing: List[str] = []
        blocked: List[str] = []
        if self.action_registry is None:
            return missing, blocked
        for objective in objectives:
            for capability in objective.required_capabilities:
                try:
                    if not self.action_registry.has(capability):
                        missing.append(capability)
                        objective.missing_capability = True
                        objective.status = "BLOCKED"
                        objective.blocked_reason = f"Missing capability: {capability}"
                        blocked.append(objective.objective_id)
                except Exception:
                    missing.append(capability)
                    objective.missing_capability = True
                    objective.status = "BLOCKED"
                    objective.blocked_reason = f"Missing capability: {capability}"
                    blocked.append(objective.objective_id)
        return missing, blocked

    def _determine_confidence(self, goal: str, unresolved: List[str]) -> str:
        if unresolved:
            return "LOW"
        if re.search(r"\b(open|check|prepare|summarize|send|message|weather|reminder|system|task)\b", goal.lower()):
            return "HIGH"
        if re.search(r"\b(whatever|something|anything|maybe)\b", goal.lower()):
            return "LOW"
        return "MEDIUM"

    def _compute_order(self, objectives: List[Objective]) -> List[str]:
        graph = {obj.objective_id: set(obj.dependencies) for obj in objectives}
        indegree = {obj.objective_id: 0 for obj in objectives}
        for obj in objectives:
            for dep in obj.dependencies:
                indegree[obj.objective_id] += 1
        ready = [obj.objective_id for obj in objectives if indegree[obj.objective_id] == 0]
        ordered: List[str] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for obj in objectives:
                if current in obj.dependencies:
                    indegree[obj.objective_id] -= 1
                    if indegree[obj.objective_id] == 0:
                        ready.append(obj.objective_id)
        if len(ordered) != len(objectives):
            ordered.extend(obj.objective_id for obj in objectives if obj.objective_id not in ordered)
        return ordered

    def _build_goal_plan(self, goal: str) -> GoalPlan:
        normalized = self.normalize_goal(goal)
        goal_id = f"goal-{uuid.uuid4().hex[:8]}"
        goal_obj = Goal(goal_id=goal_id, original_goal=normalized, normalized_goal=normalized)
        unresolved: List[str] = []

        if not normalized:
            unresolved.append("Goal is empty.")
            return GoalPlan(
                plan_id=f"plan-{uuid.uuid4().hex[:8]}",
                goal=goal_obj,
                objectives=[],
                status=GoalStatuses.NEEDS_CLARIFICATION,
                confidence="LOW",
                unresolved_items=unresolved,
            )

        if re.search(r"\b(whatever|anything|something|do whatever|do something)\b", normalized.lower()):
            unresolved.append("The goal is too ambiguous to decompose safely.")
            return GoalPlan(
                plan_id=f"plan-{uuid.uuid4().hex[:8]}",
                goal=goal_obj,
                objectives=[],
                status=GoalStatuses.NEEDS_CLARIFICATION,
                confidence="LOW",
                unresolved_items=unresolved,
            )

        objectives = self._ensure_valid_ids(self._generic_objectives(normalized))
        missing, blocked = self._detect_missing_capabilities(objectives, normalized)
        if any(objective.missing_capability for objective in objectives):
            for objective in objectives:
                if objective.missing_capability:
                    objective.status = "BLOCKED"
                    objective.blocked_reason = objective.blocked_reason or "Missing capability."
            plan_status = GoalStatuses.BLOCKED
        else:
            plan_status = GoalStatuses.READY

        plan = GoalPlan(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            goal=goal_obj,
            objectives=objectives,
            dependency_graph={obj.objective_id: list(obj.dependencies) for obj in objectives},
            execution_order=self._compute_order(objectives),
            status=plan_status,
            confidence=self._determine_confidence(normalized, unresolved),
            unresolved_items=unresolved,
            final_result={"missing_capabilities": missing, "blocked_objectives": blocked},
        )

        valid, errors = plan.validate()
        if not valid:
            plan.status = GoalStatuses.NEEDS_CLARIFICATION
            plan.unresolved_items.extend(errors)
            return plan
        return plan

    def decompose_goal(self, goal: Any) -> GoalPlan:
        normalized = self.normalize_goal(goal)
        plan = self._build_goal_plan(normalized)
        self.logger(f"[GoalDecomposer] Decomposed: {normalized}")
        return plan

    def decompose(self, goal: Any) -> GoalPlan:
        return self.decompose_goal(goal)

    def build_plan(self, goal: Any) -> GoalPlan:
        return self.decompose_goal(goal)

    def plan_goal(self, goal: Any) -> GoalPlan:
        return self.decompose_goal(goal)

    def validate_plan(self, plan: GoalPlan) -> tuple[bool, List[str]]:
        return plan.validate()

    def save_plan(self, plan: GoalPlan, path: str) -> None:
        from pathlib import Path
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(__import__("json").dumps(plan.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load_plan(cls, path: str) -> GoalPlan:
        import json
        from pathlib import Path
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return GoalPlan.from_dict(raw)

    def learn_from_plan(self, plan: GoalPlan) -> None:
        if self.learning_memory is None:
            return
        try:
            self.learning_memory.learn(
                name=f"plan-{plan.plan_id}",
                goal=plan.goal.original_goal,
                steps=[
                    {"action": cap, "parameters": {"goal": plan.goal.original_goal}, "description": obj.description}
                    for obj in plan.objectives
                    for cap in obj.required_capabilities
                ],
            )
        except Exception:
            pass


__all__ = ["GoalDecomposer"]
