"""String values shared by the domain and the VARCHAR database schema."""

from enum import StrEnum


class ScheduleType(StrEnum):
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"


class ScheduleStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    FAILED = "failed"
    COMPLETED = "completed"
    ENDED = "ended"
    DELETED = "deleted"


class DisplayNameSource(StrEnum):
    AI = "ai"
    MANUAL = "manual"
    UNSET = "unset"


class NameGenerationJobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    ABANDONED = "abandoned"


class NameGenerationResultCode(StrEnum):
    GENERATED = "generated"
    STALE_SCHEDULE = "stale_schedule"
    MANUAL_NAME = "manual_name"
    INELIGIBLE_SCHEDULE = "ineligible_schedule"
    GENERATION_DISABLED = "generation_disabled"
    PRICE_UNKNOWN = "price_unknown"
    BUDGET_INVALID = "budget_invalid"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    GENERATOR_UNAVAILABLE = "generator_unavailable"
    GENERATOR_ERROR = "generator_error"
    STALE_AFTER_GENERATION = "stale_after_generation"
    STARTUP_ABANDONED = "startup_abandoned"
    SHUTDOWN_UNKNOWN = "shutdown_unknown"


class BudgetPeriodType(StrEnum):
    DAILY = "daily"
    MONTHLY = "monthly"


class RunStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class DeliveryAttemptStatus(StrEnum):
    CLAIMED = "claimed"
    SENDING = "sending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class DeliveryErrorKind(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


class OperationAction(StrEnum):
    CREATED = "created"
    EDITED = "edited"
    DELETED = "deleted"
    PAUSED = "paused"
    RESUMED = "resumed"
    COMPLETED = "completed"
    ENDED = "ended"
    FAILED = "failed"
    NAME_GENERATED = "name_generated"


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"


class DeleteKind(StrEnum):
    CREATOR_DELETED = "creator_deleted"
    ADMIN_DELETED = "admin_deleted"
    OPERATOR_RESOLVED_FAILED = "operator_resolved_failed"


class NotificationType(StrEnum):
    DRAFT_24H = "draft_24h"
    DRAFT_1H = "draft_1h"
    DRAFT_IMMEDIATE = "draft_immediate"
    RUN_FAILED = "run_failed"
    RUN_DELAYED = "run_delayed"
    RUN_SKIPPED = "run_skipped"
    RECOVERY = "recovery"


class NotificationRecipientType(StrEnum):
    CREATOR_DM = "creator_dm"
    OPERATOR_CHANNEL = "operator_channel"
    OPERATOR_DM = "operator_dm"
    LOG = "log"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class NotificationAttemptStatus(StrEnum):
    CLAIMED = "claimed"
    SENDING = "sending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class NotificationErrorKind(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


def enum_values(enum_type: type[StrEnum]) -> tuple[str, ...]:
    """Return declaration-order values for a string enum."""
    return tuple(member.value for member in enum_type)
