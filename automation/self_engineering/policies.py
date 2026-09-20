"""Policy and path boundaries for autonomous engineering operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from automation.permission_manager import PermissionManager


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EngineeringOperation(str, Enum):
    INSPECT_REPOSITORY = "inspect_repository"
    READ_SOURCE = "read_source"
    CREATE_FILE = "create_file"
    MODIFY_FILE = "modify_file"
    RENAME_FILE = "rename_file"
    DELETE_FILE = "delete_file"
    RUN_TEST = "run_test"
    RUN_STATIC_CHECK = "run_static_check"
    INSPECT_DIFF = "inspect_diff"
    CREATE_BRANCH = "create_branch"
    CREATE_COMMIT = "create_commit"
    ROLLBACK = "rollback"
    SANDBOX_DEPLOY = "sandbox_deploy"
    VERIFY_DEPLOYMENT = "verify_deployment"
    RECORD_MEMORY = "record_memory"
    PRODUCTION_DEPLOY = "production_deploy"


class ApprovalRequired(PermissionError):
    """Raised when a mutating operation lacks explicit human approval."""


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    risk: RiskLevel
    reason: str


class EngineeringPolicy:
    """Central allowlist for self-engineering operations.

    The existing :class:`PermissionManager` remains the final user policy
    source.  Unknown ``self_engineering`` actions therefore default to
    confirmation in the existing manager; this class adds operation and path
    restrictions around it.
    """

    RISK_BY_OPERATION = {
        EngineeringOperation.INSPECT_REPOSITORY.value: RiskLevel.LOW,
        EngineeringOperation.READ_SOURCE.value: RiskLevel.LOW,
        EngineeringOperation.RUN_TEST.value: RiskLevel.LOW,
        EngineeringOperation.RUN_STATIC_CHECK.value: RiskLevel.LOW,
        EngineeringOperation.INSPECT_DIFF.value: RiskLevel.LOW,
        EngineeringOperation.RECORD_MEMORY.value: RiskLevel.LOW,
        EngineeringOperation.CREATE_FILE.value: RiskLevel.MEDIUM,
        EngineeringOperation.MODIFY_FILE.value: RiskLevel.MEDIUM,
        EngineeringOperation.RENAME_FILE.value: RiskLevel.MEDIUM,
        EngineeringOperation.DELETE_FILE.value: RiskLevel.MEDIUM,
        EngineeringOperation.CREATE_BRANCH.value: RiskLevel.MEDIUM,
        EngineeringOperation.SANDBOX_DEPLOY.value: RiskLevel.MEDIUM,
        EngineeringOperation.VERIFY_DEPLOYMENT.value: RiskLevel.LOW,
        EngineeringOperation.CREATE_COMMIT.value: RiskLevel.HIGH,
        EngineeringOperation.ROLLBACK.value: RiskLevel.HIGH,
        EngineeringOperation.PRODUCTION_DEPLOY.value: RiskLevel.CRITICAL,
    }

    PROTECTED_PARTS = {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".env",
        ".env.local",
        ".env.production",
        "config/api_keys.json",
        "cloud/tasks.json",
        "cloud/devices.json",
        "cloud/activity.json",
        "automation/learning_memory.json",
        "automation/permissions.json",
        "agent/mac_agent_outbox.json",
        "memory/long_term.json",
    }

    def __init__(
        self,
        workspace_root: Path,
        permission_manager: Optional[PermissionManager] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.permission_manager = permission_manager or PermissionManager()

    def resolve_path(self, path: str | Path, *, allow_missing: bool = True) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.workspace_root / candidate).resolve()

        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError("Target path is outside the approved workspace.") from exc

        if resolved.exists() and resolved.is_symlink():
            raise PermissionError("Symlink targets are not allowed for engineering changes.")
        if not allow_missing and not resolved.exists():
            raise FileNotFoundError(str(path))
        return resolved

    def relative_path(self, path: str | Path) -> str:
        resolved = self.resolve_path(path)
        return resolved.relative_to(self.workspace_root).as_posix()

    def is_protected(self, path: str | Path) -> bool:
        relative = self.relative_path(path)
        parts = Path(relative).parts
        if any(part in {".git", ".venv", "venv", "env", "__pycache__"} for part in parts):
            return True
        if relative in self.PROTECTED_PARTS:
            return True
        if Path(relative).name.startswith(".env") and Path(relative).name != ".env.example":
            return True
        name = Path(relative).name.lower()
        if any(marker in name for marker in ("credential", "client_secret", "api_key", "token")):
            return True
        if name.endswith((".pem", ".key")):
            return True
        if relative.startswith("uploads/"):
            return True
        return False

    def decision(
        self,
        operation: str | EngineeringOperation,
        target: str | Path | None = None,
        *,
        approved: bool = False,
    ) -> PolicyDecision:
        operation_name = str(operation.value if isinstance(operation, EngineeringOperation) else operation)
        risk = self.RISK_BY_OPERATION.get(operation_name)
        if risk is None:
            return PolicyDecision(False, False, RiskLevel.CRITICAL, "Operation is not on the engineering allowlist.")

        if target is not None and self.is_protected(target):
            return PolicyDecision(False, False, risk, "Target path is protected from autonomous modification.")

        if operation_name == EngineeringOperation.PRODUCTION_DEPLOY.value:
            return PolicyDecision(False, True, risk, "Production deployment requires a separate explicit approval flow.")

        permission = self.permission_manager.check(
            "self_engineering",
            {"action": operation_name, "path": str(target or "")},
        )
        if not permission.allowed:
            return PolicyDecision(False, False, risk, permission.reason)

        requires = risk != RiskLevel.LOW or permission.requires_confirmation
        if requires and not approved:
            return PolicyDecision(True, True, risk, "Human approval is required before this operation.")
        return PolicyDecision(True, False, risk, "Operation is allowed by the engineering policy.")

    def require(
        self,
        operation: str | EngineeringOperation,
        target: str | Path | None = None,
        *,
        approved: bool = False,
    ) -> PolicyDecision:
        decision = self.decision(operation, target, approved=approved)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        if decision.requires_approval:
            raise ApprovalRequired(decision.reason)
        return decision
