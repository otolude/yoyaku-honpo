"""Provider-neutral input and output validation for AI-assisted post drafts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

MAX_PURPOSE_CHARACTERS = 200
MAX_KEY_POINTS_CHARACTERS = 1_000
MAX_GENERATED_POST_CHARACTERS = 2_000
SUPPORTED_LOCALE = "ja-JP"

_BROADCAST_MENTION = re.compile(r"@(everyone|here)", re.IGNORECASE)
_BIDI_CONTROL_CLASSES = frozenset(
    {
        "LRE",
        "RLE",
        "LRO",
        "RLO",
        "PDF",
        "LRI",
        "RLI",
        "FSI",
        "PDI",
    }
)


class PostTone(StrEnum):
    """Closed tone choices presented by the Discord UI."""

    POLITE = "polite"
    FRIENDLY = "friendly"
    CONCISE = "concise"


class PostLength(StrEnum):
    """Closed length choices mapped to fixed provider limits."""

    SHORT = "short"
    STANDARD = "standard"
    LONG = "long"


def _bounded_integer(value: object, *, field_name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")  # noqa: TRY004
    if not 1 <= value <= maximum:
        raise ValueError(f"{field_name} is outside the supported range")
    return value


@dataclass(frozen=True, slots=True)
class PostDraftOutputLimit:
    """Fixed character and token ceilings for one selected length."""

    max_characters: int
    max_output_tokens: int

    def __post_init__(self) -> None:
        _bounded_integer(
            self.max_characters,
            field_name="max_characters",
            maximum=MAX_GENERATED_POST_CHARACTERS,
        )
        _bounded_integer(
            self.max_output_tokens,
            field_name="max_output_tokens",
            maximum=2_048,
        )


_OUTPUT_LIMITS = {
    PostLength.SHORT: PostDraftOutputLimit(max_characters=500, max_output_tokens=512),
    PostLength.STANDARD: PostDraftOutputLimit(
        max_characters=1_000,
        max_output_tokens=1_024,
    ),
    PostLength.LONG: PostDraftOutputLimit(
        max_characters=2_000,
        max_output_tokens=2_048,
    ),
}


def output_limit_for(length: PostLength) -> PostDraftOutputLimit:
    """Return the immutable output limit for a validated length choice."""
    if not isinstance(length, PostLength):
        raise TypeError("length must be a PostLength")
    return _OUTPUT_LIMITS[length]


def _validate_untrusted_text(value: object, *, field_name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > maximum:
        raise ValueError(f"{field_name} exceeds the character limit")
    if _BROADCAST_MENTION.search(value) is not None:
        raise ValueError(f"{field_name} contains a prohibited mention")
    for character in value:
        category = unicodedata.category(character)
        if (category == "Cc" and character != "\n") or category in {"Cf", "Cs"}:
            raise ValueError(f"{field_name} contains a prohibited character")
        if unicodedata.bidirectional(character) in _BIDI_CONTROL_CLASSES:
            raise ValueError(f"{field_name} contains a prohibited character")
    return value


@dataclass(frozen=True, slots=True)
class PostDraftGenerationRequest:
    """Validated generation conditions with sensitive text excluded from repr."""

    purpose: str = field(repr=False)
    key_points: str = field(repr=False)
    tone: PostTone
    length: PostLength
    locale: str = SUPPORTED_LOCALE
    output_limit: PostDraftOutputLimit | None = None

    def __post_init__(self) -> None:
        purpose = _validate_untrusted_text(
            self.purpose,
            field_name="purpose",
            maximum=MAX_PURPOSE_CHARACTERS,
        )
        key_points = _validate_untrusted_text(
            self.key_points,
            field_name="key_points",
            maximum=MAX_KEY_POINTS_CHARACTERS,
        )
        if not isinstance(self.tone, PostTone):
            raise TypeError("tone must be a PostTone")
        if not isinstance(self.length, PostLength):
            raise TypeError("length must be a PostLength")
        if self.locale != SUPPORTED_LOCALE:
            raise ValueError("unsupported locale")
        expected_limit = output_limit_for(self.length)
        if self.output_limit is not None and self.output_limit != expected_limit:
            raise ValueError("output_limit does not match length")
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "key_points", key_points)
        object.__setattr__(self, "output_limit", expected_limit)


@dataclass(frozen=True, slots=True)
class GeneratedPostDraft:
    """Untrusted provider text validated before it can cross the Domain boundary."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        validated = _validate_untrusted_text(
            self.value,
            field_name="generated_post_draft",
            maximum=MAX_GENERATED_POST_CHARACTERS,
        )
        object.__setattr__(self, "value", validated)
