import logging

import pytest

from discord_ai_reminder_bot.domain.post_draft_generation import (
    GeneratedPostDraft,
    PostDraftGenerationRequest,
    PostDraftOutputLimit,
    PostLength,
    PostTone,
    output_limit_for,
)

PURPOSE_CANARY = "purpose-private-canary"
KEY_POINTS_CANARY = "key-points-private-canary"
DRAFT_CANARY = "draft-private-canary"


def request(**overrides: object) -> PostDraftGenerationRequest:
    values: dict[str, object] = {
        "purpose": "新サービスの受付開始を案内する",
        "key_points": "受付開始日は9月10日\n詳細は公式ページを確認",
        "tone": PostTone.POLITE,
        "length": PostLength.STANDARD,
    }
    values.update(overrides)
    return PostDraftGenerationRequest(**values)  # type: ignore[arg-type]


def test_request_accepts_normal_japanese_input_and_fixed_defaults() -> None:
    value = request()
    assert value.purpose == "新サービスの受付開始を案内する"
    assert value.key_points == "受付開始日は9月10日\n詳細は公式ページを確認"
    assert value.tone is PostTone.POLITE
    assert value.length is PostLength.STANDARD
    assert value.locale == "ja-JP"
    assert value.output_limit == PostDraftOutputLimit(max_characters=1_000, max_output_tokens=1_024)


@pytest.mark.parametrize(
    ("field", "valid", "too_long"),
    [
        ("purpose", "目" * 200, "目" * 201),
        ("key_points", "点" * 1_000, "点" * 1_001),
    ],
)
def test_request_accepts_text_boundaries_and_rejects_boundary_overflow(
    field: str, valid: str, too_long: str
) -> None:
    assert getattr(request(**{field: valid}), field) == valid
    with pytest.raises(ValueError):
        request(**{field: too_long})


@pytest.mark.parametrize("field", ["purpose", "key_points"])
@pytest.mark.parametrize("value", ["", " ", "\n\t"])
def test_request_rejects_empty_and_whitespace_only(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        request(**{field: value})


def test_request_accepts_emoji_newlines_markdown_and_url() -> None:
    value = request(
        purpose="新企画を案内する 🎉",
        key_points="**受付開始**\n- 詳細: https://example.invalid/info 🎈",
    )
    assert "🎉" in value.purpose
    assert "**受付開始**" in value.key_points
    assert "https://example.invalid/info" in value.key_points


@pytest.mark.parametrize(
    "value",
    ["@everyoneへ案内", "@EVERYONEへ案内", "@hereへ案内", "@HeReへ案内"],
)
@pytest.mark.parametrize("field", ["purpose", "key_points"])
def test_request_rejects_broadcast_mentions_case_insensitively(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        request(**{field: value})


@pytest.mark.parametrize(
    "unsafe",
    [
        "nul\x00text",
        "escape\x1btext",
        "zero-width\u200btext",
        "isolate\u2066text",
        "override\u202etext",
        "surrogate\ud800text",
    ],
)
@pytest.mark.parametrize("field", ["purpose", "key_points"])
def test_request_rejects_control_format_and_bidi_characters(field: str, unsafe: str) -> None:
    with pytest.raises(ValueError):
        request(**{field: unsafe})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tone", "formal"),
        ("tone", "polite"),
        ("length", "medium"),
        ("length", "standard"),
    ],
)
def test_request_rejects_non_enum_values(field: str, value: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        request(**{field: value})


@pytest.mark.parametrize("field", ["max_characters", "max_output_tokens"])
def test_output_limit_rejects_bool_as_integer(field: str) -> None:
    values = {"max_characters": 500, "max_output_tokens": 512}
    values[field] = True
    with pytest.raises(ValueError):
        PostDraftOutputLimit(**values)


@pytest.mark.parametrize(
    ("length", "characters", "tokens"),
    [
        (PostLength.SHORT, 500, 512),
        (PostLength.STANDARD, 1_000, 1_024),
        (PostLength.LONG, 2_000, 2_048),
    ],
)
def test_length_maps_to_fixed_character_and_token_limits(
    length: PostLength, characters: int, tokens: int
) -> None:
    assert output_limit_for(length) == PostDraftOutputLimit(
        max_characters=characters,
        max_output_tokens=tokens,
    )
    assert request(length=length).output_limit == output_limit_for(length)


def test_request_rejects_output_limit_that_does_not_match_length() -> None:
    with pytest.raises(ValueError):
        request(
            length=PostLength.SHORT,
            output_limit=PostDraftOutputLimit(
                max_characters=2_000,
                max_output_tokens=2_048,
            ),
        )


def test_generated_draft_accepts_japanese_emoji_newlines_markdown_url_and_boundary() -> None:
    rich = "**お知らせ** 🎉\n詳細: https://example.invalid/info"
    assert GeneratedPostDraft(rich).value == rich
    assert len(GeneratedPostDraft("本" * 2_000).value) == 2_000


@pytest.mark.parametrize("value", ["", " ", "\n\t", "本" * 2_001])
def test_generated_draft_rejects_empty_whitespace_and_boundary_overflow(value: str) -> None:
    with pytest.raises(ValueError):
        GeneratedPostDraft(value)


@pytest.mark.parametrize(
    "value",
    [
        "@everyoneへ通知",
        "@EVERYONEへ通知",
        "@hereへ通知",
        "@HeReへ通知",
        "control\x00text",
        "format\u200btext",
        "bidi\u202etext",
    ],
)
def test_generated_draft_rejects_mentions_control_format_and_bidi(value: str) -> None:
    with pytest.raises(ValueError):
        GeneratedPostDraft(value)


def test_sensitive_values_are_absent_from_repr_exceptions_and_logs(caplog) -> None:
    value = PostDraftGenerationRequest(
        purpose=PURPOSE_CANARY,
        key_points=KEY_POINTS_CANARY,
        tone=PostTone.CONCISE,
        length=PostLength.SHORT,
    )
    generated = GeneratedPostDraft(DRAFT_CANARY)
    with pytest.raises(ValueError) as captured:
        PostDraftGenerationRequest(
            purpose=PURPOSE_CANARY + "\x00",
            key_points=KEY_POINTS_CANARY,
            tone=PostTone.CONCISE,
            length=PostLength.SHORT,
        )
    with pytest.raises(ValueError) as generated_error:
        GeneratedPostDraft(DRAFT_CANARY + "\u202e")

    logging.getLogger("post-draft-domain-test").info("fixed_event")
    observed = " ".join(
        (repr(value), repr(generated), str(captured.value), str(generated_error.value), caplog.text)
    )
    assert PURPOSE_CANARY not in observed
    assert KEY_POINTS_CANARY not in observed
    assert DRAFT_CANARY not in observed
