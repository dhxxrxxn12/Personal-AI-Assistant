"""Opt-in lifecycle coordinator for repository inspection and planning."""

from __future__ import annotations

import os
import hashlib
import inspect
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .capability_analyzer import CapabilityAnalyzer
from .change_memory import ChangeMemory, EngineeringMemoryRecord
from .engineering_planner import EngineeringPlanner, PlanValidationError
from .models import (
    EngineeringPlan,
    EngineeringRun,
    EngineeringTask,
    EngineeringTaskStatus,
)
from .policies import EngineeringPolicy
from .repository_inspector import RepositoryInspector
from .repair_engine import RepairEngine
from .sandbox_manager import SandboxHandle, SandboxManager
from .test_runner import TestResult, TestRunner
from .code_executor import CodeExecutor
from .failure_analyzer import FailureAnalysis
from .failure_analyzer import FailureAnalyzer
from .git_manager import GitManager


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
                    plan = self.planner.parse_json(candidate_plan, source="provider", snapshot=snapshot)
                else:
                    plan = self.planner.validate(candidate_plan, source="provider", snapshot=snapshot)
                if plan.goal.strip().lower() != run.goal.strip().lower():
                    raise PlanValidationError("Plan goal does not match the requested engineering goal.")
                run.record("PLAN", "Candidate plan passed strict validation.", source=plan.source)
            except (PlanValidationError, TypeError, ValueError) as exc:
                run.record("PLAN_REJECTED", "Candidate plan rejected; deterministic fallback selected.", reason=str(exc))
                plan = self.planner.fallback(run.goal, analysis, snapshot)
                run.record("PLAN", "Deterministic recovery plan created.", source=plan.source)
        run.plan = plan
        run.status = "planned"
        return run

    @staticmethod
    def _task_id(value: Optional[str]) -> str:
        if value:
            return value
        return f"engineering-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _affected_files(plan: EngineeringPlan) -> list[str]:
        values = list(plan.affected_files)
        for step in plan.steps:
            values.extend(step.files)
            values.extend(step.tests)
        return list(dict.fromkeys(values))

    @staticmethod
    def _mutating(plan: EngineeringPlan) -> bool:
        return any(step.operation in {
            "create_file", "modify_file", "rename_file", "delete_file",
            "create_branch", "create_commit", "rollback", "sandbox_deploy",
            "production_deploy",
        } for step in plan.steps)

    @staticmethod
    def _test_data(result: TestResult) -> dict[str, Any]:
        return result.to_dict()

    @staticmethod
    def _safe_diff(value: str) -> str:
        return FailureAnalyzer.redact(value or "", limit=20_000)

    def _report_text(self, task: EngineeringTask) -> str:
        tests = task.test_results
        passed = sum(1 for result in tests if result.get("success"))
        failed = sum(1 for result in tests if not result.get("success"))
        checkpoint = task.git_checkpoint or "not created"
        return "\n".join([
            "ENGINEERING RESULT",
            f"Goal: {task.goal}",
            f"Status: {task.status.upper()}",
            f"Files inspected/affected: {len(task.affected_files)}",
            f"Tests: {passed} passed, {failed} failed",
            f"Repair attempts: {len(task.repair_attempts)}",
            f"Git checkpoint: {checkpoint}",
            f"Sandbox: {task.sandbox_id or 'not created'}",
            f"Verification: {'passed' if task.verification_result and task.verification_result.get('success') else 'pending or failed'}",
            f"Production impact: {task.production_impact}",
            f"Next action: {'review/approve production deployment' if task.status == EngineeringTaskStatus.AWAITING_DEPLOY_APPROVAL.value else 'inspect the failure report' if task.error else 'none'}",
        ])

    def execute(
        self,
        goal: str,
        *,
        candidate_plan: Optional[str | dict[str, Any]] = None,
        task_id: Optional[str] = None,
        approved: bool = False,
        implementation: Optional[Callable[..., Any]] = None,
        repair: Optional[Callable[..., Any]] = None,
        max_repair_attempts: int = 2,
        preserve_successful_sandbox: bool = False,
    ) -> EngineeringTask:
        """Run a complete bounded engineering lifecycle in a Git worktree.

        ``implementation`` is a trusted application callback.  It receives a
        :class:`CodeExecutor`, the validated plan, and the task when declared
        with three parameters.  Provider text never becomes executable code or
        a shell command; callers must explicitly supply the implementation
        boundary and approval.
        """
        run = self.prepare(goal, candidate_plan=candidate_plan)
        task = EngineeringTask(self._task_id(task_id), run.goal)
        task.repository_inspection = run.snapshot.to_dict() if run.snapshot else None
        task.capability_analysis = run.analysis.to_dict() if run.analysis else None
        task.plan = run.plan.to_dict() if run.plan else None
        task.affected_files = self._affected_files(run.plan) if run.plan else []
        task.proposed_changes = list(run.plan.proposed_changes) if run.plan else []
        task.risk_level = run.plan.risk if run.plan else "low"
        # Preserve the factual prepare trace in the durable task report before
        # appending execution-specific lifecycle events.
        task.events.extend(run.events)
        task.record("repository_inspected", "Repository inspection completed.", files=len(task.affected_files))
        task.record("capability_analyzed", "Capability analysis completed.", missing=len(run.analysis.missing_capabilities) if run.analysis else 0)
        task.record("plan_created", "Engineering plan validated.", source=run.plan.source if run.plan else "unknown")

        if run.plan is None:
            task.set_status(EngineeringTaskStatus.FAILED)
            task.error = "No validated engineering plan was produced."
            task.final_report = self._report_text(task)
            return task

        requires_approval = self._mutating(run.plan) or run.plan.risk != "low"
        if requires_approval and not approved:
            task.required_approval = True
            task.approval_reason = "Mutating engineering operations require explicit approval before sandbox execution."
            task.set_status(EngineeringTaskStatus.AWAITING_APPROVAL)
            task.record("approval_required", task.approval_reason)
            task.final_report = self._report_text(task)
            return task

        sandbox_manager = SandboxManager.for_repository(self.workspace_root, self.policy)
        handle: Optional[SandboxHandle] = None
        sandbox_policy: Optional[EngineeringPolicy] = None
        executor: Optional[CodeExecutor] = None
        changed_files: list[str] = []
        try:
            task.set_status(EngineeringTaskStatus.SANDBOX_CREATING)
            task.record("sandbox_created", "Creating isolated Git worktree.")
            handle = sandbox_manager.create_worktree(task.task_id, approved=approved or not requires_approval)
            sandbox_policy = sandbox_manager.policy_for(handle)
            executor = CodeExecutor(sandbox_policy)
            task.sandbox_id = handle.sandbox_id
            task.sandbox_path = str(handle.path)
            task.record("sandbox_created", "Isolated Git worktree created.", branch=handle.branch)

            task.set_status(EngineeringTaskStatus.IMPLEMENTING)
            task.record("change_started", "Implementation phase started.")
            if implementation is not None:
                parameters = len(inspect.signature(implementation).parameters)
                if parameters >= 3:
                    implementation(executor, run.plan, task)
                else:
                    implementation(executor, run.plan)
            elif self._mutating(run.plan):
                raise RuntimeError("A mutating plan requires an explicit trusted implementation callback.")
            approved_paths = set(task.affected_files)
            unexpected_paths = [
                item["path"] for item in executor.trace()
                if item["operation"] != "read_source" and item["path"] not in approved_paths
            ]
            if unexpected_paths:
                raise PermissionError(
                    "Implementation changed files outside the validated plan: "
                    + ", ".join(sorted(set(unexpected_paths)))
                )
            changed_files = list(dict.fromkeys(
                item["path"] for item in executor.trace()
                if item["operation"] not in {"read_source"}
            ))
            task.proposed_changes = list(dict.fromkeys(task.proposed_changes + changed_files))
            task.record("change_completed", "Implementation phase completed.", changes=len(task.proposed_changes))

            runner = TestRunner(handle.path, sandbox_policy)
            test_paths = list(dict.fromkeys(
                [*run.plan.tests, *(test for step in run.plan.steps for test in step.tests)]
            ))

            def validate() -> TestResult:
                static = runner.run_static_check(task.affected_files or None)
                task.test_results.append(self._test_data(static))
                if not static.success:
                    return static
                tests = runner.run_tests(test_paths or None)
                task.test_results.append(self._test_data(tests))
                return tests

            task.set_status(EngineeringTaskStatus.TESTING)
            task.record("tests_started", "Static checks and tests started.")

            def apply_repair(analysis: FailureAnalysis, attempt: int) -> bool:
                task.set_status(EngineeringTaskStatus.REPAIRING)
                task.record("repair_started", f"Repair attempt {attempt} started.", category=analysis.category)
                if repair is None:
                    task.repair_attempts.append({
                        "attempt": attempt,
                        "analysis": analysis.__dict__,
                        "applied": False,
                        "reason": "no repair callback was provided",
                    })
                    return False
                parameters = len(inspect.signature(repair).parameters)
                if parameters >= 3:
                    applied = bool(repair(analysis, attempt, executor))
                else:
                    applied = bool(repair(analysis, attempt))
                unexpected = [
                    item["path"] for item in executor.trace()
                    if item["operation"] != "read_source" and item["path"] not in set(task.affected_files)
                ]
                if unexpected:
                    raise PermissionError(
                        "Repair changed files outside the validated plan: "
                        + ", ".join(sorted(set(unexpected)))
                    )
                task.repair_attempts.append({
                    "attempt": attempt,
                    "analysis": analysis.__dict__,
                    "applied": applied,
                })
                task.record("repair_completed", f"Repair attempt {attempt} completed.", applied=applied)
                return applied

            def repair_key(analysis: FailureAnalysis, _attempt: int) -> str:
                value = "|".join([analysis.category, *analysis.likely_files, analysis.relevant_output])
                return hashlib.sha256(value.encode("utf-8")).hexdigest()

            report = RepairEngine(max_attempts=max_repair_attempts).run(
                validate,
                apply_repair,
                repair_key=repair_key,
            )
            task.set_status(EngineeringTaskStatus.VERIFYING if report.success else EngineeringTaskStatus.FAILED)
            if not report.success:
                task.error = report.stopped_reason
                task.record("task_failed", "Engineering validation failed.", reason=report.stopped_reason)
                task.verification_result = {
                    "success": False,
                    "reason": report.stopped_reason,
                    "current_diff": self._safe_diff(GitManager(handle.path, sandbox_policy).diff()),
                }
                return task

            git = GitManager(handle.path, sandbox_policy)
            diff = git.diff()
            if changed_files:
                commit = git.checkpoint(
                    f"feat(self-engineering): {task.goal[:60]}",
                    changed_files,
                    approved=True,
                )
                task.git_checkpoint = {"commit": commit, "branch": handle.branch, "diff": self._safe_diff(diff)}
                task.set_status(EngineeringTaskStatus.CHECKPOINTED)
                task.record("git_checkpoint_created", "Git checkpoint created in the isolated branch.", commit=commit)
            else:
                task.record("git_checkpoint_skipped", "No file changes were produced by the read/test task.")

            task.verification_result = {
                "success": True,
                "static_and_tests": True,
                "git_status": git.status(),
                "diff": self._safe_diff(diff),
                "production_unchanged": True,
            }
            task.set_status(EngineeringTaskStatus.AWAITING_DEPLOY_APPROVAL if changed_files else EngineeringTaskStatus.COMPLETED)
            task.production_impact = "pending approval" if changed_files else "unchanged"
            task.record("verification_completed", "Sandbox result verified; production remains unchanged.")
            return task
        except Exception as exc:
            task.set_status(EngineeringTaskStatus.FAILED)
            task.error = f"{type(exc).__name__}: {exc}"
            task.record("task_failed", "Engineering lifecycle stopped safely.", error=task.error)
            return task
        finally:
            if handle is not None and task.status != EngineeringTaskStatus.FAILED.value:
                try:
                    sandbox_manager.cleanup(handle, preserve=preserve_successful_sandbox)
                except Exception as cleanup_error:
                    task.record("cleanup_failed", "Sandbox cleanup failed; preserve it for inspection.", error=str(cleanup_error))
            task.final_report = self._report_text(task)

    run_task = execute

    def report(self, run: EngineeringRun) -> dict[str, Any]:
        """Return a dashboard-safe trace; no secrets or source contents are added."""
        if hasattr(run, "to_dict"):
            return run.to_dict()
        raise TypeError("Engineering report requires a serializable engineering model.")

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
