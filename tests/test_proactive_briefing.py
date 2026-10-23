from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from automation.briefing.config_store import BriefingConfigStore
from automation.briefing.context_engine import ContextEngine, RelevanceEngine
from automation.briefing.delivery import ConsoleDelivery, DeliveryRegistry, VoiceDelivery
from automation.briefing.generator import BriefingGenerator
from automation.briefing.ledger import BriefingLedger
from automation.briefing.models import (
    Briefing,
    BriefingContext,
    BriefingDefinition,
    BriefingRunRecord,
    BriefingSection,
    ContextItem,
    ProviderReport,
    ProviderState,
    RunStatus,
)
from automation.briefing.providers import (
    BriefingContextAggregator,
    ReminderProvider,
    TaskProvider,
    WeatherProvider,
    default_providers,
)
from automation.briefing.scheduler import BriefingScheduler
from automation.briefing.service import BriefingService, resolve_request


def test_briefing_definition_and_run_model():
    definition = BriefingDefinition(
        definition_id="morning-briefing",
        name="Morning briefing",
        time_of_day="08:00",
        timezone="UTC",
        channels=("console",),
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
    ).validate()

    briefing = Briefing(
        definition_id=definition.definition_id,
        generated_at="2024-01-01T08:00:00+00:00",
        greeting="Good morning, Alex.",
        sections=(
            BriefingSection("Weather", ("24°C, partly cloudy",)),
            BriefingSection("Tasks", ("Finish AI assignment",)),
        ),
        notes=("Gym at 7 PM",),
    )

    assert definition.time_of_day == "08:00"
    assert "24°C" in briefing.plain_text
    assert "Finish AI assignment" in briefing.plain_text
    assert "Gym at 7 PM" in briefing.plain_text


def test_context_aggregation_collects_local_providers(tmp_path):
    reminder_dir = tmp_path / "reminders"
    reminder_dir.mkdir()
    (reminder_dir / "gym.py").write_text("print('gym')\n", encoding="utf-8")
    memory_path = tmp_path / "learning_memory.json"
    memory_path.write_text(
        json.dumps({
            "workflow_1": {"name": "Finish AI assignment", "success_count": 3},
            "workflow_2": {"name": "Prepare meeting", "success_count": 1},
        }),
        encoding="utf-8",
    )
    weather_path = tmp_path / "weather_cache.json"
    weather_path.write_text(
        json.dumps({"summary": "partly cloudy", "temperature": "24°C", "location": "San Francisco"}),
        encoding="utf-8",
    )

    aggregator = BriefingContextAggregator(default_providers(
        reminder_dir=reminder_dir,
        memory_path=memory_path,
        weather_cache=weather_path,
    ))
    context = aggregator.collect(datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc))

    assert context.by_name("reminders").state == ProviderState.AVAILABLE.value
    assert context.by_name("weather").state == ProviderState.AVAILABLE.value
    assert context.by_name("tasks").state == ProviderState.AVAILABLE.value
    assert context.by_name("calendar").state == ProviderState.FUTURE.value
    assert any("24°C" in item["weather"] for item in context.by_name("weather").items)


def test_context_collection_handles_provider_failure_and_partial_data(tmp_path):
    class BrokenProvider:
        name = "weather"

        def available(self):
            return True

        def collect(self):
            raise RuntimeError("boom")

    reports = [
        ProviderReport(provider="tasks", state=ProviderState.AVAILABLE.value, items=({"workflow": "Finish AI assignment"},)),
        ProviderReport(provider="weather", state=ProviderState.UNAVAILABLE.value, reason="weather cache unreadable"),
    ]
    context = BriefingContext(collected_at="2024-01-02T08:00:00+00:00", reports=tuple(reports))
    assert context.by_name("tasks").available is True
    assert context.by_name("weather").available is False

    aggregator = BriefingContextAggregator((BrokenProvider(),))
    collected = aggregator.collect(datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc))
    assert collected.by_name("weather").state == ProviderState.UNAVAILABLE.value
    assert "provider failed" in collected.by_name("weather").reason


def test_generator_uses_partial_context_without_fabrication():
    definition = BriefingDefinition(
        definition_id="morning-briefing",
        name="Alex",
        time_of_day="08:00",
        timezone="UTC",
        channels=("console",),
    ).validate()
    context = BriefingContext(
        collected_at="2024-01-02T08:00:00+00:00",
        reports=(
            ProviderReport(provider="tasks", state=ProviderState.AVAILABLE.value, items=({"workflow": "Finish AI assignment"},)),
            ProviderReport(provider="weather", state=ProviderState.UNAVAILABLE.value, reason="weather cache missing"),
        ),
    )

    briefing = BriefingGenerator().generate(definition, context, now=datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc))

    assert "Finish AI assignment" in briefing.plain_text
    assert "weather" in "\n".join(briefing.notes).lower()
    assert "weather" in briefing.plain_text.lower()
    assert "no local context is available yet" not in briefing.plain_text.lower()


