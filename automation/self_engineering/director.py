"""Opt-in lifecycle coordinator for repository inspection and planning."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from .capability_analyzer import CapabilityAnalyzer
from .change_memory import ChangeMemory, EngineeringMemoryRecord
from .engineering_planner import EngineeringPlanner, PlanValidationError
from .models import EngineeringPlan, EngineeringRun
from .policies import EngineeringPolicy
from .repository_inspector import RepositoryInspector


class EngineeringDirector:
    """Drive the safe planning stages without silently executing changes.

    ``prepare`` performs only bounded inspection, deterministic analysis, and
    plan validation.  Code changes, Git checkpoints, and sandbox deployment
    are separate operations that callers must invoke with explicit approvals.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        enabled: Optional[bool] = None,
        inspector: Optional[RepositoryInspector] = None,
        analyzer: Optional[CapabilityAnalyzer] = None,
        planner: Optional[EngineeringPlanner] = None,
        memory: Optional[ChangeMemory] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.enabled = (
            enabled
            if enabled is not None
            else os.getenv("JARVIS_SELF_ENGINEERING_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
        )
        self.policy = EngineeringPolicy(self.workspace_root)
        self.inspector = inspector or RepositoryInspector(self.workspace_root)
        self.analyzer = analyzer or CapabilityAnalyzer()
        self.planner = planner or EngineeringPlanner()
        self.memory = memory or ChangeMemory()

    def prepare(self, goal: str, *, candidate_plan: Optional[str | dict[str, Any]] = None) -> EngineeringRun:
        if not self.enabled:
            raise RuntimeError("Self-engineering is disabled; set JARVIS_SELF_ENGINEERING_ENABLED=1 for an explicit opt-in.")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("Engineering goal must be non-empty.")

        run = EngineeringRun(goal=goal.strip())
        run.record("GOAL", "Engineering goal accepted.")
        run.record("UNDERSTAND", "Goal normalized for bounded repository analysis.")
        snapshot = self.inspector.inspect()
        run.snapshot = snapshot
        run.record("INSPECTION", "Repository structure and Git state inspected.", files=len(snapshot.files))
        analysis = self.analyzer.analyze(run.goal, snapshot)
        run.analysis = analysis
        run.record("GAP_ANALYSIS", "Current and missing capabilities identified.", missing=len(analysis.missing_capabilities))

        plan: EngineeringPlan
        if candidate_plan is None:
            plan = self.planner.fallback(run.goal, analysis, snapshot)
            run.record("PLAN", "Deterministic read/test-only plan created.", source=plan.source)
        else:
            try:
                if isinstance(candidate_plan, str):
                    plan = self.planner.parse_json(candidate_plan, source="provider")
                else:
                    plan = self.planner.validate(candidate_plan, source="provider")
                run.record("PLAN", "Candidate plan passed strict validation.", source=plan.source)
            except (PlanValidationError, TypeError, ValueError) as exc:
                run.record("PLAN_REJECTED", "Candidate plan rejected; deterministic fallback selected.", reason=str(exc))
                plan = self.planner.fallback(run.goal, analysis, snapshot)
                run.record("PLAN", "Deterministic recovery plan created.", source=plan.source)
        run.plan = plan
        run.status = "planned"
        return run

    def report(self, run: EngineeringRun) -> dict[str, Any]:
        """Return a dashboard-safe trace; no secrets or source contents are added."""
        return run.to_dict()

    def remember(
        self,
        run: EngineeringRun,
        *,
        subsystem: str,
        changes: Optional[list[str]] = None,
        tests: Optional[list[str]] = None,
        failures: Optional[list[str]] = None,
        repairs: Optional[list[str]] = None,
        successful_solution: str = "",
        deployment_result: str = "",
    ) -> EngineeringMemoryRecord:
        if not run.analysis:
            raise ValueError("Cannot record an engineering run without capability analysis.")
        actions = [step.operation for step in (run.plan.steps if run.plan else [])]
        entry = EngineeringMemoryRecord(
            goal=run.goal,
            capabilities=run.analysis.missing_capabilities,
            subsystem=subsystem,
            actions=actions,
            changes=changes or [],
            tests=tests or [],
            failures=failures or [],
            repairs=repairs or [],
            successful_solution=successful_solution,
            deployment_result=deployment_result,
        )
        self.memory.record(entry)
        run.record("LEARN", "Engineering outcome recorded with strict context keys.")
        return entry
