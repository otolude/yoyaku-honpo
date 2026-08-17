"""Persistence errors that are separate from scheduling domain errors."""


class RepositoryError(Exception):
    """Base class for persistence-layer failures."""


class RepositoryNotFoundError(RepositoryError):
    """The requested row does not exist within the requested boundary."""


class OptimisticLockError(RepositoryError):
    """The row exists, but its version changed before the update."""


class DuplicateRecordError(RepositoryError):
    """A database uniqueness rule rejected a duplicate row."""


class RepositoryOwnershipError(RepositoryError):
    """The row belongs to a different worker."""


class RepositoryStateConflictError(RepositoryError):
    """The row exists but is no longer in the expected state."""


class UnsafeTestDatabaseError(RepositoryError):
    """A test database URL could point to development or production data."""
