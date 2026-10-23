from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SocialAccount:
    """Representation of a local social account."""

    account_id: str
    platform: str
    display_name: str
    account_type: str = "MAIN"
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "platform": self.platform,
            "display_name": self.display_name,
            "account_type": self.account_type,
            "enabled": self.enabled,
            "metadata": dict(self.metadata or {}),
        }


@dataclass
class SocialMessage:
    """Normalized social message used across providers."""

    message_id: str
    account_id: str
    conversation_id: str
    sender: str
    timestamp: str
    text: str
    unread: Optional[bool] = None
    category: str = "UNKNOWN"
    priority: str = "LOW"
    requires_attention: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "account_id": self.account_id,
            "conversation_id": self.conversation_id,
            "sender": self.sender,
            "timestamp": self.timestamp,
            "text": self.text,
            "unread": self.unread,
            "category": self.category,
            "priority": self.priority,
            "requires_attention": self.requires_attention,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_raw(cls, raw: Dict[str, Any], *, account_id: Optional[str] = None) -> "SocialMessage":
        data = dict(raw or {})
        normalized = {
            "message_id": str(data.get("message_id") or data.get("id") or f"msg-{abs(hash(str(data))) % 1000000}"),
            "account_id": str(data.get("account_id") or account_id or "unknown"),
            "conversation_id": str(data.get("conversation_id") or data.get("thread_id") or data.get("account_id") or account_id or "conversation"),
            "sender": str(data.get("sender") or data.get("from") or "unknown"),
            "timestamp": str(data.get("timestamp") or data.get("time") or "1970-01-01T00:00:00Z"),
            "text": str(data.get("text") or data.get("message") or ""),
            "unread": data.get("unread") if isinstance(data.get("unread"), bool) else None,
            "category": str(data.get("category") or "UNKNOWN"),
            "priority": str(data.get("priority") or "LOW"),
            "requires_attention": bool(data.get("requires_attention", False)),
            "metadata": dict(data.get("metadata") or {}),
        }
        return cls(**normalized)
