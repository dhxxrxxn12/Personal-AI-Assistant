"""Read-only sandbox endpoint verification."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable

from .policies import EngineeringOperation, EngineeringPolicy


@dataclass(frozen=True)
class VerificationResult:
    base_url: str
    checks: Dict[str, int | str]
    success: bool


class DeploymentVerifier:
    def __init__(self, policy: EngineeringPolicy, *, timeout_seconds: float = 5.0) -> None:
        self.policy = policy
        self.timeout_seconds = timeout_seconds

    def verify(self, base_url: str, paths: Iterable[str] = ("/health",)) -> VerificationResult:
        self.policy.require(EngineeringOperation.VERIFY_DEPLOYMENT)
        normalized = base_url.rstrip("/")
        if not normalized.startswith(("http://", "https://")) or any(char in normalized for char in "\r\n"):
            raise ValueError("Deployment verification requires an http(s) URL without credentials.")
        parsed = urllib.parse.urlparse(normalized)
        if parsed.username or parsed.password:
            raise ValueError("Deployment verification URLs may not contain credentials.")
        checks: Dict[str, int | str] = {}
        success = True
        for path in paths:
            if not path.startswith("/") or ".." in path or "\n" in path or "\r" in path:
                raise ValueError("Verification paths must be absolute, local URL paths.")
            url = normalized + path
            try:
                with urllib.request.urlopen(url, timeout=self.timeout_seconds) as response:
                    checks[path] = int(response.status)
                    success = success and 200 <= int(response.status) < 400
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                checks[path] = f"{type(exc).__name__}: unavailable"
                success = False
        return VerificationResult(normalized, checks, success)
