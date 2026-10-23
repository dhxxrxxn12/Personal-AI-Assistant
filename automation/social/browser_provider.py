from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urljoin, urlparse

from .browser_session import BrowserControlSession, BrowserSession
from .models import SocialAccount, SocialMessage
from .providers import SocialProvider


INSTAGRAM_INBOX_URL = "https://www.instagram.com/direct/inbox/"


class AuthenticationState(str, Enum):
    AUTHENTICATED = "AUTHENTICATED"
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    SECURITY_CHALLENGE = "SECURITY_CHALLENGE"
    UNKNOWN = "UNKNOWN"


def _default_accounts() -> List[SocialAccount]:
    return [
        SocialAccount(
            "instagram_main",
            "instagram",
            "Main",
            "MAIN",
            True,
            {"browser": "chrome"},
        ),
        SocialAccount(
            "instagram_secondary",
            "instagram",
            "Secondary",
            "SECONDARY",
            True,
            {"browser": "firefox"},
        ),
    ]


class InstagramBrowserProvider(SocialProvider):
    """Read visible Instagram inbox previews from configured authorized sessions.

    The provider never asks for or handles credentials. Browser profiles must be
    signed in by the user. Only rendered, visible conversation-list content is read.
    """

    _THREAD_SELECTOR = 'a[href*="/direct/t/"]'

    def __init__(
        self,
        *,
        accounts: Optional[Sequence[SocialAccount]] = None,
        session_factory: Optional[Callable[[SocialAccount], BrowserSession]] = None,
    ):
        self._accounts = list(accounts) if accounts is not None else _default_accounts()
        self._session_factory = session_factory or self._create_browser_session
        self.account_states: Dict[str, Dict[str, str]] = {}
        self._sessions: Dict[str, BrowserSession] = {}

    @staticmethod
    def _create_browser_session(account: SocialAccount) -> BrowserSession:
        browser_name = str((account.metadata or {}).get("browser") or "").strip() or None
        return BrowserControlSession(browser_name=browser_name)

    def get_accounts(self) -> List[SocialAccount]:
        return [account for account in self._accounts if account.enabled]

    def _session(self, account: SocialAccount) -> BrowserSession:
        if account.account_id not in self._sessions:
            self._sessions[account.account_id] = self._session_factory(account)
        return self._sessions[account.account_id]

    @staticmethod
    def _authentication_state(session: BrowserSession, url: str, text: str) -> AuthenticationState:
        lowered_url = (url or "").lower()
        lowered_text = (text or "").lower()
        hostname = (urlparse(lowered_url).hostname or "").lower()
        if any(part in lowered_url for part in ("/challenge/", "/checkpoint/")) or any(
            marker in lowered_text
            for marker in ("security challenge", "confirm it's you", "confirm it is you", "suspicious login")
        ):
            return AuthenticationState.SECURITY_CHALLENGE
        if "/accounts/login" in lowered_url or "/login" in lowered_url or (
            "password" in lowered_text and any(label in lowered_text for label in ("log in", "login"))
        ):
            return AuthenticationState.LOGIN_REQUIRED
        explicit_state = getattr(session, "authentication_state", None)
        if explicit_state is not None:
            try:
                state = AuthenticationState(str(getattr(explicit_state, "value", explicit_state)).upper())
            except ValueError:
                state = AuthenticationState.UNKNOWN
            if state != AuthenticationState.UNKNOWN:
                return state
        if hostname not in {"instagram.com", "www.instagram.com"}:
            return AuthenticationState.NOT_AUTHENTICATED
        if any(marker in lowered_text for marker in ("messages", "primary", "general")):
            return AuthenticationState.AUTHENTICATED
        return AuthenticationState.UNKNOWN

    @staticmethod
    def _valid_thread_url(href: str) -> bool:
        parsed = urlparse(href)
        if parsed.scheme and parsed.scheme != "https":
            return False
        if parsed.netloc and parsed.hostname not in {"instagram.com", "www.instagram.com"}:
            return False
        return "/direct/t/" in parsed.path

    @staticmethod
    def _timestamp(text: str, element: Dict[str, Any]) -> str:
        for key in ("datetime", "timestamp", "time"):
            value = str(element.get(key) or "").strip()
            if value:
                return value
        match = re.search(r"\b(?:\d{1,2}:\d{2}|(?:\d+\s*(?:mo|m|h|d|w|y)))\b", text, re.IGNORECASE)
        return match.group(0) if match else ""

    def _normalize_thread(
        self,
        account: SocialAccount,
        element: Dict[str, Any],
    ) -> Optional[SocialMessage]:
        href = str(element.get("href") or "").strip()
        raw_text = str(element.get("text") or "").strip()
        if not href or not raw_text or not self._valid_thread_url(href):
            return None
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        sender = str(element.get("sender") or (lines[0] if lines else "")).strip()
        body = str(element.get("message_text") or "").strip()
        if not body and len(lines) > 1:
            body_lines = lines[1:]
            if body_lines and re.fullmatch(
                r"(?:\d{1,2}:\d{2}|\d+\s*(?:m|h|d|w|mo|y))",
                body_lines[-1],
                re.IGNORECASE,
            ):
                body_lines = body_lines[:-1]
            body = " ".join(body_lines)
        if not sender or not body:
            return None

        aria = str(element.get("aria_label") or "").lower()
        marker_text = raw_text.lower()
        unread_value = element.get("unread")
        if isinstance(unread_value, bool):
            unread: Optional[bool] = unread_value
        elif any(marker in f"{aria} {marker_text}" for marker in ("unread", "new message", "unseen")):
            unread = True
        elif any(marker in f"{aria} {marker_text}" for marker in ("read message", "seen")):
            unread = False
        else:
            unread = None

        conversation_url = urljoin(INSTAGRAM_INBOX_URL, href)
        conversation_id = urlparse(conversation_url).path.rstrip("/").split("/")[-1]
        fingerprint = hashlib.sha256(f"{conversation_id}\0{body}".encode("utf-8")).hexdigest()[:20]
        return SocialMessage(
            message_id=f"{account.account_id}:{conversation_id}:{fingerprint}",
            account_id=account.account_id,
            conversation_id=conversation_id,
            sender=sender,
            timestamp=self._timestamp(raw_text, element),
            text=body,
            unread=unread,
            metadata={"conversation_url": conversation_url},
        )

    def get_inbox(self, account_id: Optional[str] = None) -> List[SocialMessage]:
        accounts = [
            account for account in self.get_accounts()
            if account_id is None or account.account_id == account_id
        ]
        messages: List[SocialMessage] = []
        for account in accounts:
            try:
                session = self._session(account)
                current_url = session.current_url()
                current_parsed = urlparse(current_url)
                if current_parsed.hostname not in {"instagram.com", "www.instagram.com"} or not current_parsed.path.startswith("/direct/inbox"):
                    session.open(INSTAGRAM_INBOX_URL)
                current_url = session.current_url()
                page_text = session.page_text()
                auth_state = self._authentication_state(session, current_url, page_text)
                self.account_states[account.account_id] = {
                    "authentication": auth_state.value,
                    "inbox": "UNAVAILABLE",
                    "reason": "",
                }
                if auth_state in {
                    AuthenticationState.LOGIN_REQUIRED,
                    AuthenticationState.NOT_AUTHENTICATED,
                    AuthenticationState.SECURITY_CHALLENGE,
                    AuthenticationState.UNKNOWN,
                }:
                    self.account_states[account.account_id]["reason"] = (
                        "Instagram requires login/verification. Please complete it in the browser."
                        if auth_state in {AuthenticationState.LOGIN_REQUIRED, AuthenticationState.SECURITY_CHALLENGE}
                        else "Authentication could not be confirmed in the authorized browser session."
                    )
                    continue

                if any(marker in page_text.lower() for marker in ("no messages", "no conversations", "your messages")) and not session.find_elements(self._THREAD_SELECTOR):
                    self.account_states[account.account_id]["inbox"] = "AVAILABLE"
                    continue

                elements = session.find_elements(self._THREAD_SELECTOR)
                if not elements:
                    self.account_states[account.account_id]["reason"] = (
                        "No reliably recognizable visible Instagram conversation rows were found."
                    )
                    continue
                extracted = [self._normalize_thread(account, item) for item in elements]
                messages.extend(message for message in extracted if message is not None)
                if not any(extracted):
                    self.account_states[account.account_id]["reason"] = (
                        "Visible inbox rows did not contain enough information to normalize safely."
                    )
                    continue
                self.account_states[account.account_id]["inbox"] = "AVAILABLE"
            except Exception as exc:
                self.account_states[account.account_id] = {
                    "authentication": AuthenticationState.UNKNOWN.value,
                    "inbox": "UNAVAILABLE",
                    "reason": f"Browser session could not be read: {exc}",
                }
        return messages

    def get_notifications(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    def get_comments(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    def get_mentions(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    def get_account_summary(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        state = self.account_states.get(account_id or "", {})
        return {
            "account_id": account_id,
            "status": state.get("inbox", "UNAVAILABLE"),
            "authentication": state.get("authentication", AuthenticationState.UNKNOWN.value),
            "reason": state.get("reason", "No authorized browser read has been performed."),
            "notifications_status": "UNAVAILABLE",
            "comments_status": "UNAVAILABLE",
            "mentions_status": "UNAVAILABLE",
            "content_metrics_status": "UNAVAILABLE",
        }

    def get_content_metrics(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "account_id": account_id,
            "status": "UNAVAILABLE",
            "reason": "Content metrics are not reliably available from the visible inbox browser view.",
        }

    def execute_action(
        self,
        action_type: str,
        *,
        message: SocialMessage,
        text: Optional[str] = None,
    ) -> Dict[str, Any]:
        account = next((item for item in self.get_accounts() if item.account_id == message.account_id), None)
        if account is None:
            return {"status": "UNAVAILABLE", "reason": "The configured Instagram account is unavailable."}
        current_messages = self.get_inbox(account.account_id)
        current_message = next(
            (
                item for item in current_messages
                if item.message_id == message.message_id
                and item.conversation_id == message.conversation_id
            ),
            None,
        )
        if current_message is None:
            return {
                "status": "UNAVAILABLE",
                "reason": "The approved target is no longer present in the visible inbox; inspect it again before acting.",
                "verified": False,
            }
        message = current_message
        session = self._session(account)
        url = str((message.metadata or {}).get("conversation_url") or "")
        if action_type not in {"OPEN_CONVERSATION", "MARK_REVIEWED", "REPLY_MESSAGE"}:
            return {"status": "BLOCKED", "reason": f"Unsupported Instagram action: {action_type}."}
        if not self._valid_thread_url(url):
            return {"status": "UNAVAILABLE", "reason": "No validated Instagram conversation URL is available."}
        session.open(url)
        current_url = session.current_url()
        page_text = session.page_text()
        auth_state = self._authentication_state(session, current_url, page_text)
        if auth_state == AuthenticationState.SECURITY_CHALLENGE:
            return {
                "status": "SECURITY_CHALLENGE",
                "reason": "Instagram requires login/verification. Please complete it in the browser.",
                "verified": False,
            }
        if auth_state != AuthenticationState.AUTHENTICATED:
            return {"status": "AUTHENTICATION_REQUIRED", "reason": "Instagram requires login/verification. Please complete it in the browser.", "verified": False}
        if action_type == "OPEN_CONVERSATION":
            verified = urlparse(current_url).path == urlparse(url).path
            return {"status": "SUCCESS" if verified else "VERIFICATION_FAILED", "verified": verified, "action": action_type}
        if action_type == "MARK_REVIEWED":
            session.wait(0.5)
            refreshed = self.get_inbox(account.account_id)
            reviewed_message = next(
                (item for item in refreshed if item.message_id == message.message_id),
                None,
            )
            verified = reviewed_message is not None and reviewed_message.unread is False
            return {
                "status": "SUCCESS" if verified else "VERIFICATION_FAILED",
                "verified": verified,
                "action": action_type,
                "reason": "" if verified else "The inbox did not visibly confirm the message as read; do not repeat automatically.",
            }
        reply = str(text or "").strip()
        if not reply:
            return {"status": "BLOCKED", "reason": "A non-empty user-approved draft is required.", "verified": False}
        if reply in page_text:
            return {"status": "DUPLICATE_PREVENTED", "reason": "The exact reply is already visible in this conversation.", "verified": False}
        try:
            session.type(selector='textarea, [contenteditable="true"]', text=reply)
            session.click(text="Send")
            session.wait(1.0)
            verified = reply in session.page_text()
        except Exception as exc:
            return {
                "status": "VERIFICATION_FAILED",
                "verified": False,
                "reason": f"Browser action failed and was not retried: {exc}",
            }
        return {
            "status": "SUCCESS" if verified else "VERIFICATION_FAILED",
            "verified": verified,
            "action": action_type,
            "reason": "" if verified else "The reply was not confirmed in the visible conversation; do not resend automatically.",
        }
