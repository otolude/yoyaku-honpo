"""Safe standard-library logging configuration."""

from __future__ import annotations

import logging
import time


class UtcEventFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        for field in (
            "worker_id",
            "claimed",
            "succeeded",
            "retry_scheduled",
            "failed",
            "unknown",
            "skipped",
            "internal_errors",
            "recovered",
            "command_count",
        ):
            if not hasattr(record, field):
                setattr(record, field, "-")
        return super().format(record)

    def formatException(self, exc_info) -> str:
        return "exception details suppressed"


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        UtcEventFormatter(
            fmt=(
                "%(asctime)sZ %(levelname)s %(name)s %(message)s "
                "worker_id=%(worker_id)s claimed=%(claimed)s succeeded=%(succeeded)s "
                "retry_scheduled=%(retry_scheduled)s failed=%(failed)s unknown=%(unknown)s "
                "skipped=%(skipped)s internal_errors=%(internal_errors)s recovered=%(recovered)s "
                "command_count=%(command_count)s"
            ),
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
