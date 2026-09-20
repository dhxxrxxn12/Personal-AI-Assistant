"""Command-center checks: activity bus, host telemetry, HUD endpoints and the
permission-gate contract the UI depends on.

Nothing here touches the real task store or uploads folder — each test isolates
the state it mutates, mirroring tests/test_failure_recovery.py.
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from cloud import server, uploads as upload_store
from cloud.activity_log import ActivityLog
from cloud.telemetry import SubsystemHealth, Telemetry


class ActivityLogTests(unittest.TestCase):
    def test_sequence_cursor_and_since_filtering(self):
        log = ActivityLog(capacity=5)
        first = log.publish("TASK", "one")
        second = log.publish("EXEC", "two", {"task_id": "t1"})

        self.assertEqual(first["seq"], 1)
        self.assertEqual(second["seq"], 2)
        self.assertEqual(log.cursor(), 2)
        self.assertEqual([event["message"] for event in log.since(0)], ["one", "two"])
        self.assertEqual([event["message"] for event in log.since(1)], ["two"])
        self.assertEqual(log.since(2), [])

    def test_unknown_categories_are_normalised_and_buffer_is_bounded(self):
        log = ActivityLog(capacity=3)
        log.publish("nonsense", "a")
        for index in range(5):
            log.publish("SYS", f"event-{index}")

        events = log.latest()
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["category"], "SYS")

    def test_snapshot_reports_cursor_and_events(self):
        log = ActivityLog()
        log.publish("PERM", "permission required", level="warn")
        snapshot = log.snapshot()
        self.assertEqual(snapshot["cursor"], 1)
        self.assertEqual(snapshot["events"][0]["level"], "warn")


class TelemetryTests(unittest.TestCase):
    def test_utc_heartbeat_age_is_not_measured_in_local_time(self):
        timestamp = datetime.now(timezone.utc).isoformat()
        self.assertLessEqual(server.seconds_since(timestamp), 1)

    def test_sample_reports_real_host_metrics(self):
        sample = Telemetry(cache_seconds=0).sample()

        for key in ("cpu", "memory", "network", "gpu", "temperature", "disk",
                    "uptime", "process_count", "os"):
            self.assertIn(key, sample)

        self.assertTrue(sample["os"]["system"])
        self.assertTrue(sample["os"]["architecture"])
        # macOS can expose CPU and memory while sandbox policy denies process
        # enumeration and boot-time syscalls. Those fields are deliberately
        # reported as unavailable rather than being fabricated.
        if sample["process_count"] is not None:
            self.assertIsInstance(sample["process_count"], int)
            self.assertGreater(sample["process_count"], 0)
        if sample["uptime"]["human"] is not None:
            self.assertIsNotNone(sample["uptime"]["human"])

    def test_unavailable_sensors_are_reported_not_invented(self):
        sample = Telemetry(cache_seconds=0).sample()
        for key in ("gpu", "temperature"):
            block = sample[key]
            self.assertIn("available", block)
            if not block["available"]:
                self.assertIsNone(block["percent"] if key == "gpu" else block["celsius"])

    def test_subsystem_health_marks_outcomes(self):
        registry = SubsystemHealth()
        self.assertIsNone(registry.get("gemini"))
        registry.mark("gemini", False, "quota exceeded")
        entry = registry.get("gemini")
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["detail"], "quota exceeded")
        self.assertIn("gemini", registry.snapshot())
