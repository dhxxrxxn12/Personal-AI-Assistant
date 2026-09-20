"""
Smart Permission Manager for JARVIS.

Controls which autonomous actions JARVIS may execute automatically.

Permission levels:

SAFE
    Execute automatically.

CONFIRM
    Ask the user before execution.

BLOCKED
    Never execute automatically.

The manager supports both:
    action
    action + sub-action

Examples:

    open_app                       -> SAFE
    web_search                     -> SAFE
    infrastructure:cloud_status   -> SAFE
    infrastructure:deploy_jarvis  -> CONFIRM
    infrastructure:destroy_resource -> BLOCKED
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional
import json

from core.runtime_config import migrate_legacy_file, runtime_path


BASE_DIR = Path(__file__).resolve().parent.parent

_LEGACY_PERMISSION_FILE = BASE_DIR / "automation" / "permissions.json"
PERMISSION_FILE = runtime_path("permissions.json", _LEGACY_PERMISSION_FILE)
migrate_legacy_file(PERMISSION_FILE, _LEGACY_PERMISSION_FILE)


class PermissionLevel(str, Enum):
    SAFE = "safe"
    CONFIRM = "confirm"
    BLOCKED = "blocked"


@dataclass
class PermissionDecision:
    allowed: bool
    requires_confirmation: bool
    reason: str


class PermissionManager:
    """
    Central permission system for autonomous JARVIS.

    Permissions can be defined for:

        action

    or:

        action:sub_action
    """

    # ==========================================================
    # DEFAULT SAFE ACTIONS
    # ==========================================================

    DEFAULT_SAFE_ACTIONS = {
        "open_app",
        "weather_report",
        "web_search",
        "youtube_video",
        "flight_finder",
        "reminder",
        "file_processor",
    }

    # ==========================================================
    # DEFAULT CONFIRM ACTIONS
    # ==========================================================

    DEFAULT_CONFIRM_ACTIONS = {
        "browser_control",
        "computer_control",
        "computer_settings",
        "desktop_control",
        "code_helper",
        "dev_agent",
        "file_controller",
        "game_updater",
        "send_message",
    }

    # ==========================================================
    # INFRASTRUCTURE SAFE SUB-ACTIONS
    # ==========================================================

    DEFAULT_INFRASTRUCTURE_SAFE = {
        "cloud_status",
        "list_cloud_providers",
        "select_cloud_provider",
        "deployment_status",
        "prepare_deployment",
        "create_deployment_plan",
        "generate_deployment_config",
    }

    # ==========================================================
    # INFRASTRUCTURE CONFIRM SUB-ACTIONS
    # ==========================================================

    DEFAULT_INFRASTRUCTURE_CONFIRM = {
        "write_deployment_config",
        "deploy_jarvis",
        "update_jarvis",
        "restart_service",
    }

    # ==========================================================
    # INFRASTRUCTURE BLOCKED SUB-ACTIONS
    # ==========================================================

    DEFAULT_INFRASTRUCTURE_BLOCKED = {
        "destroy_resource",
    }

    # ==========================================================
    # INIT
    # ==========================================================

    def __init__(
        self,
        path: Path = PERMISSION_FILE,
    ):

        self.path = path

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Unknown actions remain CONFIRM.
        self.default_level = (
            PermissionLevel.CONFIRM
        )

        self.permissions: Dict[str, str] = {}

        self._load()

    # ==========================================================
    # STORAGE
    # ==========================================================

    def _load(self) -> None:

        if not self.path.exists():

            self._save()

            return

        try:

            with self.path.open(
                "r",
                encoding="utf-8",
            ) as f:

                data = json.load(f)

            if isinstance(data, dict):

                self.permissions = {
                    str(k): str(v)
                    for k, v in data.items()
                }

        except Exception:

            # Never crash JARVIS because of a corrupted
            # permissions file.
            self.permissions = {}

    def _save(self) -> None:

        with self.path.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                self.permissions,
                f,
                indent=2,
                ensure_ascii=False,
            )

    # ==========================================================
    # DEFAULT POLICY
    # ==========================================================

    def _default_permission(
        self,
        action: str,
        parameters: Optional[
            Dict[str, Any]
        ] = None,
    ) -> PermissionLevel:

        parameters = (
            parameters
            or {}
        )

        # ------------------------------------------------------
        # Infrastructure
        # ------------------------------------------------------

        if action == "infrastructure":

            sub_action = parameters.get(
                "action"
            )

            if sub_action in (
                self.DEFAULT_INFRASTRUCTURE_SAFE
            ):
                return PermissionLevel.SAFE

            if sub_action in (
                self.DEFAULT_INFRASTRUCTURE_BLOCKED
            ):
                return PermissionLevel.BLOCKED

            if sub_action in (
                self.DEFAULT_INFRASTRUCTURE_CONFIRM
            ):
                return PermissionLevel.CONFIRM

            # Unknown infrastructure operation
            # must require confirmation.
            return PermissionLevel.CONFIRM

        # ------------------------------------------------------
        # Normal actions
        # ------------------------------------------------------

        if action in (
            self.DEFAULT_SAFE_ACTIONS
        ):
            return PermissionLevel.SAFE

        if action in (
            self.DEFAULT_CONFIRM_ACTIONS
        ):
            return PermissionLevel.CONFIRM

        # Unknown actions are never automatically trusted.
        return self.default_level

    # ==========================================================
    # PERMISSION CONFIGURATION
    # ==========================================================

    def set_permission(
        self,
        action: str,
        level: PermissionLevel | str,
    ) -> None:

        if isinstance(
            level,
            PermissionLevel,
        ):
            level = level.value

        level = str(level).lower()

        valid_levels = {
            PermissionLevel.SAFE.value,
            PermissionLevel.CONFIRM.value,
            PermissionLevel.BLOCKED.value,
        }

        if level not in valid_levels:

            raise ValueError(
                f"Invalid permission level: {level}"
            )

        self.permissions[
            action
        ] = level

        self._save()

    def remove_permission(
        self,
        action: str,
    ) -> None:

        self.permissions.pop(
            action,
            None,
        )

        self._save()

    # ==========================================================
    # GET PERMISSION
    # ==========================================================

    def get_permission(
        self,
        action: str,
        parameters: Optional[
            Dict[str, Any]
        ] = None,
    ) -> PermissionLevel:

        # Explicit user configuration always wins.
        if action in self.permissions:

            level = self.permissions[
                action
            ]

            try:

                return PermissionLevel(
                    level
                )

            except ValueError:

                return (
                    self.default_level
                )

        # Otherwise use smart defaults.
        return self._default_permission(
            action,
            parameters,
        )

    # ==========================================================
    # DECISION
    # ==========================================================

    def check(
        self,
        action: str,
        parameters: Optional[
            Dict[str, Any]
        ] = None,
    ) -> PermissionDecision:

        parameters = (
            parameters
            or {}
        )

        level = self.get_permission(
            action,
            parameters,
        )

        # ------------------------------------------------------
        # SAFE
        # ------------------------------------------------------

        if level == PermissionLevel.SAFE:

            if action == "infrastructure":

                sub_action = parameters.get(
                    "action",
                    "unknown",
                )

                return PermissionDecision(
                    allowed=True,
                    requires_confirmation=False,
                    reason=(
                        "infrastructure:"
                        f"{sub_action} is safe."
                    ),
                )

            return PermissionDecision(
                allowed=True,
                requires_confirmation=False,
                reason=(
                    f"{action} is marked safe."
                ),
            )

        # ------------------------------------------------------
        # BLOCKED
        # ------------------------------------------------------

        if level == PermissionLevel.BLOCKED:

            if action == "infrastructure":

                sub_action = parameters.get(
                    "action",
                    "unknown",
                )

                return PermissionDecision(
                    allowed=False,
                    requires_confirmation=False,
                    reason=(
                        "infrastructure:"
                        f"{sub_action} is blocked."
                    ),
                )

            return PermissionDecision(
                allowed=False,
                requires_confirmation=False,
                reason=(
                    f"{action} is blocked "
                    "by user policy."
                ),
            )

        # ------------------------------------------------------
        # CONFIRM
        # ------------------------------------------------------

        if action == "infrastructure":

            sub_action = parameters.get(
                "action",
                "unknown",
            )

            return PermissionDecision(
                allowed=True,
                requires_confirmation=True,
                reason=(
                    "User confirmation required for "
                    f"infrastructure:{sub_action}."
                ),
            )

        return PermissionDecision(
            allowed=True,
            requires_confirmation=True,
            reason=(
                f"{action} requires "
                "user confirmation."
            ),
        )

    # ==========================================================
    # CONVENIENCE METHODS
    # ==========================================================

    def allow_automatically(
        self,
        action: str,
    ) -> None:

        self.set_permission(
            action,
            PermissionLevel.SAFE,
        )

    def require_confirmation(
        self,
        action: str,
    ) -> None:

        self.set_permission(
            action,
            PermissionLevel.CONFIRM,
        )

    def block(
        self,
        action: str,
    ) -> None:

        self.set_permission(
            action,
            PermissionLevel.BLOCKED,
        )

    # ==========================================================
    # RESET
    # ==========================================================

    def reset(
        self,
    ) -> None:

        self.permissions = {}

        self._save()

    # ==========================================================
    # STATUS
    # ==========================================================

    def status(
        self,
    ) -> Dict[str, Any]:

        safe = []
        confirmation = []
        blocked = []

        # Explicit permissions.
        for name, level in (
            self.permissions.items()
        ):

            if level == (
                PermissionLevel.SAFE.value
            ):
                safe.append(name)

            elif level == (
                PermissionLevel.CONFIRM.value
            ):
                confirmation.append(name)

            elif level == (
                PermissionLevel.BLOCKED.value
            ):
                blocked.append(name)

        return {
            "default_permission": (
                self.default_level.value
            ),

            "configured_actions": len(
                self.permissions
            ),

            "safe": sorted(safe),

            "confirmation_required": sorted(
                confirmation
            ),

            "blocked": sorted(
                blocked
            ),

            "default_safe_actions": sorted(
                self.DEFAULT_SAFE_ACTIONS
            ),

            "default_confirm_actions": sorted(
                self.DEFAULT_CONFIRM_ACTIONS
            ),

            "infrastructure_safe": sorted(
                self.DEFAULT_INFRASTRUCTURE_SAFE
            ),

            "infrastructure_confirmation": sorted(
                self.DEFAULT_INFRASTRUCTURE_CONFIRM
            ),

            "infrastructure_blocked": sorted(
                self.DEFAULT_INFRASTRUCTURE_BLOCKED
            ),
        }


# ==============================================================
# DIRECT TEST
# ==============================================================

if __name__ == "__main__":

    manager = PermissionManager()

    print(
        "PermissionManager loaded successfully."
    )

    print()

    tests = [
        (
            "open_app",
            {},
        ),
        (
            "web_search",
            {},
        ),
        (
            "infrastructure",
            {
                "action": "cloud_status"
            },
        ),
        (
            "infrastructure",
            {
                "action": "prepare_deployment"
            },
        ),
        (
            "infrastructure",
            {
                "action": "deploy_jarvis"
            },
        ),
        (
            "infrastructure",
            {
                "action": "destroy_resource"
            },
        ),
    ]

    for action, parameters in tests:

        decision = manager.check(
            action,
            parameters,
        )

        print(
            f"{action} "
            f"{parameters} -> "
            f"{decision}"
        )

    print()
    print(
        manager.status()
    )
