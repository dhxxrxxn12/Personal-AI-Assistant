"""Local proactive briefing subsystem."""

from .config_store import BriefingConfigStore
from .context_engine import ContextEngine, RelevanceEngine
from .delivery import ConsoleDelivery, DeliveryRegistry, RecordingDelivery, SystemNotificationDelivery, VoiceDelivery
from .generator import BriefingGenerator
from .ledger import BriefingLedger
from .models import (
    Briefing,
    BriefingContext,
    BriefingDefinition,
    BriefingRunRecord,
    BriefingSection,
    ContextItem,
    DeliveryResult,
    ProviderReport,
    ProviderState,
    RunStatus,
)
from .providers import BriefingContextAggregator, CalendarProvider, EmailProvider, ReminderProvider, TaskProvider, WeatherProvider, default_providers
from .scheduler import BriefingScheduler
from .service import BriefingService, resolve_request

__all__ = [
    "Briefing",
    "BriefingConfigStore",
    "BriefingContext",
    "BriefingContextAggregator",
    "BriefingDefinition",
    "BriefingGenerator",
    "BriefingLedger",
    "BriefingRunRecord",
    "BriefingScheduler",
    "BriefingSection",
    "BriefingService",
    "CalendarProvider",
    "ContextEngine",
    "ContextItem",
    "ConsoleDelivery",
    "DeliveryRegistry",
    "DeliveryResult",
    "EmailProvider",
    "ProviderReport",
    "ProviderState",
    "RecordingDelivery",
    "RelevanceEngine",
    "ReminderProvider",
    "RunStatus",
    "SystemNotificationDelivery",
    "TaskProvider",
    "VoiceDelivery",
    "WeatherProvider",
    "default_providers",
    "resolve_request",
]
