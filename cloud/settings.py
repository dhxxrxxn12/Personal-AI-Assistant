"""Production-safe configuration for the JARVIS Cloud service."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from core.runtime_config import BASE_DIR, configured_data_dir, runtime_dir, runtime_path


def _read_local_gemini_key() -> str | None:
    """Local-development fallback; production must use GEMINI_API_KEY."""
    if os.getenv("JARVIS_ENV", "development").strip().lower() in {"production", "prod"}:
        return None
    path = BASE_DIR / "config" / "api_keys.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("gemini_api_key")
    return str(value).strip() if value else None


def _split_origins(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _port_from_environment() -> int:
    try:
        return max(1, int(os.getenv("PORT", os.getenv("JARVIS_PORT", "8000"))))
    except (TypeError, ValueError):
        return 8000


@dataclass(frozen=True)
class CloudSettings:
    environment: str
    host: str
    port: int
    gemini_api_key: str | None
    admin_token: str | None
    agent_token: str | None
    cors_origins: tuple[str, ...]
    data_dir: Path | None
    task_file: Path
    devices_file: Path
    activity_file: Path
    uploads_dir: Path

    @property
    def production(self) -> bool:
        return self.environment in {"production", "prod", "staging"}

    @property
    def auth_required(self) -> bool:
        return self.production or bool(self.admin_token or self.agent_token)

    def validate(self) -> None:
        if not self.production:
            return
        missing = []
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if not self.admin_token:
            missing.append("JARVIS_ADMIN_TOKEN")
        if not self.agent_token:
            missing.append("JARVIS_AGENT_TOKEN")
        if missing:
            raise RuntimeError(
                "Production configuration is incomplete; missing environment variable(s): "
                + ", ".join(missing)
            )


def load_settings() -> CloudSettings:
    environment = os.getenv("JARVIS_ENV", "development").strip().lower()
    shared_token = os.getenv("JARVIS_CLOUD_TOKEN", "").strip() or None
    admin_token = os.getenv("JARVIS_ADMIN_TOKEN", "").strip() or shared_token
    agent_token = os.getenv("JARVIS_AGENT_TOKEN", "").strip() or shared_token
    data_dir = configured_data_dir()

    legacy_cloud = BASE_DIR / "cloud"
    legacy_uploads = BASE_DIR / "uploads"

    return CloudSettings(
        environment=environment,
        host=os.getenv("JARVIS_HOST", "0.0.0.0"),
        port=_port_from_environment(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip() or _read_local_gemini_key(),
        admin_token=admin_token,
        agent_token=agent_token,
        cors_origins=_split_origins(os.getenv("JARVIS_CORS_ORIGINS", "")),
        data_dir=data_dir,
        task_file=runtime_path("tasks.json", legacy_cloud / "tasks.json"),
        devices_file=runtime_path("devices.json", legacy_cloud / "devices.json"),
        activity_file=runtime_path("activity.json", legacy_cloud / "activity.json"),
        uploads_dir=runtime_dir("uploads", legacy_uploads),
    )


settings = load_settings()
settings.validate()
