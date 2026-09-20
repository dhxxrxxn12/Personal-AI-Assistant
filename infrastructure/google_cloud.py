from __future__ import annotations

from typing import Any

from infrastructure.cloud_provider import CloudProvider


class GoogleCloudProvider(CloudProvider):
    """
    Google Cloud provider for JARVIS.

    Current version is intentionally SAFE:
    it only prepares/checks configuration.

    No VM, Cloud Run service, billing change,
    or other paid resource is created here.
    """

    name = "google_cloud"

    def __init__(
        self,
        project_id: str | None = None,
        region: str = "asia-south1",
    ):
        self.project_id = project_id
        self.region = region

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "connected": bool(self.project_id),
            "project_id": self.project_id,
            "region": self.region,
            "deployment_enabled": False,
            "message": (
                "Google Cloud provider is configured "
                "for planning only."
            ),
        }

    def prepare(
        self,
        config: dict[str, Any],
    ) -> dict[str, Any]:

        return {
            "provider": self.name,
            "status": "prepared",
            "project_id": self.project_id,
            "region": self.region,
            "service": "Cloud Run",
            "config": config,
            "deployment_started": False,
            "message": (
                "Cloud Run deployment configuration "
                "prepared. No resources were created."
            ),
        }

    def deploy(
        self,
        config: dict[str, Any],
    ) -> dict[str, Any]:

        return {
            "provider": self.name,
            "status": "blocked",
            "deployment_started": False,
            "message": (
                "Real Google Cloud deployment is "
                "disabled until cloud credentials, "
                "billing, and explicit deployment "
                "approval are configured."
            ),
            "config": config,
        }

    def restart(
        self,
        service: str,
    ) -> dict[str, Any]:

        return {
            "provider": self.name,
            "status": "blocked",
            "service": service,
            "message": (
                "Google Cloud service restart is "
                "not enabled yet."
            ),
        }

    def destroy(
        self,
        resource: str,
    ) -> dict[str, Any]:

        return {
            "provider": self.name,
            "status": "blocked",
            "resource": resource,
            "message": (
                "Resource destruction is disabled."
            ),
        }
