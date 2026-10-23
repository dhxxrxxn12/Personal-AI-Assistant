"""Briefing orchestration: configure -> schedule -> context -> generate -> deliver -> record.

This is the only module that wires the pieces together.  It never starts a
background thread; the host calls ``tick`` (or ``run_once``) with an explicit
clock, which keeps the whole pipeline deterministic and easy to verify.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .config_store import BriefingConfigStore
from .delivery import DeliveryRegistry, default_channels
from .generator import BriefingGenerator
from .ledger import BriefingLedger
from .models import (
    Briefing,
    BriefingContext,
    BriefingDefinition,
    BriefingRunRecord,
    DeliveryResult,
    RunStatus,
    safe_text,
    utc_now,
)
from .providers import BriefingContextAggregator, default_providers
from .scheduler import BriefingScheduler

MORNING = "08:00"
EVENING = "18:00"


def _request_time(text: str) -> Optional[str]:
    """Extract a strict HH:MM time from a short configuration request."""
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text or "")
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def resolve_request(
    text: str,
    *,
    definition_id: str = "morning-briefing",
    timezone_name: str = "UTC",
) -> BriefingDefinition:
    """Turn a short local request into a validated briefing definition.

    This is a deliberate, deterministic parser (no model is involved): it reads
    an explicit ``HH:MM`` time, or falls back to the words morning/evening.
    """
    request = " ".join(str(text or "").lower().split())
    if not request:
        raise ValueError("A briefing request must not be empty.")
    time_of_day = _request_time(request)
    if time_of_day is None:
        if "morning" in request:
            time_of_day = MORNING
        elif "evening" in request:
            time_of_day = EVENING
        else:
            raise ValueError("The request must include a time such as '08:00' or the word morning/evening.")
    name = "Morning briefing" if time_of_day < "12:00" else "Evening briefing"
    return BriefingDefinition(
        definition_id=definition_id,
        name=name,
        time_of_day=time_of_day,
        timezone=timezone_name,
        enabled=True,
        channels=("console", "recording"),
    ).validate()


class BriefingService:
    """Local, deterministic orchestration of the briefing lifecycle."""

    def __init__(
        self,
        *,
        state_dir: Optional[Path] = None,
        store: Optional[BriefingConfigStore] = None,
        ledger: Optional[BriefingLedger] = None,
        aggregator: Optional[BriefingContextAggregator] = None,
        generator: Optional[BriefingGenerator] = None,
        deliveries: Optional[DeliveryRegistry] = None,
        clock: Optional[Callable[[], datetime]] = None,
        writer: Optional[Callable[[str], None]] = None,
        speak: Optional[Callable[[str], None]] = None,
        allow_system_notification: bool = False,
    ) -> None:
        directory = Path(state_dir) if state_dir else None
        self.store = store or BriefingConfigStore(
            (directory / "briefing_config.json") if directory else None
        )
        self.ledger = ledger or BriefingLedger(
            (directory / "briefing_ledger.json") if directory else None
        )
        self.aggregator = aggregator or BriefingContextAggregator(default_providers())
        self.generator = generator or BriefingGenerator()
        self.deliveries = deliveries or default_channels(
            writer=writer,
            speak=speak,
            allow_system_notification=allow_system_notification,
        )
        self.clock: Callable[[], datetime] = clock or utc_now

    # ----------------------------------------------------------------
    # Configuration
    # ----------------------------------------------------------------

    def definitions(self) -> List[BriefingDefinition]:
        return self.store.definitions()

    def definition(self, definition_id: str) -> BriefingDefinition:
        definition = self.store.get(definition_id)
        if definition is None:
            raise KeyError(f"Unknown briefing '{definition_id}'.")
        return definition

    def configure(self, definition: BriefingDefinition) -> BriefingDefinition:
        return self.store.save(definition.validate())

    def configure_from_request(self, text: str, **kwargs) -> BriefingDefinition:
        return self.configure(resolve_request(text, **kwargs))

    def set_enabled(self, definition_id: str, enabled: bool) -> BriefingDefinition:
        return self.store.set_enabled(definition_id, enabled)

    def remove(self, definition_id: str) -> bool:
        return self.store.remove(definition_id)

    # ----------------------------------------------------------------
    # Schedule questions
    # ----------------------------------------------------------------

    def scheduler(self, definition_id: str) -> BriefingScheduler:
        return BriefingScheduler(self.definition(definition_id))

    def next_run(self, definition_id: str, *, now: Optional[datetime] = None) -> Optional[datetime]:
        return self.scheduler(definition_id).next_run(now or self.clock())

    # ----------------------------------------------------------------
    # Context, generation, delivery
    # ----------------------------------------------------------------

    def load_context(self, *, now: Optional[datetime] = None) -> BriefingContext:
        return self.aggregator.collect(now or self.clock())

    def generate(
        self,
        definition_id: str,
        *,
        context: Optional[BriefingContext] = None,
        now: Optional[datetime] = None,
    ) -> Briefing:
        current = now or self.clock()
        collected = context if context is not None else self.load_context(now=current)
        return self.generator.generate(self.definition(definition_id), collected, now=current)

    def deliver(self, briefing: Briefing, *, channels: Optional[Sequence[str]] = None) -> List[DeliveryResult]:
        return self.deliveries.deliver(briefing, channels)
