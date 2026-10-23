"""Local, atomic persistence for capability plans and registry snapshots.

Capability state has to survive a JARVIS restart without any remote service, so
the store writes structured metadata to the existing local runtime path (or to
``JARVIS_DATA_DIR`` when a deployment configures one) and redacts credential-like
values before writing.  Source contents, diffs, and secrets are never stored.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from core.runtime_config import BASE_DIR, runtime_path

from .models import utc_now

_LEGACY_PLAN_FILE = BASE_DIR / "automation" / "capability_plans.json"
_LEGACY_REGISTRY_FILE = BASE_DIR / "automation" / "capability_registry.json"

CAPABILITY_PLAN_FILE = runtime_path("capability_plans.json", _LEGACY_PLAN_FILE)
CAPABILITY_REGISTRY_FILE = runtime_path("capability_registry.json", _LEGACY_REGISTRY_FILE)


class CapabilityStore:
    """Read and write capability plans and the last registry snapshot locally."""

    MAX_PLANS = 25
    MAX_REGISTRY_ENTRIES = 200

    def __init__(
        self,
        path: Optional[Path] = None,
        *,
        registry_path: Optional[Path] = None,
        max_plans: int = MAX_PLANS,
    ) -> None:
        self.path = Path(path or CAPABILITY_PLAN_FILE)
        self.registry_path = Path(registry_path or CAPABILITY_REGISTRY_FILE)
        self.max_plans = max(1, int(max_plans))

    # ----------------------------------------------------------------
    # Safety helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _redact(value: Any) -> Any:
        """Redact credential-like keys and values, mirroring engineering memory."""
        if isinstance(value, Mapping):
            return {
                str(key): (
                    "<redacted>"
                    if re.search(r"key|token|secret|password", str(key), re.I)
                    else CapabilityStore._redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [CapabilityStore._redact(item) for item in value]
        if isinstance(value, str):
            return re.sub(
                r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+",
                r"\1=<redacted>",
                value,
            )
        return value

    def _write(self, path: Path, payload: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._redact(payload), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

    def _read(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # ----------------------------------------------------------------
    # Plans
    # ----------------------------------------------------------------

    def plans(self) -> List[Dict[str, Any]]:
        raw = self._read(self.path)
        if not isinstance(raw, list):
            return []
        return [dict(item) for item in raw if isinstance(item, Mapping)]

    def save_plan(self, plan: Any) -> Path:
        """Upsert one plan by analysis id, keeping the newest ``max_plans``."""
        if not hasattr(plan, "to_dict"):
            raise TypeError("Capability plans must be serializable.")
        payload = plan.to_dict()
        analysis_id = payload.get("analysis_id")
        if not analysis_id:
            raise ValueError("Capability plans require an analysis_id before persistence.")
        retained = [item for item in self.plans() if item.get("analysis_id") != analysis_id]
        retained.append(payload)
        return self._write(self.path, retained[-self.max_plans:])

    def load_plan(self, analysis_id: str) -> Optional[Any]:
        """Rebuild a persisted plan; an unknown or corrupt entry returns None."""
        for item in reversed(self.plans()):
            if item.get("analysis_id") != analysis_id:
                continue
            try:
                return self._plan_from(item)
            except ValueError:
                return None
        return None

    def latest_plan(self) -> Optional[Any]:
        for item in reversed(self.plans()):
            try:
                return self._plan_from(item)
            except ValueError:
                continue
        return None

    def plan_summaries(self) -> List[Dict[str, Any]]:
        return [
            {
                "analysis_id": item.get("analysis_id"),
                "goal": item.get("goal"),
                "status": item.get("status"),
                "revision": item.get("revision", 1),
                "created_at": item.get("created_at"),
                "approved_by": item.get("approved_by"),
                "engineering_task_id": item.get("engineering_task_id"),
            }
            for item in self.plans()
        ]

    @staticmethod
    def _plan_from(payload: Mapping[str, Any]) -> Any:
        # Imported lazily so the builder can import this store at module level.
        from .capability_builder import CapabilityPlan

        return CapabilityPlan.from_dict(payload)

    # ----------------------------------------------------------------
    # Registry snapshot
    # ----------------------------------------------------------------

    def save_registry(self, specs: Sequence[Any], *, goal: str = "") -> Path:
        """Store only the structured capability metadata needed after a restart."""
        entries: List[Dict[str, Any]] = []
        for spec in list(specs)[: self.MAX_REGISTRY_ENTRIES]:
            if not hasattr(spec, "to_dict"):
                continue
            payload = spec.to_dict()
            entries.append({
                "capability_id": payload.get("capability_id"),
                "name": payload.get("name"),
                "status": payload.get("status"),
                "availability": payload.get("availability"),
                "risk": payload.get("risk"),
                "dependencies": list(payload.get("dependencies") or []),
                "permissions": list(payload.get("permissions") or []),
                "evidence": list(payload.get("evidence") or []),
                "missing_evidence": list(payload.get("missing_evidence") or []),
                "roadmap_reference": payload.get("roadmap_reference") or "",
                "discovered": bool(payload.get("discovered", False)),
                "blocked_reason": payload.get("blocked_reason") or "",
            })
        return self._write(self.registry_path, {
            "goal": goal,
            "generated_at": utc_now(),
            "count": len(entries),
            "capabilities": entries,
        })

    def registry(self) -> Dict[str, Any]:
        raw = self._read(self.registry_path)
        return dict(raw) if isinstance(raw, Mapping) else {}
