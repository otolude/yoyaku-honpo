"""Persistence errors that are separate from scheduling domain errors."""


class RepositoryError(Exception):
    """Base class for persistence-layer failures."""


class RepositoryNotFoundError(RepositoryError):
    """The requested row does not exist within the requested boundary."""


class OptimisticLockError(RepositoryError):
    """The row exists, but its version changed before the update."""


class DuplicateRecordError(RepositoryError):
    """A database uniqueness rule rejected a duplicate row."""


class UnsafeTestDatabaseError(RepositoryError):
    """A test database URL could point to development or production data."""
