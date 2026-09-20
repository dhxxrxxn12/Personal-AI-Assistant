from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CloudProvider(ABC):
    """
    Base interface for JARVIS cloud providers.

    This layer defines WHAT JARVIS can ask a provider to do.
    It does not implement any provider-specific operations yet.
    """

    name = "unknown"

    @abstractmethod
    def status(self) -> dict[str, Any]:
        """Return provider/account status."""
        raise NotImplementedError

    @abstractmethod
    def prepare(self, config: dict[str, Any]) -> dict[str, Any]:
        """Prepare infrastructure configuration."""
        raise NotImplementedError

    @abstractmethod
    def deploy(self, config: dict[str, Any]) -> dict[str, Any]:
        """Deploy an approved JARVIS instance."""
        raise NotImplementedError

    @abstractmethod
    def restart(self, service: str) -> dict[str, Any]:
        """Restart an approved service."""
        raise NotImplementedError

    @abstractmethod
    def destroy(self, resource: str) -> dict[str, Any]:
        """Destroy an approved resource."""
        raise NotImplementedError


class LocalCloudProvider(CloudProvider):
    """
    Safe local provider used for development/testing.

    It performs NO real cloud operations.
    """

    name = "local"

    def status(self):
        return {
            "provider": self.name,
            "connected": True,
            "runtime": "local",
        }

    def prepare(self, config):
        return {
            "provider": self.name,
            "status": "prepared",
            "config": config,
        }

    def deploy(self, config):
        return {
            "provider": self.name,
            "status": "simulation",
            "message": (
                "Deployment simulated locally. "
                "No cloud resources were created."
            ),
            "config": config,
        }

    def restart(self, service):
        return {
            "provider": self.name,
            "status": "simulation",
            "message": f"Restart simulated for '{service}'.",
        }

    def destroy(self, resource):
        return {
            "provider": self.name,
            "status": "blocked",
            "message": (
                f"Destruction of '{resource}' is disabled "
                "in the local provider."
            ),
        }
