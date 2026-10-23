import re
from typing import Any


_SECRET_FIELD = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|"
    r"secret|cookie|authorization|credential)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|"
    r"secret|credential)\b\s*[:=]\s*)[^\r\n,;]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SENSITIVE_HEADER = re.compile(
    r"(?im)(\b(?:authorization|cookie|set-cookie)\b\s*[:=]\s*)[^\r\n]+"
)


def redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    text = _SENSITIVE_HEADER.sub(r"\1[REDACTED]", text)
    text = _BEARER_TOKEN.sub("[REDACTED]", text)
    return _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", text)


def contains_sensitive_text(value: Any) -> bool:
    text = str(value or "")
    return redact_sensitive_text(text) != text


def is_sensitive_field(value: Any) -> bool:
    return bool(_SECRET_FIELD.search(str(value or "")))


def redact_sensitive_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_FIELD.search(str(key)) else redact_sensitive_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_sensitive_text(value)