def test_scheduler_configuration_and_due_logic_handles_timezone_and_missed_runs():
    definition = BriefingDefinition(
        definition_id="morning-briefing",
        name="Morning briefing",
        time_of_day="08:00",
        timezone="America/New_York",
        enabled=True,
        channels=("console",),
        grace_minutes=120,
    ).validate()
    scheduler = BriefingScheduler(definition)
    now = datetime(2024, 1, 2, 13, 30, tzinfo=timezone.utc)

    assert scheduler.next_run(now).isoformat().startswith("2024-01-03T08:00:00")
    assert scheduler.is_due(now, last_run=None) is True
    assert scheduler.is_due(datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc), last_run=None) is False

    missed = scheduler.missed_runs(datetime(2024, 1, 3, 9, 0, tzinfo=timezone.utc), since=datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc))
    assert missed
    assert all(slot.tzinfo is not None for slot in missed)

    disabled = BriefingDefinition(
        definition_id="disabled-briefing",
        name="Disabled",
        time_of_day="09:00",
        timezone="UTC",
        enabled=False,
        channels=("console",),
    ).validate()
    assert BriefingScheduler(disabled).next_run(datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)) is None


def test_briefing_config_store_persists_restart_safe_configuration(tmp_path):
    store = BriefingConfigStore(tmp_path / "briefing_config.json")
    definition = BriefingDefinition(
        definition_id="morning-briefing",
        name="Morning briefing",
        time_of_day="08:00",
        timezone="UTC",
        channels=("console",),
    ).validate()

    saved = store.save(definition)
    assert store.get("morning-briefing") == saved
    assert store.set_enabled("morning-briefing", False).enabled is False
    assert store.summary()[0]["enabled"] is False

    reloaded = BriefingConfigStore(tmp_path / "briefing_config.json")
    assert reloaded.get("morning-briefing").enabled is False


def test_briefing_ledger_blocks_duplicate_runs_and_tracks_restart_recovery(tmp_path):
    ledger = BriefingLedger(tmp_path / "briefing_ledger.json")
    record = BriefingRunRecord(
        definition_id="morning-briefing",
        scheduled_for="2024-01-02T08:00:00+00:00",
        executed_at="2024-01-02T08:00:05+00:00",
        status=RunStatus.DELIVERED.value,
        duplicate=False,
        detail="delivered",
    )

    ledger.record(record)
    assert ledger.has_run("morning-briefing", "2024-01-02T08:00:00+00:00") is True
    assert ledger.last_run("morning-briefing") is not None

    duplicate = BriefingRunRecord(
        definition_id="morning-briefing",
        scheduled_for="2024-01-02T08:00:00+00:00",
        executed_at="2024-01-02T08:06:00+00:00",
        status=RunStatus.SKIPPED.value,
        duplicate=True,
        detail="duplicate prevented",
    )
    ledger.record(duplicate)
    assert ledger.records()[-1].duplicate is True
    assert ledger.last_run("morning-briefing") is not None


def test_local_delivery_channels_write_and_speak(tmp_path):
    packets = []
    console = ConsoleDelivery(writer=lambda text: packets.append(text))
    voice = VoiceDelivery(speak=lambda text: packets.append(f"VOICE:{text}"))
    registry = DeliveryRegistry((console, voice))

    briefing = Briefing(
        definition_id="morning-briefing",
        generated_at="2024-01-02T08:00:00+00:00",
        greeting="Good morning, Alex.",
        sections=(BriefingSection("Weather", ("24°C, partly cloudy",)),),
    )

    outputs = registry.deliver(briefing, channels=("console", "voice"))
    assert outputs[0].delivered is True
    assert outputs[1].delivered is True
    assert any("Good morning, Alex." in packet for packet in packets)


def test_service_end_to_end_flow_uses_local_context_and_persists_results(tmp_path):
    reminder_dir = tmp_path / "reminders"
    reminder_dir.mkdir()
    (reminder_dir / "gym.py").write_text("print('gym')\n", encoding="utf-8")
    memory_path = tmp_path / "learning_memory.json"
    memory_path.write_text(json.dumps({"workflow_1": {"name": "Finish AI assignment", "success_count": 5}}), encoding="utf-8")
    weather_path = tmp_path / "weather_cache.json"
    weather_path.write_text(json.dumps({"summary": "partly cloudy", "temperature": "24°C"}), encoding="utf-8")

    service = BriefingService(
        state_dir=tmp_path,
        aggregator=BriefingContextAggregator(default_providers(
            reminder_dir=reminder_dir,
            memory_path=memory_path,
            weather_cache=weather_path,
        )),
        clock=lambda: datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc),
    )

    definition = service.configure_from_request("08:00 morning", definition_id="morning-briefing", timezone_name="UTC")
    context = service.load_context(now=datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc))
    briefing = service.generate("morning-briefing", context=context, now=datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc))

    assert definition.time_of_day == "08:00"
    assert "Finish AI assignment" in briefing.plain_text
    assert "partly cloudy" in briefing.plain_text.lower()
    assert service.definition("morning-briefing").enabled is True


