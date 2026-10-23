"""
Autonomous Engine for Mark-LIII.

Connects:
    ActionRegistry
    PermissionManager
    LearningMemory
    AutonomousPlanner
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .learning_memory import (
    LearningMemory,
    LearnedStep,
)
from .permission_manager import (
    PermissionManager,
)


@dataclass
class Task:
    action: str
    parameters: Dict[str, Any]
    description: str = ""


@dataclass
class TaskResult:
    success: bool
    action: str
    result: Any = None
    error: Optional[str] = None
    permission_required: bool = False


class AutonomousEngine:

    def __init__(
        self,
        action_registry: Any = None,
        context: Optional[Dict[str, Any]] = None,
        logger=print,
        max_steps: int = 20,
        engineering_director: Any = None,
        capability_builder: Any = None,
    ):

        self.action_registry = action_registry
        self.context = context or {}
        self.logger = logger
        self.max_steps = max_steps
        self.engineering_director = engineering_director
        self.capability_builder = capability_builder

        self.permissions = PermissionManager()
        self.learning = LearningMemory()

        self.execution_history: List[
            TaskResult
        ] = []

    # --------------------------------------------------------------
    # Controlled self-engineering integration
    # --------------------------------------------------------------

    ENGINEERING_MARKERS = (
        "improve planner",
        "improve the planner",
        "improve the code",
        "refactor ",
        "fix the planner",
        "fix planner",
        "modify source",
        "add a capability",
        "add capability",
        "implement a feature",
        "change the code",
        "self-engineering",
    )

    def attach_engineering_director(self, director: Any) -> None:
        """Attach the opt-in engineering coordinator to the existing engine."""
        self.engineering_director = director

    @classmethod
    def is_engineering_goal(cls, goal: str) -> bool:
        normalized = " ".join(str(goal or "").lower().split())
        return any(marker in normalized for marker in cls.ENGINEERING_MARKERS)

    def submit_engineering_goal(self, goal: str, **kwargs: Any) -> Any:
        """Route an engineering goal through the same autonomous lifecycle.

        The director remains opt-in and owns all policy, sandbox, and
        checkpoint decisions. This method never turns provider text into a
        shell command or grants the normal action registry extra permissions.
        """
        if self.engineering_director is None:
            raise RuntimeError("No self-engineering director is attached.")
        return self.engineering_director.execute(goal, **kwargs)

    # --------------------------------------------------------------
    # Controlled local capability analysis
    # --------------------------------------------------------------

    CAPABILITY_MARKERS = (
        "capability analysis",
        "capability roadmap",
        "capability plan",
        "capability builder",
        "missing capabilities",
        "gap analysis",
        "what capabilities",
        "which capabilities",
        "build a capability",
    )

    def attach_capability_builder(self, builder: Any) -> None:
        """Attach the read-only local capability builder to this engine."""
        self.capability_builder = builder

    @classmethod
    def is_capability_goal(cls, goal: str) -> bool:
        normalized = " ".join(str(goal or "").lower().split())
        return any(marker in normalized for marker in cls.CAPABILITY_MARKERS)

    def analyze_capability_goal(self, goal: str, **kwargs: Any) -> Any:
        """Route a capability goal to the local capability builder.

        The builder is read-only: it inspects the repository, inventories
        capabilities, builds the dependency graph and roadmap, and stops at the
        approval gate. Nothing is implemented by this call.
        """
        if self.capability_builder is None:
            raise RuntimeError("No local capability builder is attached.")
        return self.capability_builder.analyze(goal, **kwargs)

    # --------------------------------------------------------------
    # Integration
    # --------------------------------------------------------------

    def attach_registry(
        self,
        action_registry: Any,
    ) -> None:

        self.action_registry = action_registry

    def set_context(
        self,
        context: Dict[str, Any],
    ) -> None:

        self.context = context or {}

    # --------------------------------------------------------------
    # Actions
    # --------------------------------------------------------------

    def available_actions(self) -> List[str]:

        if self.action_registry is None:
            return []

        try:
            return sorted(
                self.action_registry.names()
            )
        except Exception:
            return []

    def has_action(
        self,
        action: str,
    ) -> bool:

        if self.action_registry is None:
            return False

        try:
            return self.action_registry.has(
                action
            )
        except Exception:
            return False

    # --------------------------------------------------------------
    # Validation
    # --------------------------------------------------------------

    def validate_task(
        self,
        task: Task,
    ) -> tuple[bool, str]:

        if not task.action:
            return False, "Missing action."

        if not isinstance(
            task.parameters,
            dict,
        ):
            return (
                False,
                "Parameters must be a dictionary.",
            )

        if task.action == "self_engineering":
            if not isinstance(task.parameters.get("goal"), str) or not task.parameters["goal"].strip():
                return False, "Self-engineering tasks require a non-empty goal."
            return True, ""

        if not self.has_action(
            task.action
        ):
            return (
                False,
                f"Unknown action: {task.action}",
            )

        return True, ""

    # --------------------------------------------------------------
    # Execute
    # --------------------------------------------------------------

    def execute_task(
        self,
        task: Task,
    ) -> TaskResult:

        valid, reason = self.validate_task(
            task
        )

        if not valid:

            result = TaskResult(
                success=False,
                action=task.action,
                error=reason,
            )

            self.execution_history.append(
                result
            )

            return result

        # ----------------------------------------------------------
        # Permission check
        # ----------------------------------------------------------

        permission = self.permissions.check(
            task.action,
            task.parameters,
        )

        if not permission.allowed:

            result = TaskResult(
                success=False,
                action=task.action,
                error=permission.reason,
            )

            self.execution_history.append(
                result
            )

            self.logger(
                "[Autonomous] BLOCKED: "
                f"{task.action}"
            )

            return result

        if permission.requires_confirmation:

            result = TaskResult(
                success=False,
                action=task.action,
                error=(
                    "User confirmation required: "
                    + permission.reason
                ),
                permission_required=True,
            )

            self.execution_history.append(
                result
            )

            self.logger(
                "[Autonomous] Confirmation required: "
                f"{task.action}"
            )

            return result

        # ----------------------------------------------------------
        # Execute existing action
        # ----------------------------------------------------------

        if task.action == "self_engineering":
            try:
                engineering_task = self.submit_engineering_goal(task.parameters["goal"])
                task_result = TaskResult(
                    success=engineering_task.status not in {"failed", "blocked"},
                    action=task.action,
                    result=engineering_task.to_dict(),
                )
            except Exception as exc:
                task_result = TaskResult(success=False, action=task.action, error=f"{type(exc).__name__}: {exc}")
            self.execution_history.append(task_result)
            return task_result

        self.logger(
            "[Autonomous] Executing: "
            f"{task.description or task.action}"
        )

        try:

            result = self.action_registry.run(
                task.action,
                task.parameters,
                self.context,
            )

            failed = (
                isinstance(result, str)
                and result.startswith("Tool '")
                and " failed:" in result
            )

            task_result = TaskResult(
                success=not failed,
                action=task.action,
                result=result,
                error=result if failed else None,
            )

            self.execution_history.append(
                task_result
            )

            return task_result

        except Exception as exc:

            task_result = TaskResult(
                success=False,
                action=task.action,
                error=f"{type(exc).__name__}: {exc}",
            )

            self.execution_history.append(
                task_result
            )

            return task_result

    # --------------------------------------------------------------
    # Execute plan
    # --------------------------------------------------------------

    def execute_plan(
        self,
        tasks: List[Task],
        stop_on_error: bool = True,
    ) -> List[TaskResult]:

        if len(tasks) > self.max_steps:
            raise ValueError(
                f"Plan exceeds maximum of "
                f"{self.max_steps} steps."
            )

        results = []

        for index, task in enumerate(
            tasks,
            start=1,
        ):

            self.logger(
                f"[Autonomous] "
                f"Step {index}/{len(tasks)}"
            )

            result = self.execute_task(
                task
            )

            results.append(result)

            if (
                not result.success
                and stop_on_error
            ):
                break

        return results

    # --------------------------------------------------------------
    # Learning
    # --------------------------------------------------------------

    def learn_workflow(
        self,
        name: str,
        goal: str,
        tasks: List[Task],
    ) -> bool:

        if not tasks:
            return False

        if len(tasks) > self.max_steps:
            return False

        # Only learn workflows where every step succeeded.
        recent = self.execution_history[
            -len(tasks):
        ]

        if len(recent) != len(tasks):
            return False

        if not all(
            result.success
            for result in recent
        ):
            return False

        steps = [
            LearnedStep(
                action=task.action,
                parameters=task.parameters,
                description=task.description,
            )
            for task in tasks
        ]

        self.learning.learn(
            name=name,
            goal=goal,
            steps=steps,
        )

        self.logger(
            "[Autonomous] Learned workflow: "
            f"{name}"
        )

        return True

    # --------------------------------------------------------------
    # Learned workflow
    # --------------------------------------------------------------

    def run_learned_workflow(
        self,
        name: str,
    ) -> List[TaskResult]:

        workflow = self.learning.recall(
            name
        )

        if not workflow:
            return []

        tasks = [
            Task(
                action=step.action,
                parameters=step.parameters,
                description=step.description,
            )
            for step in workflow.steps
        ]

        return self.execute_plan(tasks)

    # --------------------------------------------------------------
    # Status
    # --------------------------------------------------------------

    def status(self) -> Dict[str, Any]:

        successful = sum(
            1
            for result in self.execution_history
            if result.success
        )

        failed = sum(
            1
            for result in self.execution_history
            if not result.success
        )

        return {
            "registry_attached": (
                self.action_registry is not None
            ),
            "available_actions": len(
                self.available_actions()
            ),
            "learned_workflows": len(
                self.learning.names()
            ),
            "execution_count": len(
                self.execution_history
            ),
            "successful": successful,
            "failed": failed,
            "max_steps": self.max_steps,
        }
