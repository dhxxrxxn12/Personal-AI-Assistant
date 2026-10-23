"""Deterministic, local-only briefing data model.

This module is pure data: no I/O, no network, no credentials. Every other
briefing module depends on these contracts so the scheduler, the context
aggregation, the generator, and the delivery layer stay replaceable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
DEFAULT_CHANNELS: Tuple[str, ...] = ("console",)


class ProviderState(str, Enum):
    """Honest availability of one context provider."""

    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    FUTURE = "future"


class RunStatus(str, Enum):
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


class MissedRunPolicy(str, Enum):
    """What to do when JARVIS was not running at the scheduled time."""

    SKIP = "skip"
    RUN_ONCE = "run_once"


def parse_time_of_day(value: str) -> Tuple[int, int]:
    """Return (hour, minute) for a strict ``HH:MM`` local wall-clock time."""
    match = TIME_RE.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError(f"Briefing time must use HH:MM (24-hour), got '{value}'.")
    return int(match.group(1)), int(match.group(2))


def normalize_timezone(value: str) -> str:
    """Validate an explicit IANA timezone name; local time is never assumed."""
    name = str(value or "").strip() or "UTC"
    if name.upper() == "UTC":
        return "UTC"
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(name)
    except Exception as exc:
        raise ValueError(f"Unknown timezone '{name}': {exc}") from exc
    return name


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat()


def bounded(values: Sequence[str], limit: int) -> List[str]:
    return list(values)[: max(0, int(limit))]


def safe_text(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True)
class BriefingDefinition:
    """One persistent, user-configured briefing schedule."""

    definition_id: str
    name: str
    time_of_day: str
    timezone: str = "UTC"
    enabled: bool = True
    channels: Tuple[str, ...] = DEFAULT_CHANNELS
    missed_run_policy: str = MissedRunPolicy.RUN_ONCE.value
    grace_minutes: int = 120
    created_at: str = ""
    updated_at: str = ""

    def validate(self) -> "BriefingDefinition":
        if not ID_RE.fullmatch(str(self.definition_id or "")):
            raise ValueError("Briefing id must match ^[a-z][a-z0-9_-]{1,63}$.")
        if not str(self.name or "").strip():
            raise ValueError("Briefing name must be non-empty.")
        parse_time_of_day(self.time_of_day)
        normalize_timezone(self.timezone)
        if not self.channels:
            raise ValueError("Briefing must declare at least one delivery channel.")
        for channel in self.channels:
            if not ID_RE.fullmatch(str(channel)):
                raise ValueError(f"Invalid delivery channel '{channel}'.")
        try:
            MissedRunPolicy(str(self.missed_run_policy))
        except ValueError as exc:
            raise ValueError(f"Invalid missed-run policy '{self.missed_run_policy}'.") from exc
        if int(self.grace_minutes) < 0:
            raise ValueError("grace_minutes must not be negative.")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "definition_id": self.definition_id,
            "name": self.name,
            "time_of_day": self.time_of_day,
            "timezone": self.timezone,
            "enabled": bool(self.enabled),
            "channels": list(self.channels),
            "missed_run_policy": self.missed_run_policy,
            "grace_minutes": int(self.grace_minutes),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BriefingDefinition":
        if not isinstance(raw, Mapping):
            raise ValueError("A briefing definition must be a JSON object.")
        channels = raw.get("channels") or list(DEFAULT_CHANNELS)
        if not isinstance(channels, (list, tuple)):
            raise ValueError("channels must be a list.")
        definition = cls(
            definition_id=str(raw.get("definition_id") or "").strip(),
            name=" ".join(str(raw.get("name") or "").split()),
            time_of_day=str(raw.get("time_of_day") or "").strip(),
            timezone=str(raw.get("timezone") or "UTC"),
            enabled=bool(raw.get("enabled", True)),
            channels=tuple(str(item) for item in channels),
            missed_run_policy=str(raw.get("missed_run_policy") or MissedRunPolicy.RUN_ONCE.value),
            grace_minutes=int(raw.get("grace_minutes", 120) or 0),
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
        )
        return definition.validate()


@dataclass(frozen=True)
class ProviderReport:
    """One provider's contribution, including why it had nothing to add."""

    provider: str
    state: str
    reason: str = ""
    items: Tuple[Mapping[str, str], ...] = ()

    @property
    def available(self) -> bool:
        return self.state in {ProviderState.AVAILABLE.value, ProviderState.PARTIAL.value}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.state,
            "reason": self.reason,
            "items": [dict(item) for item in self.items],
        }