def test_resolve_request_parses_fixed_times_and_falls_back_to_words():
    assert resolve_request("08:00 morning", definition_id="briefing", timezone_name="UTC").time_of_day == "08:00"
    assert resolve_request("morning briefing", definition_id="briefing", timezone_name="UTC").time_of_day == "08:00"
    assert resolve_request("evening", definition_id="briefing", timezone_name="UTC").time_of_day == "18:00"


def test_persistence_and_safe_shutdown_are_deterministic(tmp_path):
    store = BriefingConfigStore(tmp_path / "briefing_config.json")
    definition = BriefingDefinition(
        definition_id="morning-briefing",
        name="Morning briefing",
        time_of_day="08:00",
        timezone="UTC",
        enabled=True,
        channels=("console",),
    ).validate()
    store.save(definition)

    ledger = BriefingLedger(tmp_path / "briefing_ledger.json")
    ledger.record(BriefingRunRecord(
        definition_id="morning-briefing",
        scheduled_for="2024-01-02T08:00:00+00:00",
        executed_at="2024-01-02T08:00:01+00:00",
        status=RunStatus.DELIVERED.value,
        duplicate=False,
        detail="complete",
    ))

    assert store.get("morning-briefing") is not None
    assert ledger.last_run("morning-briefing") is not None


def test_voice_delivery_reports_unavailable_without_callback():
    channel = VoiceDelivery(speak=None)
    assert channel.available() is False
    assert channel.deliver(Briefing(
        definition_id="d",
        generated_at="2024-01-02T08:00:00+00:00",
        greeting="Hi",
    )).delivered is False


def test_context_engine_normalizes_and_ranks_local_context():
    context = BriefingContext(
        collected_at="2024-01-02T08:00:00+00:00",
        reports=(
            ProviderReport(
                provider="reminders",
                state=ProviderState.AVAILABLE.value,
                items=({"reminder": "Gym at 7 PM", "timestamp": "2024-01-02T19:00:00+00:00"},),
            ),
            ProviderReport(
                provider="weather",
                state=ProviderState.AVAILABLE.value,
                items=({"weather": "24°C, partly cloudy", "timestamp": "2024-01-02T08:00:00+00:00"},),
            ),
        ),
        items=(
            ContextItem(source="reminders", category="reminders", title="Gym at 7 PM", content="Gym at 7 PM", priority="high"),
            ContextItem(source="weather", category="weather", title="Weather", content="24°C, partly cloudy", priority="low"),
        ),
    )

    ranked = RelevanceEngine().rank(context)
    assert ranked and ranked[0].source == "reminders"
    assert ranked[0].title == "Gym at 7 PM"
    assert ranked[1].category == "weather"


def test_service_uses_context_engine_for_relevance_ordering(tmp_path):
    reminder_dir = tmp_path / "reminders"
    reminder_dir.mkdir()
    (reminder_dir / "gym.py").write_text("print('gym')\n", encoding="utf-8")
    memory_path = tmp_path / "learning_memory.json"
    memory_path.write_text(json.dumps({"workflow_1": {"name": "Finish AI assignment", "success_count": 4}}), encoding="utf-8")
    weather_path = tmp_path / "weather_cache.json"
    weather_path.write_text(json.dumps({"summary": "partly cloudy", "temperature": "24°C"}), encoding="utf-8")

    service = BriefingService(
        state_dir=tmp_path,
        aggregator=BriefingContextAggregator(default_providers(
            reminder_dir=reminder_dir,
            memory_path=memory_path,
            weather_cache=weather_path,
        )),
        clock=lambda: datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc),
    )
    service.configure_from_request("08:00 morning", definition_id="morning-briefing", timezone_name="UTC")
    context = service.load_context(now=datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc))

    assert any(item.category == "reminders" for item in context.items)
    assert any(item.category == "tasks" for item in context.items)
    scores = [item.priority_score for item in context.items]
    assert scores == sorted(scores, reverse=True)
    assert "Finish AI assignment" in service.generate(
        "morning-briefing",
        context=context,
        now=datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc),
    ).plain_text
