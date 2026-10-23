"""Local-only context providers for the briefing core.

Every provider reports its own availability instead of inventing content, and no
provider opens a network connection, reads credentials, or writes anything.
Existing JARVIS surfaces are reused wherever local data already exists:

* reminders -> ``~/.jarvis/reminders`` scripts registered by ``actions/reminder.py``
* tasks     -> ``automation/learning_memory.json`` learned workflows
* weather   -> an optional local cache; the bundled weather action is provider
               backed, so without a cache this provider reports ``future``
* calendar / email -> declared future providers, never fabricated
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Tuple

from core.runtime_config import BASE_DIR

from .models import BriefingContext, ProviderReport, ProviderState, iso, safe_text, utc_now


class ContextProvider:
    """Base contract: a name, an availability check, and a bounded collection."""

    name = "provider"
    state_when_empty = ProviderState.PARTIAL
    empty_reason = "no local data available"

    def available(self) -> bool:
        """Whether this provider can run at all in this installation."""
        return True

    def collect(self) -> ProviderReport:
        raise NotImplementedError

    def report(self, state: str, reason: str = "", items: Iterable[Mapping[str, str]] = ()) -> ProviderReport:
        return ProviderReport(provider=self.name, state=state, reason=reason, items=tuple(items))


class ReminderProvider(ContextProvider):
    """Local system reminders registered by the existing reminder action."""

    name = "reminders"
    empty_reason = "no system reminders are registered yet"

    def __init__(self, directory: Optional[Path] = None, *, limit: int = 5) -> None:
        self.directory = Path(directory or (Path.home() / ".jarvis" / "reminders"))
        self.limit = max(1, int(limit))

    def collect(self) -> ProviderReport:
        try:
            if not self.directory.is_dir():
                return self.report(self.state_when_empty.value, self.empty_reason)
            names = sorted(path.stem for path in self.directory.glob("*.py") if path.is_file())
        except OSError as exc:
            return self.report(ProviderState.UNAVAILABLE.value, f"reminder directory unreadable: {type(exc).__name__}")
        if not names:
            return self.report(self.state_when_empty.value, self.empty_reason)
        items = [{"reminder": safe_text(name, 80)} for name in names[: self.limit]]
        return self.report(ProviderState.AVAILABLE.value, "", items)


class TaskProvider(ContextProvider):
    """Learned autonomous workflows stored by the existing learning memory."""

    name = "tasks"
    empty_reason = "no learned workflows have been recorded yet"

    def __init__(self, memory_path: Optional[Path] = None, *, limit: int = 5) -> None:
        self.memory_path = Path(memory_path or (BASE_DIR / "automation" / "learning_memory.json"))
        self.limit = max(1, int(limit))

    def collect(self) -> ProviderReport:
        try:
            if not self.memory_path.is_file():
                return self.report(self.state_when_empty.value, self.empty_reason)
            raw = json.loads(self.memory_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return self.report(ProviderState.UNAVAILABLE.value, f"learning memory unreadable: {type(exc).__name__}")
        if not isinstance(raw, Mapping):
            return self.report(self.state_when_empty.value, self.empty_reason)
        ranked: List[Tuple[int, str]] = []
        for key, value in raw.items():
            if not isinstance(value, Mapping):
                continue
            name = safe_text(value.get("name") or key, 80)
            try:
                successes = int(value.get("success_count") or 0)
            except (TypeError, ValueError):
                successes = 0
            if name:
                ranked.append((successes, name))
        if not ranked:
            return self.report(self.state_when_empty.value, self.empty_reason)
        ranked.sort(key=lambda item: (-item[0], item[1]))
        items = [{"workflow": name, "successes": str(successes)} for successes, name in ranked[: self.limit]]
        return self.report(ProviderState.AVAILABLE.value, "", items)


class WeatherProvider(ContextProvider):
    """Reads a local weather cache only; it never calls a weather service."""

    name = "weather"
    empty_reason = "no local weather cache is present"

    def __init__(self, cache_path: Optional[Path] = None) -> None:
        self.cache_path = Path(cache_path or (BASE_DIR / "automation" / "weather_cache.json"))

    def collect(self) -> ProviderReport:
        try:
            if not self.cache_path.is_file():
                return self.report(
                    ProviderState.FUTURE.value,
                    "the bundled weather action is provider backed and keeps no local cache",
                )
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return self.report(ProviderState.UNAVAILABLE.value, f"weather cache unreadable: {type(exc).__name__}")
        if not isinstance(raw, Mapping):
            return self.report(ProviderState.UNAVAILABLE.value, "weather cache is not a JSON object")
        summary = safe_text(raw.get("summary") or raw.get("condition") or "", 120)
        temperature = safe_text(raw.get("temperature") or raw.get("temp") or "", 20)
        location = safe_text(raw.get("location") or "", 60)
        if not summary and not temperature:
            return self.report(ProviderState.UNAVAILABLE.value, "weather cache has no usable summary")
        items = [{"weather": " ".join(part for part in (location, temperature, summary) if part).strip()}]
        return self.report(ProviderState.AVAILABLE.value, "", items)


class FutureProvider(ContextProvider):
    """A declared integration that is honestly reported as future work."""

    state_when_empty = ProviderState.FUTURE
    required = "explicit account consent"

    def __init__(self, name: str, *, required: Optional[str] = None) -> None:
        self.name = name
        self.required = required or self.required

    def available(self) -> bool:
        return False

    def collect(self) -> ProviderReport:
        return self.report(
            ProviderState.FUTURE.value,
            f"not configured: requires {self.required} (external provider is not part of the local core)",
        )


class CalendarProvider(FutureProvider):
    name = "calendar"


class EmailProvider(FutureProvider):
    name = "email"


class BriefingContextAggregator:
    """Collects provider reports without ever letting one provider break a run."""

    def __init__(self, providers: Optional[Iterable[ContextProvider]] = None) -> None:
        self.providers: Tuple[ContextProvider, ...] = tuple(providers or default_providers())

    def provider_names(self) -> List[str]:
        return [provider.name for provider in self.providers]

    def collect(self, now: Optional[object] = None) -> BriefingContext:
        reports: List[ProviderReport] = []
        for provider in self.providers:
            try:
                reports.append(provider.collect() if provider.available() else provider.collect())
            except Exception as exc:  # a broken provider must never break the briefing
                reports.append(ProviderReport(
                    provider=getattr(provider, "name", "provider"),
                    state=ProviderState.UNAVAILABLE.value,
                    reason=f"provider failed: {type(exc).__name__}",
                ))
        return BriefingContext(collected_at=iso(now or utc_now()), reports=tuple(reports))


def default_providers(
    *,
    reminder_dir: Optional[Path] = None,
    memory_path: Optional[Path] = None,
    weather_cache: Optional[Path] = None,
) -> Tuple[ContextProvider, ...]:
    """The local provider set: real local sources first, future ones declared."""
    return (
        ReminderProvider(reminder_dir),
        TaskProvider(memory_path),
        WeatherProvider(weather_cache),
        CalendarProvider(),
        EmailProvider(),
    )
