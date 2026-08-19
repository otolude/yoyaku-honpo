"""Validation for short persisted error metadata."""

_SENSITIVE_MARKERS = (
    "http://",
    "https://",
    "www.",
    "postgresql://",
    "postgresql+psycopg://",
    "discord.com/api",
    "token",
    "password",
    "database_url",
    "authorization:",
    "bearer ",
    "traceback (",
)


def validate_safe_error_text(value: str, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    lowered = normalized.casefold()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        raise ValueError(f"{field} contains sensitive or unsafe text")
    return normalized
