"""Deterministic briefing generation.

The generator is pure: it receives an already-collected context and a definition
and returns a briefing.  It never reads files, credentials, or services, so the
same context and clock always produce the same briefing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from .models import (
    Briefing,
    BriefingContext,
    BriefingDefinition,
    BriefingSection,
    ProviderReport,
    ProviderState,
    iso,
    safe_text,
)

SECTION_TITLES: Dict[str, str] = {
    "reminders": "Reminders",
    "tasks": "Learned workflows",
    "weather": "Weather",
}


class BriefingGenerator:
    """Turn structured context into a structured, repeatable briefing."""

    MAX_ITEMS_PER_SECTION = 5

    def __init__(self, *, max_items_per_section: int = MAX_ITEMS_PER_SECTION) -> None:
        self.max_items_per_section = max(1, int(max_items_per_section))

    # ----------------------------------------------------------------
    # Rendering helpers (pure functions of the context)
    # ----------------------------------------------------------------

    @staticmethod
    def greeting_for(current: datetime, name: str) -> str:
        hour = current.hour
        if hour < 12:
            part = "Good morning"
        elif hour < 18:
            part = "Good afternoon"
        else:
            part = "Good evening"
        return f"{part}, {name}. Here is your briefing for {current.strftime('%A %d %B %Y')}."

    def _lines(self, provider: str, report: ProviderReport) -> List[str]:
        lines: List[str] = []
        for item in report.items[: self.max_items_per_section]:
            if provider == "reminders":
                value = safe_text(item.get("reminder"), 120)
            elif provider == "tasks":
                value = safe_text(item.get("workflow"), 120)
                successes = safe_text(item.get("successes"), 12)
                if value and successes:
                    value = f"{value} ({successes} successful run(s))"
            elif provider == "weather":
                value = safe_text(item.get("weather"), 160)
            else:
                value = ", ".join(
                    f"{safe_text(key, 40)}: {safe_text(value, 80)}" for key, value in sorted(item.items())
                )
            if value:
                lines.append(value)
        return sorted(lines)

    def generate(
        self,
        definition: BriefingDefinition,
        context: BriefingContext,
        *,
        now: Optional[datetime] = None,
    ) -> Briefing:
        current = now or datetime.now(timezone.utc)
        sections: List[BriefingSection] = []
        notes: List[str] = []
        for provider, title in SECTION_TITLES.items():
            report = context.by_name(provider)
            if report is None:
                continue
            lines = self._lines(provider, report) if report.available else []
            sections.append(BriefingSection(title=title, lines=tuple(lines)))
            if not report.available:
                notes.append(f"{provider}: {report.reason or ProviderState.UNAVAILABLE.value}")
        future = [
            report for report in context.reports
            if report.state == ProviderState.FUTURE.value and report.provider not in SECTION_TITLES
        ]
        for report in sorted(future, key=lambda item: item.provider):
            notes.append(f"{report.provider}: {report.reason}")
        if not any(section.lines for section in sections):
            notes.append("no local context is available yet; nothing was invented to fill the gap")
        return Briefing(
            definition_id=definition.definition_id,
            generated_at=iso(current),
            greeting=self.greeting_for(current, definition.name),
            sections=tuple(sections),
            notes=tuple(notes),
        )
