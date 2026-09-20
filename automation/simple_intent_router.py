"""Deterministic routing for a small set of explicitly safe intents."""

from __future__ import annotations

import re
from typing import Optional

from .autonomous_engine import Task
from .autonomous_planner import Plan


class SimpleIntentRouter:
    """Build validated planner-shaped plans for unambiguous safe commands."""

    _OPEN_APP_NAMES = {
        "safari": "Safari",
        "chrome": "Chrome",
        "calculator": "Calculator",
        "finder": "Finder",
    }

    _OPEN_APP_PATTERN = re.compile(
        r"^(?:open|launch|start)(?:\s+the)?\s+([a-z]+)$",
        re.IGNORECASE,
    )

    def route(self, goal: str) -> Optional[Plan]:
        if not isinstance(goal, str):
            return None

        match = self._OPEN_APP_PATTERN.fullmatch(" ".join(goal.strip().split()))
        if match is None:
            return None

        app_name = self._OPEN_APP_NAMES.get(match.group(1).lower())
        if app_name is None:
            return None

        return Plan(
            goal=goal.strip(),
            tasks=[Task(
                action="open_app",
                parameters={"app_name": app_name},
                description=f"Open {app_name}",
            )],
            source="simple_intent",
        )
