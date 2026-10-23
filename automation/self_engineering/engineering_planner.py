"""Strict structured engineering-plan validation and safe fallback planning."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, Optional

from .models import CapabilityAnalysis, EngineeringPlan, EngineeringStep, RepositorySnapshot
from .policies import EngineeringOperation, EngineeringPolicy, RiskLevel


class PlanValidationError(ValueError):
    """Raised when a generated engineering plan is malformed or unsafe."""


class EngineeringPlanner:
    """Validate provider output and provide a deterministic no-write fallback."""

    PLAN_VERSION = 1
    MAX_STEPS = 20
    _ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")

    def __init__(self, *, max_steps: int = MAX_STEPS) -> None:
        self.max_steps = max_steps

    @staticmethod
    def _string_list(value: Any, field: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise PlanValidationError(f"{field} must be a list of non-empty strings.")
        return [item.strip() for item in value]

    @staticmethod
    def _validate_relative_path(path: str, field: str) -> None:
        value = path.replace("\\", "/")
        if value.startswith("/") or value == ".." or value.startswith("../") or "/../" in value:
            raise PlanValidationError(f"{field} contains a path outside the workspace.")
        name = value.rsplit("/", 1)[-1]
        if name.startswith(".env") and name != ".env.example":
            raise PlanValidationError(f"{field} targets a protected environment file.")
        if any(part in {".git", ".venv", "__pycache__"} for part in value.split("/")):
            raise PlanValidationError(f"{field} targets a protected directory.")
        lowered = name.lower()
        if any(marker in lowered for marker in ("credential", "client_secret", "api_key", "token")):
            raise PlanValidationError(f"{field} targets a protected secret-like file.")

    def _step(self, raw: Any, index: int, snapshot: Optional[RepositorySnapshot] = None) -> EngineeringStep:
        if not isinstance(raw, dict):
            raise PlanValidationError(f"steps[{index}] must be an object.")
        required = ("id", "description", "operation")
        missing = [key for key in required if not isinstance(raw.get(key), str) or not raw[key].strip()]
        if missing:
            raise PlanValidationError(f"steps[{index}] missing required field(s): {', '.join(missing)}.")
        step_id = raw["id"].strip()
        if not self._ID_RE.fullmatch(step_id):
            raise PlanValidationError(f"steps[{index}].id is invalid.")
        operation = raw["operation"].strip()
        allowed = {item.value for item in EngineeringOperation}
        if operation not in allowed:
            raise PlanValidationError(f"steps[{index}] uses an unapproved operation: {operation}.")
        files = self._string_list(raw.get("files", []), f"steps[{index}].files")
        tests = self._string_list(raw.get("tests", []), f"steps[{index}].tests")
        dependencies = self._string_list(raw.get("dependencies", []), f"steps[{index}].dependencies")
        for field, values in (("files", files), ("tests", tests)):
            for value in values:
                self._validate_relative_path(value, f"steps[{index}].{field}")
        if snapshot is not None:
            known_files = set(snapshot.tree) | set(snapshot.tests) | set(snapshot.configs) | set(snapshot.deployment_files)
            existing_ops = {
                EngineeringOperation.READ_SOURCE.value,
                EngineeringOperation.RENAME_FILE.value,
                EngineeringOperation.DELETE_FILE.value,
            }
            if operation in existing_ops:
                missing = [value for value in files if value not in known_files]
                if missing:
                    raise PlanValidationError(
                        f"steps[{index}] references files not found by repository inspection: {', '.join(missing)}."
                    )
            missing_tests = [value for value in tests if value not in known_files]
            if missing_tests:
                raise PlanValidationError(
                    f"steps[{index}] references tests not found by repository inspection: {', '.join(missing_tests)}."
                )
        risk = str(raw.get("risk", "low")).lower().strip()
        if risk not in {item.value for item in RiskLevel}:
            raise PlanValidationError(f"steps[{index}].risk is invalid.")
        requires_approval = bool(raw.get("requires_approval", risk != RiskLevel.LOW.value))
        if risk != RiskLevel.LOW.value and not requires_approval:
            raise PlanValidationError(f"steps[{index}] must require approval for {risk} risk.")
        if operation in {EngineeringOperation.CREATE_FILE.value, EngineeringOperation.MODIFY_FILE.value, EngineeringOperation.RENAME_FILE.value, EngineeringOperation.DELETE_FILE.value} and not files:
            raise PlanValidationError(f"steps[{index}] must name a target file.")
        return EngineeringStep(
            id=step_id,
            description=raw["description"].strip(),
            operation=operation,
            files=files,
            dependencies=dependencies,
            tests=tests,
            risk=risk,
            requires_approval=requires_approval,
        )

    def validate(
        self,
        raw: Dict[str, Any],
        *,
        source: str = "generated",
        snapshot: Optional[RepositorySnapshot] = None,
    ) -> EngineeringPlan:
        if not isinstance(raw, dict):
            raise PlanValidationError("Plan must be a JSON object.")
        try:
            version = int(raw.get("version", self.PLAN_VERSION))
        except (TypeError, ValueError) as exc:
            raise PlanValidationError("Plan version must be an integer.") from exc
        if version != self.PLAN_VERSION:
            raise PlanValidationError(f"Unsupported plan version: {version}.")
        goal = raw.get("goal")
        analysis = raw.get("analysis", "")
        if not isinstance(goal, str) or not goal.strip():
            raise PlanValidationError("Plan goal is empty.")
        steps_raw = raw.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            raise PlanValidationError("Plan contains no tasks.")
        if not isinstance(analysis, str) or not analysis.strip():
            raise PlanValidationError("Plan analysis must be a string.")
        if len(steps_raw) > self.max_steps:
            raise PlanValidationError(f"Plan exceeds maximum of {self.max_steps} steps.")
        steps = [self._step(item, index, snapshot) for index, item in enumerate(steps_raw)]
        ids = [step.id for step in steps]
        if len(set(ids)) != len(ids):
            raise PlanValidationError("Plan step IDs must be unique.")
        known_ids = set(ids)
        for step in steps:
            unknown = set(step.dependencies) - known_ids
            if unknown:
                raise PlanValidationError(f"Step {step.id} has unknown dependencies: {', '.join(sorted(unknown))}.")
        approvals = self._string_list(raw.get("required_approvals", []), "required_approvals")
        verification = self._string_list(raw.get("verification_plan", []), "verification_plan")
        if not verification:
            raise PlanValidationError("verification_plan must contain at least one check.")
        assumptions = self._string_list(raw.get("assumptions", []), "assumptions")
        affected_files = self._string_list(raw.get("affected_files", []), "affected_files")
        proposed_changes = self._string_list(raw.get("proposed_changes", []), "proposed_changes")
        dependencies = self._string_list(raw.get("dependencies", []), "dependencies")
        plan_tests = self._string_list(raw.get("tests", []), "tests")
        known_files = (
            (set(snapshot.tree) | set(snapshot.tests) | set(snapshot.configs) | set(snapshot.deployment_files))
            if snapshot is not None else set()
        )
        for value in plan_tests:
            self._validate_relative_path(value, "tests")
            if snapshot is not None and value not in known_files:
                raise PlanValidationError(f"tests references a file not found by repository inspection: {value}.")
        for value in affected_files:
            self._validate_relative_path(value, "affected_files")
        if snapshot is not None:
            new_files = {
                file_name
                for step in steps
                if step.operation == EngineeringOperation.CREATE_FILE.value
                for file_name in step.files
            }
            unknown_affected = [
                value for value in affected_files
                if value not in known_files and value not in new_files
            ]
            if unknown_affected:
                raise PlanValidationError(
                    "affected_files references files not found by repository inspection: "
                    + ", ".join(unknown_affected)
                    + "."
                )
        risk = str(raw.get("risk", "low")).strip().lower()
        if risk not in {item.value for item in RiskLevel}:
            raise PlanValidationError("risk is invalid.")
        risk_order = {item.value: index for index, item in enumerate(RiskLevel)}
        highest_step_risk = max((risk_order[step.risk] for step in steps), default=0)
        if risk_order[risk] < highest_step_risk:
            raise PlanValidationError("Plan risk must cover the risk of every step.")
        rollback_strategy = raw.get("rollback_strategy", "Remove the isolated worktree without changing production.")
        if not isinstance(rollback_strategy, str) or not rollback_strategy.strip():
            raise PlanValidationError("rollback_strategy must be a non-empty string.")
        approval_steps = {step.id for step in steps if step.requires_approval}
        if approval_steps and not approvals:
            raise PlanValidationError("required_approvals must name the approval boundary for mutating steps.")
        return EngineeringPlan(
            goal=goal.strip(),
            analysis=analysis.strip(),
            steps=steps,
            required_approvals=approvals,
            verification_plan=verification,
            source=source,
            version=version,
            assumptions=assumptions,
            affected_files=affected_files,
            proposed_changes=proposed_changes,
            dependencies=dependencies,
            tests=plan_tests,
            risk=risk,
            rollback_strategy=rollback_strategy.strip(),
        )

    def parse_json(
        self,
        value: str,
        *,
        source: str = "generated",
        snapshot: Optional[RepositorySnapshot] = None,
    ) -> EngineeringPlan:
        try:
            raw = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PlanValidationError(f"Plan is not valid JSON: {exc}") from exc
        return self.validate(raw, source=source, snapshot=snapshot)

    def fallback(self, goal: str, analysis: CapabilityAnalysis, snapshot: RepositorySnapshot) -> EngineeringPlan:
        """Produce a bounded, read/test-only plan when provider output is invalid."""
        steps = [
            EngineeringStep(
                id="inspect-repository",
                description="Inspect the bounded repository snapshot and confirm affected subsystems.",
                operation=EngineeringOperation.INSPECT_REPOSITORY.value,
                risk=RiskLevel.LOW.value,
            )
        ]
        for index, capability in enumerate(analysis.missing_capabilities[: self.max_steps - 2], start=1):
            steps.append(
                EngineeringStep(
                    id=f"analyze-{index}-{self._slug(capability)}",
                    description=f"Define the smallest implementation boundary for the missing {capability} capability.",
                    operation=EngineeringOperation.INSPECT_REPOSITORY.value,
                    dependencies=[steps[-1].id],
                    risk=RiskLevel.LOW.value,
                )
            )
        steps.append(
            EngineeringStep(
                id="baseline-tests",
                description="Run the existing regression suite before any code modification.",
                operation=EngineeringOperation.RUN_TEST.value,
                dependencies=[steps[-1].id],
                tests=snapshot.tests[:20],
                risk=RiskLevel.LOW.value,
            )
        )
        raw = {
            "goal": goal,
            "analysis": "Provider output was unavailable or invalid; generated a read/test-only recovery plan.",
            "steps": [step.to_dict() for step in steps],
            "required_approvals": [],
            "verification_plan": ["All existing tests pass", "No protected path is modified"],
            "tests": snapshot.tests[:20],
            "version": self.PLAN_VERSION,
        }
        return self.validate(raw, source="deterministic_fallback", snapshot=snapshot)

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug[:32] or "capability"
