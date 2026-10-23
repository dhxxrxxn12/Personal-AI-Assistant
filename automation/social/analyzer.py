from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .models import SocialAccount, SocialMessage


class SocialAnalyzer:
    """Deterministic local analyzer for inbox and account activity."""

    def normalize_message(self, raw: Dict[str, Any], *, account_id: Optional[str] = None) -> SocialMessage:
        message = SocialMessage.from_raw(raw, account_id=account_id)
        message.category = self.classify_category(message.text)
        message.priority = self.classify_priority(message)
        message.requires_attention = self.requires_attention(message)
        return message

    def classify_category(self, text: str) -> str:
        cleaned = (text or "").strip()
        lowered = cleaned.lower()
        if any(token in lowered for token in ("collab", "collaboration", "brand deal", "sponsor", "partnership")):
            return "COLLABORATION"
        if any(token in lowered for token in ("support", "help", "issue", "bug", "cannot", "problem")):
            return "SUPPORT"
        if any(token in lowered for token in ("question", "can you", "could you", "when", "how much", "what is", "want to", "schedule")):
            return "QUESTION"
        if any(token in lowered for token in ("free", "winner", "click here", "claim now", "lottery", "urgent", "cash fast", "limited time")):
            return "POSSIBLE_SCAM"
        if any(token in lowered for token in ("spam", "promo", "scam", "fake", "link", "crypto")):
            return "SPAM"
        if any(token in lowered for token in ("hi", "hey", "thanks", "good morning")):
            return "PERSONAL"
        return "UNKNOWN"

    def classify_priority(self, message: SocialMessage) -> str:
        category = (message.category or "UNKNOWN").upper()
        if category in {"COLLABORATION", "SUPPORT", "QUESTION"}:
            return "HIGH" if message.unread else "MEDIUM"
        if category in {"POSSIBLE_SCAM", "SPAM"}:
            return "LOW"
        if category == "PERSONAL":
            return "LOW"
        return "MEDIUM" if message.unread else "LOW"

    def requires_attention(self, message: SocialMessage) -> bool:
        if not message.unread:
            return False
        category = (message.category or "UNKNOWN").upper()
        if category in {"COLLABORATION", "QUESTION", "SUPPORT"}:
            return True
        if category == "POSSIBLE_SCAM":
            return False
        return category == "UNKNOWN" and message.priority.upper() in {"HIGH", "MEDIUM"}

    def analyze_account(self, account: SocialAccount, messages: Iterable[SocialMessage | Dict[str, Any]]) -> Dict[str, Any]:
        normalized = []
        for item in messages:
            if isinstance(item, SocialMessage):
                message = item
            else:
                message = self.normalize_message(item, account_id=account.account_id)
            message.category = self.classify_category(message.text)
            message.priority = self.classify_priority(message)
            message.requires_attention = self.requires_attention(message)
            normalized.append(message)

        unread = [m for m in normalized if m.unread]
        unread_unknown = [m for m in normalized if m.unread is None]
        attention = [m for m in unread if m.requires_attention]
        questions = [m for m in unread if m.category == "QUESTION"]
        collaboration = [m for m in unread if m.category == "COLLABORATION"]
        spam = [m for m in normalized if m.category in {"SPAM", "POSSIBLE_SCAM"}]
        low_priority = [
            m for m in normalized
            if m.priority == "LOW"
            and not m.requires_attention
            and m.category not in {"SPAM", "POSSIBLE_SCAM"}
        ]
        normal = [
            m for m in unread
            if not m.requires_attention and m.category not in {"SPAM", "POSSIBLE_SCAM"}
        ]

        return {
            "account_id": account.account_id,
            "platform": account.platform,
            "account_type": account.account_type,
            "display_name": account.display_name,
            "enabled": account.enabled,
            "message_count": len(normalized),
            "unread_message_count": len(unread),
            "unread_state_unknown_count": len(unread_unknown),
            "new_conversations": len({m.conversation_id for m in unread}),
            "messages_requiring_attention": len(attention),
            "question_count": len(questions),
            "collaboration_count": len(collaboration),
            "low_priority_count": len(low_priority),
            "normal_count": len(normal),
            "spam_count": len(spam),
            "messages": [m.to_dict() for m in sorted(normalized, key=lambda item: item.timestamp, reverse=True)],
        }

    def analyze_accounts(self, accounts: Iterable[SocialAccount], messages_by_account: Dict[str, List[SocialMessage | Dict[str, Any]]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for account in accounts:
            rows = messages_by_account.get(account.account_id, [])
            results.append(self.analyze_account(account, rows))
        return results
