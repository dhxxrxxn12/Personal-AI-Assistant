from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_config import migrate_legacy_file, runtime_path


# ============================================================
# LEARNED STEP
# ============================================================

@dataclass
class LearnedStep:
    """
    One step in a learned JARVIS workflow.
    """

    action: str
    parameters: Dict[str, Any] = field(
        default_factory=dict
    )
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "parameters": self.parameters,
            "description": self.description,
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
    ) -> "LearnedStep":

        return cls(
            action=str(
                data.get("action", "")
            ),
            parameters=dict(
                data.get(
                    "parameters",
                    {},
                )
            ),
            description=str(
                data.get(
                    "description",
                    "",
                )
            ),
        )


# ============================================================
# LEARNED WORKFLOW
# ============================================================

@dataclass
class LearnedWorkflow:
    """
    A previously successful JARVIS workflow.
    """

    name: str
    goal: str
    steps: List[LearnedStep]
    success_count: int = 0
    failure_count: int = 0
    created_at: float = field(
        default_factory=time.time
    )
    last_used: float = field(
        default_factory=time.time
    )

    def to_dict(self) -> Dict[str, Any]:

        return {
            "name": self.name,
            "goal": self.goal,
            "steps": [
                step.to_dict()
                if isinstance(
                    step,
                    LearnedStep,
                )
                else step
                for step in self.steps
            ],
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "created_at": self.created_at,
            "last_used": self.last_used,
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
    ) -> "LearnedWorkflow":

        raw_steps = data.get(
            "steps",
            [],
        )

        steps = []

        for step in raw_steps:

            if isinstance(
                step,
                LearnedStep,
            ):
                steps.append(step)

            elif isinstance(
                step,
                dict,
            ):
                steps.append(
                    LearnedStep.from_dict(
                        step
                    )
                )

        return cls(
            name=str(
                data.get(
                    "name",
                    "",
                )
            ),
            goal=str(
                data.get(
                    "goal",
                    "",
                )
            ),
            steps=steps,
            success_count=int(
                data.get(
                    "success_count",
                    0,
                )
            ),
            failure_count=int(
                data.get(
                    "failure_count",
                    0,
                )
            ),
            created_at=float(
                data.get(
                    "created_at",
                    time.time(),
                )
            ),
            last_used=float(
                data.get(
                    "last_used",
                    time.time(),
                )
            ),
        )


# ============================================================
# LEARNING MEMORY
# ============================================================