@dataclass(frozen=True)
class ContextItem:
    """A normalized, provider-agnostic local briefing fact."""

    source: str
    category: str
    title: str
    content: str
    priority: str = "medium"
    timestamp: str = ""
    confidence: float = 0.0
    availability: str = "available"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def priority_score(self) -> int:
        weights = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
        return weights.get(str(self.priority).lower(), 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "category": self.category,
            "title": self.title,
            "content": self.content,
            "priority": self.priority,
            "timestamp": self.timestamp,
            "confidence": float(self.confidence),
            "availability": self.availability,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class BriefingContext:
    """Structured context collected from local providers only."""

    collected_at: str
    reports: Tuple[ProviderReport, ...] = ()
    items: Tuple[ContextItem, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    unavailable_sources: Tuple[str, ...] = ()

    def by_name(self, provider: str) -> Optional[ProviderReport]:
        for report in self.reports:
            if report.provider == provider:
                return report
        return None

    @property
    def available_providers(self) -> List[str]:
        return [report.provider for report in self.reports if report.available]

    @property
    def unavailable_providers(self) -> List[str]:
        values = [report.provider for report in self.reports if not report.available]
        if values:
            return values
        return list(self.unavailable_sources)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "collected_at": self.collected_at,
            "providers": [report.to_dict() for report in self.reports],
            "items": [item.to_dict() for item in self.items],
            "metadata": dict(self.metadata),
            "available": self.available_providers,
            "unavailable": self.unavailable_providers,
        }


@dataclass(frozen=True)
class BriefingSection:
    title: str
    lines: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "lines": list(self.lines)}


@dataclass(frozen=True)
class Briefing:
    """A generated briefing; deterministic for the same context and clock."""

    definition_id: str
    generated_at: str
    greeting: str
    sections: Tuple[BriefingSection, ...] = ()
    notes: Tuple[str, ...] = ()

    @property
    def plain_text(self) -> str:
        lines = [self.greeting]
        for section in self.sections:
            lines.append("")
            lines.append(f"{section.title}:")
            if section.lines:
                lines.extend(f"  - {line}" for line in section.lines)
            else:
                lines.append("  - nothing to report")
        if self.notes:
            lines.append("")
            lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "definition_id": self.definition_id,
            "generated_at": self.generated_at,
            "greeting": self.greeting,
            "sections": [section.to_dict() for section in self.sections],
            "notes": list(self.notes),
            "plain_text": self.plain_text,
        }


@dataclass(frozen=True)
class DeliveryResult:
    channel: str
    delivered: bool
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"channel": self.channel, "delivered": bool(self.delivered), "detail": self.detail}


@dataclass(frozen=True)
class BriefingRunRecord:
    """One execution attempt, persisted locally for duplicate prevention."""

    definition_id: str
    scheduled_for: str
    executed_at: str
    status: str
    duplicate: bool = False
    detail: str = ""
    deliveries: Tuple[Mapping[str, Any], ...] = ()
    providers: Tuple[str, ...] = ()

    @property
    def slot_key(self) -> str:
        return f"{self.definition_id}@{self.scheduled_for}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "definition_id": self.definition_id,
            "scheduled_for": self.scheduled_for,
            "executed_at": self.executed_at,
            "status": self.status,
            "duplicate": bool(self.duplicate),
            "detail": self.detail,
            "deliveries": [dict(item) for item in self.deliveries],
            "providers": list(self.providers),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BriefingRunRecord":
        if not isinstance(raw, Mapping):
            raise ValueError("A run record must be a JSON object.")
        deliveries = raw.get("deliveries") or []
        return cls(
            definition_id=str(raw.get("definition_id") or ""),
            scheduled_for=str(raw.get("scheduled_for") or ""),
            executed_at=str(raw.get("executed_at") or ""),
            status=str(raw.get("status") or RunStatus.SKIPPED.value),
            duplicate=bool(raw.get("duplicate", False)),
            detail=str(raw.get("detail") or ""),
            deliveries=tuple(dict(item) for item in deliveries if isinstance(item, Mapping)),
            providers=tuple(str(item) for item in (raw.get("providers") or [])),
        )
