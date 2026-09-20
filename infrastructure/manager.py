from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from infrastructure.cloud_provider import (
    CloudProvider,
    LocalCloudProvider,
)

from infrastructure.google_cloud import (
    GoogleCloudProvider,
)


@dataclass
class InfrastructureAction:
    name: str
    description: str
    requires_confirmation: bool
    handler: Callable[..., Any]


class InfrastructureManager:
    """
    Controlled self-management layer for JARVIS.

    Supports multiple cloud providers while keeping
    dangerous infrastructure operations behind an
    explicit confirmation gate.
    """

    def __init__(
        self,
        logger=print,
        provider: CloudProvider | None = None,
    ):
        self.logger = logger

        # -----------------------------------------------------
        # AVAILABLE PROVIDERS
        # -----------------------------------------------------

        self.providers: dict[str, CloudProvider] = {
            "local": LocalCloudProvider(),

            "google_cloud": GoogleCloudProvider(
                project_id="jarvis-cloud",
                region="asia-south1",
            ),
        }

        # Local remains the default.
        self.provider = (
            provider
            if provider is not None
            else self.providers["local"]
        )

        # -----------------------------------------------------
        # REGISTER ACTIONS
        # -----------------------------------------------------

        self.actions: dict[
            str,
            InfrastructureAction,
        ] = {}

        self._register_default_actions()

    # =========================================================
    # ACTION REGISTRATION
    # =========================================================

    def register(
        self,
        name: str,
        description: str,
        handler: Callable[..., Any],
        requires_confirmation: bool = True,
    ):
        self.actions[name] = InfrastructureAction(
            name=name,
            description=description,
            requires_confirmation=requires_confirmation,
            handler=handler,
        )

    # =========================================================
    # DEFAULT ACTIONS
    # =========================================================

    def _register_default_actions(self):

        self.register(
            name="cloud_status",
            description=(
                "Check the status of the current "
                "JARVIS cloud provider."
            ),
            handler=self.cloud_status,
            requires_confirmation=False,
        )

        self.register(
            name="list_cloud_providers",
            description=(
                "List cloud providers available "
                "to JARVIS."
            ),
            handler=self.list_cloud_providers,
            requires_confirmation=False,
        )

        self.register(
            name="select_cloud_provider",
            description=(
                "Select the cloud provider JARVIS "
                "will use."
            ),
            handler=self.select_cloud_provider,
            requires_confirmation=False,
        )

        self.register(
            name="deployment_status",
            description=(
                "Check the current JARVIS "
                "deployment status."
            ),
            handler=self.deployment_status,
            requires_confirmation=False,
        )

        self.register(
            name="prepare_deployment",
            description=(
                "Prepare JARVIS for cloud deployment."
            ),
            handler=self.prepare_deployment,
            requires_confirmation=False,
        )

        self.register(
            name="deploy_jarvis",
            description=(
                "Deploy JARVIS to the configured "
                "cloud provider."
            ),
            handler=self.deploy_jarvis,
            requires_confirmation=True,
        )

        self.register(
            name="update_jarvis",
            description=(
                "Update the deployed JARVIS application."
            ),
            handler=self.update_jarvis,
            requires_confirmation=True,
        )

        self.register(
            name="restart_service",
            description=(
                "Restart an approved JARVIS service."
            ),
            handler=self.restart_service,
            requires_confirmation=True,
        )

        self.register(
            name="destroy_resource",
            description=(
                "Destroy an infrastructure resource."
            ),
            handler=self.destroy_resource,
            requires_confirmation=True,
        )

    # =========================================================
    # ACTION INFORMATION
    # =========================================================

    def list_actions(self):

        return [
            {
                "name": action.name,
                "description": action.description,
                "requires_confirmation": (
                    action.requires_confirmation
                ),
            }
            for action in self.actions.values()
        ]

    def has_action(
        self,
        name: str,
    ) -> bool:

        return name in self.actions

    # =========================================================
    # CLOUD PROVIDERS
    # =========================================================

    def list_cloud_providers(self):

        return {
            "current": self.provider.name,
            "providers": [
                {
                    "name": name,
                    "status": provider.status(),
                }
                for name, provider
                in self.providers.items()
            ],
        }

    def select_cloud_provider(
        self,
        provider: str,
    ):

        if provider not in self.providers:

            raise ValueError(
                f"Unknown cloud provider: {provider}"
            )

        self.provider = self.providers[
            provider
        ]

        self.logger(
            "[Infrastructure] "
            f"Provider selected: {provider}"
        )

        return {
            "success": True,
            "provider": provider,
            "status": self.provider.status(),
        }

    def set_provider(
        self,
        provider: CloudProvider,
    ):

        self.provider = provider

        self.logger(
            "[Infrastructure] "
            f"Provider changed to: "
            f"{provider.name}"
        )

    def provider_status(self):

        return self.provider.status()

    # =========================================================
    # EXECUTION
    # =========================================================

    def execute(
        self,
        name: str,
        parameters: dict | None = None,
        confirmed: bool = False,
    ):

        parameters = parameters or {}

        if name not in self.actions:

            raise ValueError(
                f"Unknown infrastructure action: {name}"
            )

        action = self.actions[name]

        # -----------------------------------------------------
        # CONFIRMATION GATE
        # -----------------------------------------------------

        if (
            action.requires_confirmation
            and not confirmed
        ):

            return {
                "success": False,
                "status": "confirmation_required",
                "action": name,
                "message": (
                    f"'{name}' requires your "
                    "explicit confirmation."
                ),
            }

        self.logger(
            "[Infrastructure] "
            f"Executing: {name}"
        )

        try:

            result = action.handler(
                **parameters
            )

            return {
                "success": True,
                "status": "completed",
                "action": name,
                "result": result,
            }

        except Exception as e:

            self.logger(
                "[Infrastructure] "
                f"Failed: {e}"
            )

            return {
                "success": False,
                "status": "failed",
                "action": name,
                "error": str(e),
            }

    # =========================================================
    # STATUS
    # =========================================================

    def cloud_status(self):

        return self.provider.status()

    def deployment_status(self):

        return {
            "provider": self.provider.name,
            "deployment": "not_deployed",
            "message": (
                "JARVIS has not been deployed "
                "through this infrastructure manager."
            ),
        }

    # =========================================================
    # DEPLOYMENT
    # =========================================================

    def prepare_deployment(self):

        config = {
            "application": "JARVIS",
            "runtime": "python",
            "api": "FastAPI",
            "provider": self.provider.name,
        }

        return self.provider.prepare(
            config
        )

    def deploy_jarvis(self):

        config = {
            "application": "JARVIS",
            "runtime": "python",
            "api": "FastAPI",
        }

        return self.provider.deploy(
            config
        )

    # =========================================================
    # MAINTENANCE
    # =========================================================

    def update_jarvis(self):

        return {
            "status": "prepared",
            "message": (
                "JARVIS update workflow is ready "
                "for provider integration."
            ),
        }

    def restart_service(
        self,
        service: str = "jarvis",
    ):

        return self.provider.restart(
            service
        )

    # =========================================================
    # DESTRUCTIVE OPERATIONS
    # =========================================================

    def destroy_resource(
        self,
        resource: str,
    ):

        return self.provider.destroy(
            resource
        )
