from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional

from .models import SocialAccount, SocialMessage


class SocialProvider(ABC):
    """Common abstraction for local social data providers."""

    @abstractmethod
    def get_accounts(self) -> List[SocialAccount]:
        raise NotImplementedError

    def get_inbox(self, account_id: Optional[str] = None) -> List[SocialMessage]:
        return []

    def get_notifications(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    def get_comments(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    def get_mentions(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    def get_account_summary(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        return {"account_id": account_id, "status": "UNAVAILABLE"}

    def get_content_metrics(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        return {"account_id": account_id, "status": "UNAVAILABLE"}


class MockSocialProvider(SocialProvider):
    """Deterministic local provider used for tests and local development."""

    def __init__(self, accounts: Optional[Iterable[SocialAccount]] = None, inbox: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        self._accounts = list(accounts or [
            SocialAccount("main-account", "instagram", "Main Account", "MAIN", True, {"source": "mock"}),
            SocialAccount("secondary-account", "instagram", "Secondary Account", "SECONDARY", True, {"source": "mock"}),
        ])
        self._inbox = {
            "main-account": [
                {"message_id": "m1", "account_id": "main-account", "conversation_id": "c1", "sender": "@example", "timestamp": "2026-10-08T09:00:00Z", "text": "Hey! I would love to collaborate on a reel.", "unread": True, "category": "COLLABORATION", "priority": "HIGH", "requires_attention": True},
                {"message_id": "m2", "account_id": "main-account", "conversation_id": "c2", "sender": "@fan", "timestamp": "2026-10-08T08:40:00Z", "text": "Can you share your rates for a brand collab?", "unread": True, "category": "QUESTION", "priority": "HIGH", "requires_attention": True},
                {"message_id": "m3", "account_id": "main-account", "conversation_id": "c3", "sender": "@scammer", "timestamp": "2026-10-08T07:10:00Z", "text": "You won a free iPhone! Click here now.", "unread": False, "category": "SPAM", "priority": "LOW", "requires_attention": False},
                {"message_id": "m4", "account_id": "main-account", "conversation_id": "c4", "sender": "@friend", "timestamp": "2026-10-08T06:00:00Z", "text": "Hey, just checking in.", "unread": True, "category": "PERSONAL", "priority": "LOW", "requires_attention": False},
            ],
            "secondary-account": [
                {"message_id": "s1", "account_id": "secondary-account", "conversation_id": "s-1", "sender": "@client", "timestamp": "2026-10-08T09:20:00Z", "text": "Can we schedule a call this week?", "unread": True, "category": "QUESTION", "priority": "MEDIUM", "requires_attention": True},
                {"message_id": "s2", "account_id": "secondary-account", "conversation_id": "s-2", "sender": "@promo", "timestamp": "2026-10-08T09:00:00Z", "text": "Earn cash fast with a simple click.", "unread": True, "category": "SPAM", "priority": "LOW", "requires_attention": False},
                {"message_id": "s3", "account_id": "secondary-account", "conversation_id": "s-3", "sender": "@support", "timestamp": "2026-10-08T08:00:00Z", "text": "Hi, I need help with a recorded reel upload.", "unread": True, "category": "SUPPORT", "priority": "HIGH", "requires_attention": True},
            ],
        }
        if inbox is not None:
            for key, messages in (inbox or {}).items():
                self._inbox[str(key)] = list(messages)

    def get_accounts(self) -> List[SocialAccount]:
        return list(self._accounts)

    def get_inbox(self, account_id: Optional[str] = None) -> List[SocialMessage]:
        target = str(account_id) if account_id is not None else None
        if target is None:
            messages: List[SocialMessage] = []
            for account in self._accounts:
                messages.extend(self.get_inbox(account.account_id))
            return messages
        rows = self._inbox.get(target, [])
        return [SocialMessage.from_raw(row, account_id=target) for row in rows]

    def get_account_summary(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        if account_id is None:
            return {"status": "OK", "accounts": len(self._accounts)}
        for account in self._accounts:
            if account.account_id == account_id:
                return {"account_id": account.account_id, "status": "OK", "platform": account.platform, "type": account.account_type}
        return {"account_id": account_id, "status": "UNAVAILABLE"}

    def get_content_metrics(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        return {"account_id": account_id, "status": "UNAVAILABLE"}


class InstagramProvider(SocialProvider):
    """Instagram adapter boundary for future authorized integrations."""

    def __init__(self, *, enabled: bool = False, authorized_data: Optional[Dict[str, Any]] = None):
        self.enabled = bool(enabled)
        self.authorized_data = dict(authorized_data or {})

    def get_accounts(self) -> List[SocialAccount]:
        if not self.enabled:
            return []
        return [
            SocialAccount("main-account", "instagram", "Main Account", "MAIN", True, {"status": "authorized"}),
            SocialAccount("secondary-account", "instagram", "Secondary Account", "SECONDARY", True, {"status": "authorized"}),
        ]

    def get_inbox(self, account_id: Optional[str] = None) -> List[SocialMessage]:
        if not self.enabled:
            return []
        data = list(self.authorized_data.get("inbox") or [])
        if account_id is not None:
            data = [item for item in data if str(item.get("account_id") or item.get("accountId")) == str(account_id)]
        return [SocialMessage.from_raw(item, account_id=(account_id or item.get("account_id") or item.get("accountId"))) for item in data]

    def get_account_summary(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        if not self.enabled:
            return {"account_id": account_id, "status": "UNAVAILABLE", "reason": "Instagram integration not configured."}
        return {"account_id": account_id, "status": "OK", "platform": "instagram", "source": "authorized_api"}

    def get_content_metrics(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        if not self.enabled:
            return {"account_id": account_id, "status": "UNAVAILABLE", "reason": "Instagram metrics unavailable without authorization."}
        return {"account_id": account_id, "status": "UNAVAILABLE", "reason": "No metrics are currently exposed by this local adapter."}
