"""Pure validation and audit classification for schedule deletion."""

from __future__ import annotations

from discord_ai_reminder_bot.domain.enums import DeleteKind, ScheduleStatus

DELETABLE_STATUSES = frozenset(
    {
        ScheduleStatus.DRAFT,
        ScheduleStatus.ACTIVE,
        ScheduleStatus.PAUSED,
        ScheduleStatus.FAILED,
    }
)
MISSING_DELETE_REASON = "理由未入力"


class InvalidDeleteReasonError(ValueError):
    """A user-supplied deletion reason is empty or exceeds the DB limit."""


def validate_delete_reason(reason: str | None) -> str:
    """Trim and validate a reason before it reaches persistence or presentation."""
    if reason is None:
        return MISSING_DELETE_REASON
    if not isinstance(reason, str):
        raise InvalidDeleteReasonError("invalid delete reason")
    normalized = reason.strip()
    if not normalized:
        return MISSING_DELETE_REASON
    if len(normalized) > 500:
        raise InvalidDeleteReasonError("invalid delete reason")
    return normalized


def deletion_kind(
    *,
    actor_user_id: int,
    creator_user_id: int,
    administrator: bool,
    status: ScheduleStatus,
) -> DeleteKind:
    """Classify deletion with creator ownership taking precedence over admin power."""
    if actor_user_id == creator_user_id:
        return DeleteKind.CREATOR_DELETED
    if not administrator:
        raise PermissionError("actor cannot delete another user's schedule")
    if status is ScheduleStatus.FAILED:
        return DeleteKind.OPERATOR_RESOLVED_FAILED
    return DeleteKind.ADMIN_DELETED
