from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set


class ExecutionMode(str, Enum):
    SEQUENTIAL = "SEQUENTIAL"
    PARALLEL_SAFE = "PARALLEL_SAFE"
    WAITING_PERMISSION = "WAITING_PERMISSION"
    BLOCKED = "BLOCKED"
    RECOVERY = "RECOVERY"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class CoordinationResult:
    status: str
    execution_mode: ExecutionMode
    ready_objectives: List[str] = field(default_factory=list)
    waiting_objectives: List[str] = field(default_factory=list)
    permission_waiting: List[str] = field(default_factory=list)
    blocked_objectives: List[str] = field(default_factory=list)
    completed_objectives: List[str] = field(default_factory=list)
    next_objective: Optional[str] = None
    safe_parallel_objectives: List[str] = field(default_factory=list)
    reason: str = ""


class ExecutionCoordinator:
    """Thin, side-effect-free coordinator for already-validated local execution.

    The coordinator is intentionally limited to dependency-aware scheduling. It does
    not execute actions or replace the runtime, workflow engine, permission manager,
    action registry, or recovery authority.
    """

    UNSAFE_ACTIONS = {
        "send_message",
        "computer_control",
        "computer_settings",
        "browser_control",
        "delete_file",
        "destroy_resource",
        "deploy_jarvis",
        "restart_service",
        "write_deployment_config",
    }

    def __init__(
        self,
        *,
        action_registry: Any = None,
        permissions: Any = None,
        learning_memory: Any = None,
        logger: Any = print,
        recovery_delegate: Any = None,
        allow_parallel: bool = False,
    ) -> None:
        self.action_registry = action_registry
        self.permissions = permissions
        self.learning_memory = learning_memory
        self.logger = logger
        self.recovery_delegate = recovery_delegate
        self.allow_parallel = allow_parallel

    def _normalize_objective_ids(self, objective_ids: Iterable[str]) -> List[str]:
        seen: Set[str] = set()
        ordered: List[str] = []
        for item in objective_ids or []:
            value = str(item)
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered

    def _safe_action_name(self, action_name: Optional[str]) -> str:
        return str(action_name or "").strip()

    def _is_parallel_safe(self, action_name: str) -> bool:
        if not action_name:
            return False
        lowered = action_name.lower()
        if any(marker in lowered for marker in ("send", "delete", "destroy", "deploy", "restart", "write", "modify", "control", "settings")):
            return False
        return True

    def _permission_decision(self, action_name: str) -> Dict[str, Any]:
        if self.permissions is None:
            return {"allowed": True, "requires_confirmation": False, "reason": "no permission manager"}
        try:
            decision = self.permissions.check(action_name, {})
            if isinstance(decision, dict):
                return {
                    "allowed": bool(decision.get("allowed", True)),
                    "requires_confirmation": bool(decision.get("requires_confirmation", False)),
                    "reason": str(decision.get("reason") or "ok"),
                }
        except Exception:
            pass
        return {"allowed": True, "requires_confirmation": False, "reason": "permission check unavailable"}

    def coordinate(
        self,
        *,
        objective_ids: Iterable[str],
        completed: Iterable[str] = (),
        dependencies: Optional[Dict[str, List[str]]] = None,
        action_map: Optional[Dict[str, str]] = None,
        allow_parallel: Optional[bool] = None,
    ) -> CoordinationResult:
        objective_ids = self._normalize_objective_ids(objective_ids)
        completed_set = {str(item) for item in completed or []}
        dependencies = dependencies or {}
        action_map = action_map or {}

        if not objective_ids:
            return CoordinationResult(
                status="COMPLETE",
                execution_mode=ExecutionMode.COMPLETE,
                completed_objectives=sorted(completed_set),
                reason="No objectives to coordinate.",
            )

        remaining = [obj for obj in objective_ids if obj not in completed_set]
        if not remaining:
            return CoordinationResult(
                status="COMPLETE",
                execution_mode=ExecutionMode.COMPLETE,
                completed_objectives=sorted(completed_set),
                ready_objectives=[],
                next_objective=None,
                reason="All objectives are complete.",
            )

        ready: List[str] = []
        waiting: List[str] = []
        blocked: List[str] = []
        permission_waiting: List[str] = []

        for objective in remaining:
            dep_list = [str(dep) for dep in list(dependencies.get(objective, []) or [])]
            unknown_deps = [dep for dep in dep_list if dep not in objective_ids]
            if unknown_deps:
                blocked.append(objective)
                continue
            if any(dep not in completed_set for dep in dep_list):
                waiting.append(objective)
                continue

            action_name = self._safe_action_name(action_map.get(objective))
            if action_name:
                decision = self._permission_decision(action_name)
                if not decision.get("allowed", True) or decision.get("requires_confirmation"):
                    permission_waiting.append(objective)
                    continue

            if self.action_registry is not None and action_name and not self.action_registry.has(action_name):
                blocked.append(objective)
                continue

            ready.append(objective)

        if blocked:
            return CoordinationResult(
                status="BLOCKED",
                execution_mode=ExecutionMode.BLOCKED,
                completed_objectives=sorted(completed_set),
                ready_objectives=sorted(ready),
                waiting_objectives=sorted(waiting),
                permission_waiting=sorted(permission_waiting),
                blocked_objectives=sorted(blocked),
                next_objective=sorted(ready)[0] if ready else None,
                reason="One or more objectives are blocked by missing dependencies or unsupported actions.",
            )

        if not ready and permission_waiting:
            return CoordinationResult(
                status="WAITING_PERMISSION",
                execution_mode=ExecutionMode.WAITING_PERMISSION,
                completed_objectives=sorted(completed_set),
                ready_objectives=[],
                waiting_objectives=sorted(waiting),
                permission_waiting=sorted(permission_waiting),
                blocked_objectives=sorted(blocked),
                next_objective=sorted(permission_waiting)[0],
                reason="Permission is required before one or more objectives may proceed.",
            )

        mode = ExecutionMode.SEQUENTIAL
        if allow_parallel is None:
            allow_parallel = self.allow_parallel
        if allow_parallel and len(ready) > 1 and all(self._is_parallel_safe(self._safe_action_name(action_map.get(obj))) for obj in ready):
            mode = ExecutionMode.PARALLEL_SAFE
        elif ready and len(ready) > 1 and all(self._is_parallel_safe(self._safe_action_name(action_map.get(obj))) for obj in ready):
            mode = ExecutionMode.PARALLEL_SAFE

        next_objective = ready[0] if ready else None
        safe_parallel = [obj for obj in ready if self._is_parallel_safe(self._safe_action_name(action_map.get(obj)))] if ready else []

        return CoordinationResult(
            status="READY" if ready else "WAITING_PERMISSION",
            execution_mode=mode,
            ready_objectives=sorted(ready),
            waiting_objectives=sorted(waiting),
            permission_waiting=sorted(permission_waiting),
            blocked_objectives=sorted(blocked),
            completed_objectives=sorted(completed_set),
            next_objective=next_objective,
            safe_parallel_objectives=safe_parallel,
            reason="Ready to schedule the next objective." if ready else "No safe objective is ready for execution.",
        )


__all__ = ["ExecutionCoordinator", "ExecutionMode", "CoordinationResult"]
