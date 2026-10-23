"""Local proactive context normalization and relevance ranking.

This layer sits between raw provider output and the briefing generator. It keeps
all selection logic deterministic and local-only: each provider is normalized into
provider-agnostic ContextItem objects, then sorted by a stable relevance score.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence, Tuple

from .models import BriefingContext, ContextItem
from .providers import BriefingContextAggregator, default_providers


class RelevanceEngine:
    """Deterministic ordering for the unified local context stream."""

    CATEGORY_WEIGHT = {
        "system": 3,
        "reminders": 2,
        "tasks": 2,
        "memory": 1,
        "weather": 0,
    }

    PRIORITY_WEIGHT = {
        "critical": 5,
        "high": 4,
        "medium": 3,
        "low": 2,
        "info": 1,
    }

    def score(self, item: ContextItem) -> int:
        base = self.CATEGORY_WEIGHT.get(item.category, 1) * 10
        base += self.PRIORITY_WEIGHT.get(str(item.priority).lower(), 3) * 10
        lowered = (item.title + " " + item.content).lower()
        if "alert" in lowered or "urgent" in lowered or "critical" in lowered:
            base += 10
        if "gym" in lowered or "meeting" in lowered:
            base += 5
        return base

    def rank(self, context: BriefingContext) -> Tuple[ContextItem, ...]:
        if not context.items:
            return ()
        return tuple(
            sorted(
                context.items,
                key=lambda item: (-self.score(item), item.title.lower(), item.source),
            )
        )

    def relevant(self, context: BriefingContext, *, limit: int = 5) -> Tuple[ContextItem, ...]:
        return self.rank(context)[: max(0, int(limit))]


class ContextEngine:
    """Normalize local provider data and rank the most relevant items first."""

    def __init__(
        self,
        aggregator: Optional[BriefingContextAggregator] = None,
        relevance: Optional[RelevanceEngine] = None,
    ) -> None:
        self.aggregator = aggregator or BriefingContextAggregator(default_providers())
        self.relevance = relevance or RelevanceEngine()

    def collect(self, now: Optional[object] = None) -> BriefingContext:
        context = self.aggregator.collect(now)
        ranked = self.relevance.rank(context)
        return BriefingContext(
            collected_at=context.collected_at,
            reports=context.reports,
            items=ranked,
            metadata={**dict(context.metadata), "relevance_ranked": True},
            unavailable_sources=context.unavailable_sources,
        )
