"""Local persistence for briefing definitions.

Configuration is stored with the existing JARVIS runtime convention
(``core.runtime_config.runtime_path``): a local JSON file by default, or the
configured ``JARVIS_DATA_DIR`` when a deployment provides one. Writes are
atomic and no secrets are ever stored here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from core.runtime_config import BASE_DIR, runtime_path

from .models import BriefingDefinition, iso, safe_text, utc_now

DEFAULT_CONFIG_PATH = runtime_path("briefing_config.json", BASE_DIR / "automation" / "briefing_config.json")


class BriefingConfigStore:
    """Create, update, enable/disable, and remove local briefing definitions."""

    MAX_DEFINITIONS = 20

    def __init__(self, path: Optional[Path] = None, *, max_definitions: int = MAX_DEFINITIONS) -> None:
        self.path = Path(path or DEFAULT_CONFIG_PATH)
        self.max_definitions = max(1, int(max_definitions))

    @staticmethod
    def _redact(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): (
                    "<redacted>"
                    if re.search(r"key|token|secret|password", str(key), re.I)
                    else BriefingConfigStore._redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [BriefingConfigStore._redact(item) for item in value]
        if isinstance(value, str):
            return re.sub(
                r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+",
                r"\1=<redacted>",
                value,
            )
        return value

    def _write(self, definitions: List[BriefingDefinition]) -> None:
        payload = self._redact([item.to_dict() for item in definitions])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def definitions(self) -> List[BriefingDefinition]:
        """Load every valid definition; corrupt entries are ignored."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        loaded: List[BriefingDefinition] = []
        for item in raw[: self.max_definitions]:
            if not isinstance(item, Mapping):
                continue
            try:
                loaded.append(BriefingDefinition.from_dict(item))
            except (ValueError, TypeError):
                continue
        return loaded

    def get(self, definition_id: str) -> Optional[BriefingDefinition]:
        for definition in self.definitions():
            if definition.definition_id == definition_id:
                return definition
        return None

    def save(self, definition: BriefingDefinition) -> BriefingDefinition:
        """Insert or update one definition, stamping lifecycle timestamps."""
        definition.validate()
        now = iso(utc_now())
        existing = {item.definition_id: item for item in self.definitions()}
        previous = existing.get(definition.definition_id)
        stamped = BriefingDefinition(
            **{
                **definition.to_dict(),
                "channels": tuple(definition.channels),
                "created_at": previous.created_at if previous else (definition.created_at or now),
                "updated_at": now,
            }
        ).validate()
        existing[stamped.definition_id] = stamped
        ordered = list(existing.values())[-self.max_definitions:]
        self._write(ordered)
        return stamped

    def remove(self, definition_id: str) -> bool:
        existing = self.definitions()
        remaining = [item for item in existing if item.definition_id != definition_id]
        if len(remaining) == len(existing):
            return False
        self._write(remaining)
        return True

    def set_enabled(self, definition_id: str, enabled: bool) -> BriefingDefinition:
        current = self.get(definition_id)
        if current is None:
            raise KeyError(f"Unknown briefing '{definition_id}'.")
        return self.save(
            BriefingDefinition(**{**current.to_dict(), "channels": tuple(current.channels), "enabled": bool(enabled)})
        )

    def summary(self) -> List[Dict[str, Any]]:
        return [
            {
                "definition_id": item.definition_id,
                "name": safe_text(item.name, 80),
                "time_of_day": item.time_of_day,
                "timezone": item.timezone,
                "enabled": item.enabled,
                "channels": list(item.channels),
            }
            for item in self.definitions()
        ]
