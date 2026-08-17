"""Domain errors returned for invalid scheduling operations."""


class DomainError(ValueError):
    """Base class for invalid domain input or actions."""


class InvalidStateTransitionError(DomainError):
    """A schedule or run transition is not allowed by Phase 1 rules."""


class InvalidDateTimeError(DomainError):
    """A datetime is naive or otherwise invalid for domain use."""
