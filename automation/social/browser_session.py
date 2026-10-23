from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Protocol


class BrowserSession(Protocol):
    """Minimal interface over an existing, user-authorized browser session."""

    def open(self, url: str) -> Any: ...

    def current_url(self) -> str: ...

    def page_text(self) -> str: ...

    def find_elements(self, selector: str) -> List[Dict[str, Any]]: ...

    def is_authenticated(self) -> bool: ...

    def click(self, *, selector: Optional[str] = None, text: Optional[str] = None) -> Any: ...

    def type(self, *, selector: str, text: str) -> Any: ...

    def wait(self, seconds: float) -> None: ...

    def screenshot(self) -> Any: ...


class BrowserControlSession:
    """Adapter over the existing ``actions.browser_control`` action."""

    def __init__(self, browser_name: Optional[str] = None, browser_action: Any = None):
        if browser_action is None:
            from actions.browser_control import browser_control

            browser_action = browser_control
        self.browser_name = browser_name
        self._browser_action = browser_action

    def _call(self, action: str, **parameters: Any) -> str:
        request: Dict[str, Any] = {"action": action, **parameters}
        if self.browser_name:
            request["browser"] = self.browser_name
        result = self._browser_action(request)
        if not isinstance(result, str):
            raise RuntimeError(f"Browser action {action!r} returned a non-text result.")
        if result.startswith(("Browser error", "Could not start browser session", "Could not get")):
            raise RuntimeError(result)
        return result

    def open(self, url: str) -> str:
        result = self._call("go_to", url=url)
        if not result.startswith("Opened:"):
            raise RuntimeError(result)
        return result

    def current_url(self) -> str:
        return self._call("get_url")

    def page_text(self) -> str:
        return self._call("get_text")

    def is_authenticated(self) -> bool:
        from urllib.parse import urlparse

        url = self.current_url()
        text = self.page_text().lower()
        hostname = (urlparse(url).hostname or "").lower()
        if hostname not in {"instagram.com", "www.instagram.com"}:
            return False
        if any(marker in url.lower() for marker in ("/accounts/login", "/challenge/", "/checkpoint/")):
            return False
        return any(marker in text for marker in ("messages", "primary", "general"))

    def find_elements(self, selector: str) -> List[Dict[str, Any]]:
        result = self._call("get_elements", selector=selector, limit=100)
        try:
            elements = json.loads(result)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Browser returned malformed visible-element data.") from exc
        if not isinstance(elements, list) or any(not isinstance(item, dict) for item in elements):
            raise RuntimeError("Browser returned an invalid visible-element collection.")
        return elements

    def click(self, *, selector: Optional[str] = None, text: Optional[str] = None) -> str:
        result = self._call("click", selector=selector, text=text)
        if result.startswith(("Element not found", "Click error", "No selector")):
            raise RuntimeError(result)
        return result

    def type(self, *, selector: str, text: str) -> str:
        result = self._call("type", selector=selector, text=text, clear_first=True)
        if result.startswith("Type error"):
            raise RuntimeError(result)
        return result

    def wait(self, seconds: float) -> None:
        time.sleep(max(0.0, float(seconds)))

    def screenshot(self) -> str:
        descriptor, path = tempfile.mkstemp(prefix="jarvis-social-", suffix=".png")
        os.close(descriptor)
        try:
            return self._call("screenshot", path=path)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
