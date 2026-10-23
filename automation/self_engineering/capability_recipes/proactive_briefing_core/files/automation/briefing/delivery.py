"""Replaceable local delivery channels.

Every channel reports whether it actually delivered.  Nothing here reaches the
network: the console writes locally, the recording channel keeps briefings in
memory for verification, the voice channel calls an application-supplied local
callback, and the system notification reuses the same local OS notification
method as ``actions/reminder.py`` (opt-in only).
"""

from __future__ import annotations

import platform
import subprocess
from typing import Callable, Iterable, List, Optional, Sequence

from .models import Briefing, DeliveryResult


class DeliveryChannel:
    """A named way to deliver a briefing locally."""

    name = "channel"

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def deliver(self, briefing: Briefing) -> DeliveryResult:
        raise NotImplementedError


class ConsoleDelivery(DeliveryChannel):
    name = "console"

    def __init__(self, writer: Optional[Callable[[str], None]] = None) -> None:
        self.writer = writer or print

    def deliver(self, briefing: Briefing) -> DeliveryResult:
        text = briefing.plain_text
        try:
            self.writer(text)
        except Exception as exc:
            return DeliveryResult(self.name, False, f"console write failed: {type(exc).__name__}")
        return DeliveryResult(self.name, True, f"{len(text)} characters written to the local console")


class RecordingDelivery(DeliveryChannel):
    """Verification/test channel: records briefings in memory and writes nothing."""

    name = "recording"

    def __init__(self) -> None:
        self.briefings: List[Briefing] = []

    def deliver(self, briefing: Briefing) -> DeliveryResult:
        self.briefings.append(briefing)
        return DeliveryResult(self.name, True, "briefing recorded in memory")

    def last(self) -> Optional[Briefing]:
        return self.briefings[-1] if self.briefings else None


class VoiceDelivery(DeliveryChannel):
    """Speaks through an existing local voice callback supplied by the host."""

    name = "voice"

    def __init__(self, speak: Optional[Callable[[str], None]] = None) -> None:
        self.speak = speak

    def available(self) -> bool:
        return callable(self.speak)

    def unavailable_reason(self) -> str:
        return "no local voice callback configured"

    def deliver(self, briefing: Briefing) -> DeliveryResult:
        if not callable(self.speak):
            return DeliveryResult(self.name, False, self.unavailable_reason())
        try:
            self.speak(briefing.plain_text)
        except Exception as exc:
            return DeliveryResult(self.name, False, f"voice delivery failed: {type(exc).__name__}")
        return DeliveryResult(self.name, True, "spoken through the local voice interface")


class SystemNotificationDelivery(DeliveryChannel):
    """Reuses the local OS notification method already used by the reminder action."""

    name = "system_notification"

    def __init__(self, *, enabled: bool = False, title: str = "J.A.R.V.I.S Briefing") -> None:
        self.enabled = bool(enabled)
        self.title = title

    def available(self) -> bool:
        return self.enabled and platform.system() in {"Darwin", "Linux", "Windows"}

    def unavailable_reason(self) -> str:
        if not self.enabled:
            return "system notifications are disabled by default"
        return "no supported local notification mechanism on this platform"

    def deliver(self, briefing: Briefing) -> DeliveryResult:
        if not self.available():
            return DeliveryResult(self.name, False, self.unavailable_reason())
        body = briefing.greeting
        system = platform.system()
        if system == "Darwin":
            command = ["osascript", "-e", f"display notification {body!r} with title {self.title!r}"]
        elif system == "Linux":
            command = ["notify-send", self.title, body]
        else:
            command = ["msg", "*", body]
        try:
            completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            return DeliveryResult(self.name, False, f"local notification failed: {type(exc).__name__}")
        if completed.returncode != 0:
            return DeliveryResult(self.name, False, "local notification command was rejected")
        return DeliveryResult(self.name, True, "delivered through the local OS notification")


class DeliveryRegistry:
    """Delivers one briefing to the requested channels, in a stable order."""

    def __init__(self, channels: Iterable[DeliveryChannel]) -> None:
        self.channels = {channel.name: channel for channel in channels}

    def names(self) -> List[str]:
        return sorted(self.channels)

    def get(self, name: str) -> Optional[DeliveryChannel]:
        return self.channels.get(name)

    def deliver(self, briefing: Briefing, channels: Optional[Sequence[str]] = None) -> List[DeliveryResult]:
        requested = list(channels) if channels else sorted(self.channels)
        results: List[DeliveryResult] = []
        for name in requested:
            channel = self.channels.get(name)
            if channel is None:
                results.append(DeliveryResult(name, False, "unknown delivery channel"))
                continue
            if not channel.available():
                results.append(DeliveryResult(name, False, channel.unavailable_reason() or "channel unavailable"))
                continue
            results.append(channel.deliver(briefing))
        return results


def default_channels(
    *,
    writer: Optional[Callable[[str], None]] = None,
    speak: Optional[Callable[[str], None]] = None,
    allow_system_notification: bool = False,
) -> DeliveryRegistry:
    """The local channel set; nothing external is registered."""
    return DeliveryRegistry((
        ConsoleDelivery(writer),
        RecordingDelivery(),
        VoiceDelivery(speak),
        SystemNotificationDelivery(enabled=allow_system_notification),
    ))
