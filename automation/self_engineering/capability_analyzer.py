"""Deterministic capability and risk analysis over an inspection snapshot."""

from __future__ import annotations

from typing import Dict, List, Tuple

from .models import CapabilityAnalysis, RepositorySnapshot


class CapabilityAnalyzer:
    """Map high-level goals to evidence-backed repository capabilities."""

    RULES: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
        "planner": (("automation/autonomous_planner.py",), ("LLM provider",)),
        "permission manager": (("automation/permission_manager.py",), ("approval UI",)),
        "learning memory": (("automation/learning_memory.py", "memory/memory_manager.py"), ("privacy policy",)),
        "voice interaction": (("cloud/voice.py", "dashboard/static/hud/js/voice.js"), ("live speech provider",)),
        "notifications": (("actions/reminder.py",), ("notification delivery",)),
        "context aggregation": (("actions/background_monitor.py", "actions/screen_processor.py"), ("calendar/email/weather connectors",)),
        "deployment": (("Dockerfile", "render.yaml", "DEPLOYMENT.md"), ("sandbox environment",)),
        "tests": (("tests",), ("test runner",)),
        "proactive scheduler": (("scheduler.py", "scheduler", "schedule"), ("durable scheduler", "timezone policy")),
        "email integration": (("email", "gmail"), ("OAuth credentials", "external API client")),
        "calendar integration": (("calendar", "google_calendar"), ("OAuth credentials", "calendar API client")),
        "telephony": (("telephony", "twilio", "phone"), ("telephony provider", "consent policy")),
    }

    GOAL_ALIASES = {
        "proactive": {"proactive scheduler", "context aggregation", "notifications"},
        "morning": {"proactive scheduler", "context aggregation", "notifications"},
        "briefing": {"context aggregation", "notifications"},
        "voice": {"voice interaction"},
        "call": {"telephony"},
        "phone": {"telephony"},
        "email": {"email integration"},
        "calendar": {"calendar integration"},
        "schedule": {"proactive scheduler"},
    }

    def _paths(self, snapshot: RepositorySnapshot) -> set[str]:
        values = set(snapshot.tree)
        values.update(module.path for module in snapshot.python_modules)
        return values

    def _present(self, capability: str, snapshot: RepositorySnapshot) -> Tuple[bool, List[str]]:
        paths = self._paths(snapshot)
        markers, _ = self.RULES[capability]
        evidence = []
        for marker in markers:
            if marker in paths:
                evidence.append(marker)
                continue
            if marker == "tests":
                matches = sorted(path for path in paths if path.startswith("tests/"))
                evidence.extend(matches[:3])
                continue
            if marker in {"scheduler", "schedule", "email", "gmail", "calendar", "google_calendar", "telephony", "twilio", "phone"}:
                matches = sorted(path for path in paths if marker in path.lower())
                evidence.extend(matches[:3])
        return bool(evidence), evidence[:5]

    def analyze(self, goal: str, snapshot: RepositorySnapshot) -> CapabilityAnalysis:
        clean_goal = " ".join(str(goal).split())
        lower_goal = clean_goal.lower()
        current: Dict[str, bool] = {}
        evidence: Dict[str, List[str]] = {}
        for capability in self.RULES:
            current[capability], evidence[capability] = self._present(capability, snapshot)

        requested = set()
        for term, capabilities in self.GOAL_ALIASES.items():
            if term in lower_goal:
                requested.update(capabilities)
        if not requested:
            requested = {"planner", "permission manager", "tests"}

        missing = sorted(capability for capability in requested if not current.get(capability, False))
        dependencies: List[str] = []
        for capability in missing:
            dependencies.extend(self.RULES[capability][1])
        dependencies = sorted(dict.fromkeys(dependencies))

        risks: List[str] = []
        if any(item in lower_goal for item in ("email", "calendar", "phone", "call", "message")):
            risks.extend(["external communication", "credential handling", "privacy"])
        if any(item in lower_goal for item in ("proactive", "morning", "briefing")):
            risks.extend(["personal data aggregation", "notification fatigue", "timezone and scheduling errors"])
        if "telephony" in missing:
            risks.append("call consent and recording policy")
        risks = sorted(dict.fromkeys(risks))

        return CapabilityAnalysis(
            goal=clean_goal,
            current_capabilities=current,
            missing_capabilities=missing,
            dependencies=dependencies,
            risks=risks,
            evidence=evidence,
        )
