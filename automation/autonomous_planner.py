"""
Autonomous Planner for JARVIS.

Converts natural-language goals into validated executable Task objects.

Responsibilities:
- Understand user goals through Gemini.
- Use the existing ActionRegistry.
- Reuse learned workflows.
- Generate safe executable plans.
- Validate every generated action.
- Handle temporary Gemini service failures.
- Never execute actions itself.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from google import genai

from .autonomous_engine import Task
from .learning_memory import LearningMemory


# ==============================================================
# PLAN
# ==============================================================

@dataclass
class Plan:
    goal: str
    tasks: List[Task]
    source: str = "generated"


# ==============================================================
# AUTONOMOUS PLANNER
# ==============================================================

class AutonomousPlanner:

    def __init__(
        self,
        action_registry: Any = None,
        learning_memory: Optional[LearningMemory] = None,
        logger=print,
        max_steps: int = 20,
        api_key: Optional[str] = None,
        model: str = "gemini-flash-latest",
    ):
        self.action_registry = action_registry

        self.memory = (
            learning_memory
            or LearningMemory()
        )

        self.logger = logger
        self.max_steps = max_steps

        self.model = model

        # Fallback models.
        self.fallback_models = [
            "gemini-flash-latest",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
        ]

        self.client = None

        if api_key:
            self.client = genai.Client(
                api_key=api_key
            )

    # ==========================================================
    # REGISTRY
    # ==========================================================

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

    # ==========================================================
    # GEMINI
    # ==========================================================

    def attach_gemini(
        self,
        api_key: str,
    ) -> None:

        self.client = genai.Client(
            api_key=api_key
        )

    # ==========================================================
    # VALIDATION
    # ==========================================================

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

        if self.action_registry is None:
            return (
                False,
                "ActionRegistry is not attached.",
            )

        for task in tasks:

            if not isinstance(
                task.action,
                str,
            ):
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

    # ==========================================================
    # LEARNED WORKFLOWS
    # ==============================================================

    def find_learned_plan(
        self,
        goal: str,
    ) -> Optional[Plan]:

        try:
            matches = self.memory.find_by_goal(
                goal
            )
        except Exception:
            return None

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
                    "[Planner] Using learned workflow: "
                    f"{workflow.name}"
                )

                return Plan(
                    goal=goal,
                    tasks=tasks,
                    source="learned",
                )

        return None

    # ==========================================================
    # PROMPT
    # ==========================================================

    def _build_prompt(
        self,
        goal: str,
        recovery_context: Optional[Dict[str, Any]] = None,
    ) -> str:

        declarations = (
            self.action_declarations()
        )

        action_text = json.dumps(
            declarations,
            indent=2,
            ensure_ascii=False,
        )

        recovery_text = ""
        if recovery_context is not None:
            recovery_text = f"""

RECOVERY CONTEXT:
{json.dumps(recovery_context, indent=2, ensure_ascii=False)}

The previous plan was stopped because the failed action above was blocked by
the Mac Agent safety policy. Preserve the completed work as context, but plan
only the remaining safe alternative. Do not repeat, approve, or work around
the blocked action. The only automatically executable Mac actions are
`open_app` and browser_control operations `go_to`, `open`, and `navigate`.
If no plan using only those actions can satisfy the remaining goal, return
`{{"tasks": []}}`.
"""

        return f"""
You are the planning brain of JARVIS.

JARVIS is currently running on:

OPERATING SYSTEM:
macOS

USER GOAL:
{goal}
{recovery_text}

AVAILABLE ACTIONS:
{action_text}

Your job is to convert the user's goal into the smallest
reliable sequence of executable actions.

IMPORTANT:

- You are ONLY a planner.
- Do NOT execute anything.
- Do NOT call tools.
- Do NOT perform the task yourself.
- Return ONLY JSON.

STRICT RULES:

1. Only use actions from AVAILABLE ACTIONS.
2. Never invent action names.
3. Never invent parameter names.
4. Parameters must always be JSON objects.
5. Use exact action names.
6. Maximum {self.max_steps} tasks.
7. Prefer the smallest reliable plan.
8. Respect macOS.
9. Do not use Windows-only shortcuts.
10. Do not use CTRL+L for macOS browser address bars.
11. Do not assume an application is installed unless an action
    can verify or open it.
12. Do not invent URLs.
13. Do not invent file paths unless required and justified.
14. For cloud/infrastructure requests, use the
    "infrastructure" action.
15. Infrastructure sub-actions go inside the parameters object.
16. Never automatically approve destructive or costly operations.