class LearningMemory:
    """
    Persistent memory for successful JARVIS workflows.

    Important:
    Learned workflow matching is deliberately conservative.
    Unrelated goals must never reuse another workflow.
    """

    def __init__(
        self,
        path: Optional[Path] = None,
    ):

        if path is None:
            legacy_path = Path(__file__).resolve().parent / "learning_memory.json"
            path = runtime_path("learning_memory.json", legacy_path)
            migrate_legacy_file(Path(path), legacy_path)

        self.path = Path(path)

        self.workflows: Dict[
            str,
            Dict[str, Any],
        ] = {}

        self._load()

    # ========================================================
    # LOAD
    # ========================================================

    def _load(self) -> None:

        if not self.path.exists():
            self.workflows = {}
            return

        try:

            with self.path.open(
                "r",
                encoding="utf-8",
            ) as f:

                data = json.load(f)

            if isinstance(
                data,
                dict,
            ):
                self.workflows = data

            else:
                self.workflows = {}

        except (
            json.JSONDecodeError,
            OSError,
            TypeError,
        ) as exc:

            print(
                f"[LearningMemory] "
                f"Could not load memory: {exc}"
            )

            self.workflows = {}

    # ========================================================
    # SAVE
    # ========================================================

    def _save(self) -> None:

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary_path = (
            self.path.with_suffix(
                self.path.suffix + ".tmp"
            )
        )

        try:

            with temporary_path.open(
                "w",
                encoding="utf-8",
            ) as f:

                json.dump(
                    self.workflows,
                    f,
                    indent=2,
                    ensure_ascii=False,
                )

            temporary_path.replace(
                self.path
            )

        except OSError as exc:

            print(
                f"[LearningMemory] "
                f"Could not save memory: {exc}"
            )

    # ========================================================
    # NORMALIZATION
    # ========================================================

    @staticmethod
    def _normalize_word(
        word: str,
    ) -> str:

        word = word.strip(
            ".,!?;:'\"()[]{}<>"
        ).lower()

        if len(word) > 4 and word.endswith(
            "ies"
        ):
            word = (
                word[:-3]
                + "y"
            )

        elif len(word) > 4 and word.endswith(
            "s"
        ):
            word = word[:-1]

        return word

    @classmethod
    def _meaningful_words(
        cls,
        text: str,
    ) -> set[str]:

        stop_words = {
            "a",
            "an",
            "and",
            "are",
            "as",
            "at",
            "be",
            "can",
            "could",
            "do",
            "does",
            "for",
            "from",
            "get",
            "give",
            "i",
            "in",
            "into",
            "is",
            "it",
            "me",
            "my",
            "need",
            "of",
            "on",
            "or",
            "please",
            "should",
            "show",
            "tell",
            "that",
            "the",
            "this",
            "to",
            "want",
            "with",
            "would",
            "find",
            "search",
            "look",
            "use",
            "using",
            "open",
            "launch",
            "start",
            "run",
            "make",
        }

        words = {
            cls._normalize_word(
                word
            )
            for word in re.findall(
                r"[A-Za-z0-9_]+",
                text.lower(),
            )
        }

        return {
            word
            for word in words
            if (
                word
                and word not in stop_words
                and len(word) >= 3
            )
        }

    # ========================================================
    # RECALL
    # ========================================================

    def recall(
        self,
        name: str,
    ) -> Optional[LearnedWorkflow]:

        data = self.workflows.get(
            name
        )

        if not isinstance(
            data,
            dict,
        ):
            return None

        return LearnedWorkflow.from_dict(
            data
        )

    # ========================================================
    # LEARN
    # ========================================================

    def learn(
        self,
        name: str,
        goal: str,
        steps: List[Any],
    ) -> LearnedWorkflow:

        if not name or not name.strip():
            raise ValueError(
                "Workflow name cannot be empty."
            )

        if not goal or not goal.strip():
            raise ValueError(
                "Workflow goal cannot be empty."
            )

        learned_steps = []

        for step in steps:

            if isinstance(
                step,
                LearnedStep,
            ):
                learned_steps.append(
                    step
                )

            elif isinstance(
                step,
                dict,
            ):
                learned_steps.append(
                    LearnedStep.from_dict(
                        step
                    )
                )

            elif hasattr(
                step,
                "action",
            ):

                learned_steps.append(
                    LearnedStep(
                        action=str(
                            step.action
                        ),
                        parameters=dict(
                            getattr(
                                step,
                                "parameters",
                                {},
                            )
                        ),
                        description=str(
                            getattr(
                                step,
                                "description",
                                "",
                            )
                        ),
                    )
                )

        now = time.time()

        existing = self.workflows.get(
            name
        )

        success_count = 1
        failure_count = 0
        created_at = now

        if isinstance(
            existing,
            dict,
        ):

            success_count = (
                int(
                    existing.get(
                        "success_count",
                        0,
                    )
                )
                + 1
            )

            failure_count = int(
                existing.get(
                    "failure_count",
                    0,
                )
            )

            created_at = float(
                existing.get(
                    "created_at",
                    now,
                )
            )

        workflow = LearnedWorkflow(
            name=name.strip(),
            goal=goal.strip(),
            steps=learned_steps,
            success_count=success_count,
            failure_count=failure_count,
            created_at=created_at,
            last_used=now,
        )

        self.workflows[
            workflow.name
        ] = workflow.to_dict()

        self._save()

        return workflow

    # ========================================================
    # SAVE COMPATIBILITY
    # ========================================================

    def save(
        self,
        workflow: LearnedWorkflow,
    ) -> bool:

        if not isinstance(
            workflow,
            LearnedWorkflow,
        ):
            return False

        self.workflows[
            workflow.name
        ] = workflow.to_dict()

        self._save()

        return True

    # ========================================================
    # FIND BY GOAL
    # ========================================================

    def find_by_goal(
        self,
        goal: str,
    ) -> List[LearnedWorkflow]:
        """
        Find learned workflows relevant to a goal.

        Requires at least TWO meaningful content words to overlap.

        Therefore:

            Open Apple Music

        will NOT match:

            Find a Blender tutorial on YouTube and open it.
        """

        if not isinstance(
            goal,
            str,
        ):
            return []

        goal = goal.strip()

        if not goal:
            return []

        goal_words = (
            self._meaningful_words(
                goal
            )
        )

        if not goal_words:
            return []

        matches = []

        for name, data in self.workflows.items():

            if not isinstance(
                data,
                dict,
            ):
                continue

            stored_goal = str(
                data.get(
                    "goal",
                    "",
                )
            ).strip()

            if not stored_goal:
                continue

            stored_words = (
                self._meaningful_words(
                    stored_goal
                )
            )

            if not stored_words:
                continue

            overlap_words = (
                goal_words
                & stored_words
            )

            overlap = len(
                overlap_words
            )

            # ------------------------------------------------
            # STRICT SAFETY RULE
            # ------------------------------------------------
            #
            # One shared generic/content word is NOT enough.
            #
            # Require at least two meaningful words.
            # ------------------------------------------------

            if overlap < 2:
                continue

            union_size = len(
                goal_words
                | stored_words
            )

            similarity = (
                overlap / union_size
                if union_size
                else 0.0
            )

            workflow = self.recall(
                name
            )

            if workflow is None:
                continue

            matches.append(
                (
                    overlap,
                    similarity,
                    workflow.success_count,
                    workflow,
                )
            )

        matches.sort(
            key=lambda item: (
                item[0],
                item[1],
                item[2],
            ),
            reverse=True,
        )

        return [
            workflow
            for (
                _,
                _,
                _,
                workflow,
            ) in matches
        ]

    # ========================================================
    # SUCCESS
    # ========================================================

    def mark_success(
        self,
        name: str,
    ) -> bool:

        data = self.workflows.get(
            name
        )

        if not isinstance(
            data,
            dict,
        ):
            return False

        data["success_count"] = (
            int(
                data.get(
                    "success_count",
                    0,
                )
            )
            + 1
        )

        data["last_used"] = time.time()

        self._save()

        return True

    # ========================================================
    # FAILURE
    # ========================================================

    def mark_failure(
        self,
        name: str,
    ) -> bool:

        data = self.workflows.get(
            name
        )

        if not isinstance(
            data,
            dict,
        ):
            return False

        data["failure_count"] = (
            int(
                data.get(
                    "failure_count",
                    0,
                )
            )
            + 1
        )

        data["last_used"] = time.time()

        self._save()

        return True

    # ========================================================
    # NAMES
    # ========================================================

    def names(self) -> List[str]:

        return sorted(
            self.workflows.keys()
        )

    # ========================================================
    # DELETE
    # ========================================================

    def delete(
        self,
        name: str,
    ) -> bool:

        if name not in self.workflows:
            return False

        del self.workflows[
            name
        ]

        self._save()

        return True

    # ========================================================
    # STATUS
    # ========================================================

    def status(
        self,
    ) -> Dict[str, Any]:

        return {
            "workflow_count": len(
                self.workflows
            ),
            "workflows": self.names(),
        }


# ============================================================
# DIRECT TEST
# ============================================================

if __name__ == "__main__":

    memory = LearningMemory()

    print(
        "LearningMemory loaded successfully."
    )

    print(
        memory.status()
    )
