"""
Autonomous Planner for JARVIS.

Converts natural-language goals into validated executable Task objects.

The planner:
- Uses the existing Mark-LIII ActionRegistry.
- Can recall previously learned workflows.
- Can ask Gemini to generate new plans.
- Strictly validates generated actions before execution.
- Never executes actions itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from google import genai

from .autonomous_engine import Task
from .learning_memory import LearningMemory


@dataclass
class Plan:
    goal: str
    tasks: List[Task]
    source: str = "generated"


class AutonomousPlanner:

    def __init__(
        self,
        action_registry: Any = None,
        learning_memory: Optional[LearningMemory] = None,
        logger=print,
        max_steps: int = 20,
        api_key: Optional[str] = None,
        model: str = "gemini-3.7-flash",
    ):
        self.action_registry = action_registry

        self.memory = (
            learning_memory
            or LearningMemory()
        )

        self.logger = logger
        self.max_steps = max_steps
        self.model = model

        self.client = None

        if api_key:
            self.client = genai.Client(
                api_key=api_key
            )

    # --------------------------------------------------------------
    # Registry
    # --------------------------------------------------------------

    def attach_registry(
        self,
        action_registry: Any,
    ) -> None:
        self.action_registry = action_registry

    def available_actions(self) -> List[str]:

        if self.action_registry is None:
            return []

        try:
            return sorted(
                self.action_registry.names()
            )
        except Exception:
            return []

    def action_declarations(self) -> List[Dict[str, Any]]:

        if self.action_registry is None:
            return []

        try:
            return self.action_registry.get_tool_declarations()
        except Exception:
            return []

    # --------------------------------------------------------------
    # Gemini
    # --------------------------------------------------------------

    def attach_gemini(
        self,
        api_key: str,
    ) -> None:

        self.client = genai.Client(
            api_key=api_key
        )

    # --------------------------------------------------------------
    # Validation
    # --------------------------------------------------------------

    def validate_plan(
        self,
        tasks: List[Task],
    ) -> tuple[bool, str]:

        if not tasks:
            return (
                False,
                "Plan contains no tasks.",
            )

        if len(tasks) > self.max_steps:
            return (
                False,
                f"Plan exceeds maximum of "
                f"{self.max_steps} steps.",
            )

        for task in tasks:

            if self.action_registry is None:
                return (
                    False,
                    "ActionRegistry is not attached.",
                )

            if not isinstance(task.action, str):
                return (
                    False,
                    "Task action must be a string.",
                )

            if not self.action_registry.has(
                task.action
            ):
                return (
                    False,
                    f"Unknown action: {task.action}",
                )

            if not isinstance(
                task.parameters,
                dict,
            ):
                return (
                    False,
                    f"Invalid parameters for "
                    f"{task.action}",
                )

        return True, ""

    # --------------------------------------------------------------
    # Learned workflow lookup
    # --------------------------------------------------------------

    def find_learned_plan(
        self,
        goal: str,
    ) -> Optional[Plan]:

        matches = self.memory.find_by_goal(
            goal
        )

        for workflow in matches:

            tasks = [
                Task(
                    action=step.action,
                    parameters=step.parameters,
                    description=step.description,
                )
                for step in workflow.steps
            ]

            valid, _ = self.validate_plan(
                tasks
            )

            if valid:

                self.logger(
                    "[Planner] "
                    f"Using learned workflow: "
                    f"{workflow.name}"
                )

                return Plan(
                    goal=goal,
                    tasks=tasks,
                    source="learned",
                )

        return None

    # --------------------------------------------------------------
    # Gemini plan generation
    # --------------------------------------------------------------

    def create_plan_with_gemini(
        self,
        goal: str,
    ) -> Plan:

        if self.action_registry is None:
            raise RuntimeError(
                "ActionRegistry is not attached."
            )

        if self.client is None:
            raise RuntimeError(
                "Gemini client is not configured."
            )

        declarations = (
            self.action_declarations()
        )

        action_text = json.dumps(
            declarations,
            indent=2,
            ensure_ascii=False,
        )

        prompt = f"""
You are the planning brain of an autonomous desktop AI assistant.

User goal:
{goal}

Available actions:
{action_text}

Create the smallest reliable sequence of actions
needed to accomplish the user's goal.

STRICT RULES:

1. Only use actions from the available actions list.
2. Never invent an action.
3. Never invent parameter names.
4. Parameters must be JSON objects.
5. Use the exact action name.
6. Keep the plan to at most {self.max_steps} steps.
7. Prefer fewer steps when possible.
8. Do not execute anything.
9. Do not explain the plan.
10. Return ONLY valid JSON.

Required JSON format:

{{
  "tasks": [
    {{
      "action": "exact_action_name",
      "parameters": {{}},
      "description": "short description"
    }}
  ]
}}
"""

        self.logger(
            "[Planner] Asking Gemini to create a plan..."
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )

        text = (
            getattr(response, "text", "")
            or ""
        ).strip()

        if not text:
            raise RuntimeError(
                "Gemini returned an empty planning response."
            )

        # Handle accidental markdown fences.
        if text.startswith("```"):
            text = text.strip("`")

            if text.startswith("json"):
                text = text[4:].strip()

        try:
            data = json.loads(text)

        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Gemini returned invalid JSON: {exc}"
            ) from exc

        raw_tasks = data.get("tasks")

        if not isinstance(
            raw_tasks,
            list,
        ):
            raise RuntimeError(
                "Gemini plan does not contain a valid 'tasks' list."
            )

        tasks: List[Task] = []

        for item in raw_tasks:

            if not isinstance(
                item,
                dict,
            ):
                raise RuntimeError(
                    "Invalid task returned by Gemini."
                )

            action = item.get("action")
            parameters = item.get(
                "parameters",
                {},
            )
            description = item.get(
                "description",
                "",
            )

            tasks.append(
                Task(
                    action=action,
                    parameters=parameters,
                    description=description,
                )
            )

        valid, reason = self.validate_plan(
            tasks
        )

        if not valid:
            raise RuntimeError(
                f"Gemini generated an unsafe/invalid plan: {reason}"
            )

        self.logger(
            f"[Planner] Generated {len(tasks)} valid task(s)."
        )

        return Plan(
            goal=goal,
            tasks=tasks,
            source="gemini",
        )

    # --------------------------------------------------------------
    # Main planning entry point
    # --------------------------------------------------------------

    def plan(
        self,
        goal: str,
        prefer_learned: bool = True,
    ) -> Plan:

        goal = goal.strip()

        if not goal:
            raise ValueError(
                "Goal cannot be empty."
            )

        # First try something JARVIS has already learned.
        if prefer_learned:

            learned = self.find_learned_plan(
                goal
            )

            if learned:
                return learned

        # Otherwise create a new plan.
        return self.create_plan_with_gemini(
            goal
        )

    # --------------------------------------------------------------
    # Manual plan support
    # --------------------------------------------------------------

    def create_plan(
        self,
        goal: str,
        tasks: List[Task],
    ) -> Plan:

        valid, reason = self.validate_plan(
            tasks
        )

        if not valid:
            raise ValueError(reason)

        return Plan(
            goal=goal,
            tasks=tasks,
            source="manual",
        )

    # --------------------------------------------------------------
    # Status
    # --------------------------------------------------------------

    def status(self) -> Dict[str, Any]:

        return {
            "registry_attached": (
                self.action_registry is not None
            ),
            "available_actions": len(
                self.available_actions()
            ),
            "learned_workflows": len(
                self.memory.names()
            ),
            "gemini_configured": (
                self.client is not None
            ),
            "model": self.model,
            "max_steps": self.max_steps,
        }