INFRASTRUCTURE SUB-ACTIONS:

- cloud_status
- list_cloud_providers
- select_cloud_provider
- deployment_status
- prepare_deployment
- create_deployment_plan
- generate_deployment_config
- write_deployment_config
- deploy_jarvis
- update_jarvis
- restart_service
- destroy_resource

EXAMPLE:

User:
Prepare JARVIS to run as a cloud server.

Return:

{{
  "tasks": [
    {{
      "action": "infrastructure",
      "parameters": {{
        "action": "prepare_deployment"
      }},
      "description": "Prepare the JARVIS cloud deployment"
    }}
  ]
}}

Another example:

User:
Generate the deployment configuration for JARVIS.

Return:

{{
  "tasks": [
    {{
      "action": "infrastructure",
      "parameters": {{
        "action": "generate_deployment_config"
      }},
      "description": "Generate the JARVIS cloud deployment configuration"
    }}
  ]
}}

For operations involving:
- deployment
- payments
- purchases
- deletion
- destruction
- shutdown
- restart
- security changes
- sending external messages

do NOT assume the user has approved the action.

Return EXACTLY this structure:

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

    # ==========================================================
    # GEMINI REQUEST WITH RETRY + FALLBACK
    # ==========================================================

    def _generate_with_fallback(
        self,
        prompt: str,
    ):

        if self.client is None:
            raise RuntimeError(
                "Gemini client is not configured."
            )

        models = []

        # Preferred model first.
        models.append(self.model)

        # Then fallbacks.
        for model_name in self.fallback_models:

            if model_name not in models:
                models.append(model_name)

        last_error = None

        for model_name in models:

            # Try each model up to 2 times.
            for attempt in range(2):

                try:

                    self.logger(
                        "[Planner] Gemini model: "
                        f"{model_name} "
                        f"(attempt {attempt + 1}/2)"
                    )

                    response = (
                        self.client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                        )
                    )

                    self.logger(
                        "[Planner] Gemini response received "
                        f"from {model_name}"
                    )

                    return response

                except Exception as exc:

                    last_error = exc

                    error_text = str(exc)

                    self.logger(
                        "[Planner] Gemini error: "
                        f"{error_text}"
                    )

                    # Only wait/retry for temporary failures.
                    if (
                        "503" in error_text
                        or "UNAVAILABLE" in error_text
                        or "429" in error_text
                        or "RESOURCE_EXHAUSTED" in error_text
                    ):

                        if attempt == 0:

                            wait_seconds = 2

                            self.logger(
                                "[Planner] Temporary Gemini "
                                f"failure. Retrying in "
                                f"{wait_seconds}s..."
                            )

                            time.sleep(
                                wait_seconds
                            )

                            continue

                    # Move to next model.
                    break

        raise RuntimeError(
            "All Gemini planning models failed. "
            f"Last error: {last_error}"
        )

    # ==========================================================
    # JSON CLEANING
    # ==========================================================

    @staticmethod
    def _clean_json(
        text: str,
    ) -> str:

        text = (
            text
            .strip()
        )

        # Remove markdown fences.
        if text.startswith("```"):

            text = re.sub(
                r"^```(?:json)?\s*",
                "",
                text,
                flags=re.IGNORECASE,
            )

            text = re.sub(
                r"\s*```$",
                "",
                text,
            )

        # Extract JSON object.
        start = text.find("{")
        end = text.rfind("}")

        if (
            start != -1
            and end != -1
            and end > start
        ):
            text = text[
                start:end + 1
            ]

        return text.strip()

    # ==========================================================
    # GEMINI PLAN GENERATION
    # ==========================================================

    def create_plan_with_gemini(
        self,
        goal: str,
        recovery_context: Optional[Dict[str, Any]] = None,
    ) -> Plan:

        if self.action_registry is None:
            raise RuntimeError(
                "ActionRegistry is not attached."
            )

        if self.client is None:
            raise RuntimeError(
                "Gemini client is not configured."
            )

        prompt = self._build_prompt(
            goal,
            recovery_context,
        )

        self.logger(
            "[Planner] Asking Gemini to create a plan..."
        )

        response = (
            self._generate_with_fallback(
                prompt
            )
        )

        text = (
            getattr(
                response,
                "text",
                "",
            )
            or ""
        ).strip()

        if not text:
            raise RuntimeError(
                "Gemini returned an empty planning response."
            )

        text = self._clean_json(
            text
        )

        try:

            data = json.loads(
                text
            )

        except json.JSONDecodeError as exc:

            raise RuntimeError(
                "Gemini returned invalid JSON: "
                f"{exc}\n"
                f"Response: {text[:1000]}"
            ) from exc

        if not isinstance(
            data,
            dict,
        ):
            raise RuntimeError(
                "Gemini response must be a JSON object."
            )

        raw_tasks = data.get(
            "tasks"
        )

        if not isinstance(
            raw_tasks,
            list,
        ):
            raise RuntimeError(
                "Gemini plan does not contain "
                "a valid 'tasks' list."
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

            action = item.get(
                "action"
            )

            parameters = item.get(
                "parameters",
                {},
            )

            description = item.get(
                "description",
                "",
            )

            if not isinstance(
                action,
                str,
            ):
                raise RuntimeError(
                    "Task action must be a string."
                )

            if not isinstance(
                parameters,
                dict,
            ):
                raise RuntimeError(
                    f"Parameters for {action} "
                    "must be a JSON object."
                )

            tasks.append(
                Task(
                    action=action,
                    parameters=parameters,
                    description=description,
                )
            )

        valid, reason = (
            self.validate_plan(
                tasks
            )
        )

        if not valid:
            raise RuntimeError(
                "Gemini generated an "
                f"unsafe/invalid plan: {reason}"
            )

        self.logger(
            "[Planner] Generated "
            f"{len(tasks)} valid task(s)."
        )

        return Plan(
            goal=goal,
            tasks=tasks,
            source="gemini",
        )

    # ==========================================================
    # MAIN PLANNING ENTRY POINT
    # ==========================================================

    def plan(
        self,
        goal: str,
        prefer_learned: bool = True,
        recovery_context: Optional[Dict[str, Any]] = None,
    ) -> Plan:

        if not isinstance(
            goal,
            str,
        ):
            raise ValueError(
                "Goal must be a string."
            )

        goal = goal.strip()

        if not goal:
            raise ValueError(
                "Goal cannot be empty."
            )

        # Try learned workflows first.
        if prefer_learned and recovery_context is None:

            learned = (
                self.find_learned_plan(
                    goal
                )
            )

            if learned:
                return learned

        # Generate a new plan.
        return (
            self.create_plan_with_gemini(
                goal,
                recovery_context,
            )
        )

    # ==========================================================
    # MANUAL PLAN SUPPORT
    # ==========================================================

    def create_plan(
        self,
        goal: str,
        tasks: List[Task],
    ) -> Plan:

        valid, reason = (
            self.validate_plan(
                tasks
            )
        )

        if not valid:
            raise ValueError(
                reason
            )

        return Plan(
            goal=goal,
            tasks=tasks,
            source="manual",
        )

    # ==========================================================
    # STATUS
    # ==========================================================

    def status(
        self,
    ) -> Dict[str, Any]:

        try:
            learned_count = len(
                self.memory.names()
            )
        except Exception:
            learned_count = 0

        return {
            "registry_attached": (
                self.action_registry is not None
            ),
            "available_actions": len(
                self.available_actions()
            ),
            "learned_workflows": learned_count,
            "gemini_configured": (
                self.client is not None
            ),
            "model": self.model,
            "fallback_models": (
                self.fallback_models
            ),
            "max_steps": self.max_steps,
            "platform": "macOS",
            "router_enabled": False,
        }
        # ==========================================================
    # STEP-BY-STEP AUTONOMOUS REASONING
    # ==========================================================
        # ==========================================================
    # STEP-BY-STEP AUTONOMOUS REASONING
    # ==========================================================

    def next_task(
        self,
        goal: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Decide the next action for an autonomous goal.

        JARVIS first tries to reuse a learned workflow.
        If no learned workflow matches, Gemini decides the
        next action dynamically from the execution history.
        """

        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("Goal cannot be empty.")

        goal = goal.strip()

        if history is None:
            history = []

        # ------------------------------------------------------
        # STEP LIMIT
        # ------------------------------------------------------

        if len(history) >= self.max_steps:
            return {
                "done": True,
                "reason": (
                    f"Maximum autonomous steps "
                    f"({self.max_steps}) reached."
                ),
                "task": None,
            }

        # ------------------------------------------------------
        # TRY LEARNED WORKFLOW
        # ------------------------------------------------------

        learned = None

        try:
            learned = self.find_learned_plan(goal)
        except Exception as exc:
            self.logger(
                f"[Planner] Learned workflow lookup failed: {exc}"
            )

        if learned and learned.tasks:

            current_step = len(history)

            # More learned steps are available.
            if current_step < len(learned.tasks):

                task = learned.tasks[current_step]

                self.logger(
                    "[Planner] 🧠 Reusing learned workflow: "
                    f"{learned.source} | "
                    f"step {current_step + 1}/"
                    f"{len(learned.tasks)}"
                )

                return {
                    "done": False,
                    "reason": (
                        "Reusing a previously learned workflow."
                    ),
                    "task": task,
                    "learned_workflow": learned,
                    "learned_index": current_step,
                }

            # All learned steps have completed.
            self.logger(
                "[Planner] 🧠 Learned workflow completed."
            )

            return {
                "done": True,
                "reason": (
                    "All steps in the learned workflow "
                    "have been completed."
                ),
                "task": None,
                "learned_workflow": learned,
            }

        # ------------------------------------------------------
        # NO LEARNED WORKFLOW
        # USE GEMINI FOR DYNAMIC REASONING
        # ------------------------------------------------------

        declarations = self.action_declarations()

        action_text = json.dumps(
            declarations,
            indent=2,
            ensure_ascii=False,
        )

        history_text = json.dumps(
            history[-10:],
            indent=2,
            ensure_ascii=False,
        )

        prompt = f"""
You are the step-by-step reasoning brain of JARVIS.

JARVIS is running on macOS.

USER GOAL:
{goal}

WHAT HAS HAPPENED SO FAR:
{history_text}

AVAILABLE ACTIONS:
{action_text}

Decide the SINGLE BEST NEXT ACTION.

You must use the results of previous actions when
deciding what to do next.

IMPORTANT RULES:

1. Return ONLY JSON.
2. Return exactly ONE next action.
3. Never invent an action.
4. Never invent parameter names.
5. Parameters must always be a JSON object.
6. Only use AVAILABLE ACTIONS.
7. If the goal is already complete, return done=true.
8. If information is needed, use an appropriate observation action.
9. For browser tasks, use browser_control observation actions
   such as get_text, get_url, screenshot, etc. when appropriate.
10. Do not repeat a failed action unless there is a clear reason.
11. Never automatically approve destructive operations.
12. Never automatically approve payments or purchases.
13. Never automatically approve external messages.
14. Respect macOS.
15. Keep the number of actions as small as reasonably possible.

If another action is required, return:

{{
    "done": false,
    "reason": "why this action is needed",
    "task": {{
        "action": "exact_action_name",
        "parameters": {{}},
        "description": "short description"
    }}
}}

If the goal is complete, return:

{{
    "done": true,
    "reason": "why the goal is complete",
    "task": null
}}
"""

        self.logger(
            "[Planner] 🧠 Deciding next autonomous action..."
        )

        response = self._generate_with_fallback(prompt)

        text = (
            getattr(response, "text", "") or ""
        ).strip()

        if not text:
            raise RuntimeError(
                "Gemini returned an empty next-action response."
            )

        text = self._clean_json(text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Gemini returned invalid next-action JSON: "
                f"{exc}\nResponse: {text[:1000]}"
            ) from exc

        if not isinstance(data, dict):
            raise RuntimeError(
                "Gemini next-action response must be a JSON object."
            )

        # ------------------------------------------------------
        # GOAL COMPLETE
        # ------------------------------------------------------

        done = bool(data.get("done", False))

        reason = str(
            data.get("reason", "")
        ).strip()

        if done:
            self.logger(
                f"[Planner] ✅ Goal appears complete: {reason}"
            )

            return {
                "done": True,
                "reason": reason,
                "task": None,
            }

        # ------------------------------------------------------
        # NEXT TASK
        # ------------------------------------------------------

        raw_task = data.get("task")

        if not isinstance(raw_task, dict):
            raise RuntimeError(
                "Gemini did not return a valid next task."
            )

        action = raw_task.get("action")

        parameters = raw_task.get(
            "parameters",
            {},
        )

        description = raw_task.get(
            "description",
            "",
        )

        if not isinstance(action, str):
            raise RuntimeError(
                "Next task action must be a string."
            )

        if not isinstance(parameters, dict):
            raise RuntimeError(
                f"Parameters for {action} "
                "must be a JSON object."
            )

        task = Task(
            action=action,
            parameters=parameters,
            description=description,
        )

        # ------------------------------------------------------
        # VALIDATE BEFORE EXECUTION
        # ------------------------------------------------------

        valid, validation_reason = (
            self.validate_plan([task])
        )

        if not valid:
            raise RuntimeError(
                "Gemini generated an invalid next action: "
                f"{validation_reason}"
            )

        self.logger(
            f"[Planner] ➡️ Next action: {task.action}"
        )

        return {
            "done": False,
            "reason": reason,
            "task": task,
        }
