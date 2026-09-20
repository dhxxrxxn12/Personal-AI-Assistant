from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_config import migrate_legacy_file
from cloud.settings import settings


BASE_DIR = Path(__file__).resolve().parent
_LEGACY_DEVICES_FILE = BASE_DIR / "devices.json"
DEVICES_FILE = settings.devices_file
migrate_legacy_file(DEVICES_FILE, _LEGACY_DEVICES_FILE)

DEVICE_TIMEOUT_SECONDS = 45


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


class DeviceManager:
    """
    Persistent registry for JARVIS execution agents and clients.

    A device has:
      - device_id
      - device_type
      - name
      - capabilities
      - status
      - last_seen
      - metadata
    """

    def __init__(self, path: Path = DEVICES_FILE):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.devices: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ---------------------------------------------------------
    # Persistence
    # ---------------------------------------------------------

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self.devices = {}
                return

            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))

                if isinstance(data, dict):
                    self.devices = data
                else:
                    self.devices = {}

            except (OSError, json.JSONDecodeError):
                self.devices = {}

    def _save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)

            temp_path = self.path.with_suffix(".tmp")

            temp_path.write_text(
                json.dumps(
                    self.devices,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            temp_path.replace(self.path)

    # ---------------------------------------------------------
    # Registration
    # ---------------------------------------------------------

    def register(
        self,
        device_id: str,
        device_type: str,
        name: str,
        capabilities: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        if not device_id:
            raise ValueError("device_id is required")

        if not device_type:
            raise ValueError("device_type is required")

        now = utc_now_iso()

        with self._lock:
            existing = self.devices.get(device_id, {})

            device = {
                "device_id": device_id,
                "device_type": device_type,
                "name": name or device_id,
                "capabilities": capabilities or existing.get("capabilities", []),
                "metadata": metadata or existing.get("metadata", {}),
                "status": "online",
                "registered_at": existing.get("registered_at", now),
                "last_seen": now,
            }

            self.devices[device_id] = device
            self._save()

            return dict(device)

    # ---------------------------------------------------------
    # Heartbeat
    # ---------------------------------------------------------

    def heartbeat(
        self,
        device_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:

        with self._lock:
            device = self.devices.get(device_id)

            if device is None:
                return None

            device["status"] = "online"
            device["last_seen"] = utc_now_iso()

            if metadata:
                device.setdefault("metadata", {}).update(metadata)

            self._save()

            return dict(device)

    # ---------------------------------------------------------
    # Status
    # ---------------------------------------------------------

    def refresh_status(self) -> None:
        now = utc_now()

        with self._lock:
            changed = False

            for device in self.devices.values():
                last_seen = parse_time(device.get("last_seen"))

                if last_seen is None:
                    continue

                age = (now - last_seen).total_seconds()

                new_status = (
                    "offline"
                    if age > DEVICE_TIMEOUT_SECONDS
                    else "online"
                )

                if device.get("status") != new_status:
                    device["status"] = new_status
                    changed = True

            if changed:
                self._save()

    def get(self, device_id: str) -> Optional[Dict[str, Any]]:
        self.refresh_status()

        with self._lock:
            device = self.devices.get(device_id)

            if device is None:
                return None

            return dict(device)

    def list(self) -> List[Dict[str, Any]]:
        self.refresh_status()

        with self._lock:
            return [
                dict(device)
                for device in self.devices.values()
            ]

    def remove(self, device_id: str) -> bool:
        with self._lock:
            if device_id not in self.devices:
                return False

            del self.devices[device_id]
            self._save()

            return True

    def is_online(self, device_id: str) -> bool:
        device = self.get(device_id)
        return bool(device and device.get("status") == "online")

    def capabilities(self, device_id: str) -> List[str]:
        device = self.get(device_id)

        if not device:
            return []

        return list(device.get("capabilities", []))

    def status(self) -> Dict[str, Any]:
        devices = self.list()

        return {
            "device_count": len(devices),
            "online": sum(
                1 for device in devices
                if device.get("status") == "online"
            ),
            "offline": sum(
                1 for device in devices
                if device.get("status") == "offline"
            ),
            "devices": devices,
        }


device_manager = DeviceManager()
