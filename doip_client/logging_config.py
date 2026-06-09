"""Logging setup helpers for the EPISERVE DOIP client."""

from __future__ import annotations

import logging
import os
import sys

# Shared application logger used across modules.
log = logging.getLogger("doip_client")


def configure_logging(level: str | int | None = None) -> logging.Logger:
    """Configure console logging with a sensible default format and level.

    Args:
        level: Optional log level (e.g. ``\"INFO\"`` or ``logging.DEBUG``). If
            omitted, the ``LOG_LEVEL`` environment variable is used and falls
            back to ``INFO`` when unset or invalid.

    Returns:
        logging.Logger: The configured application logger instance.
    """
    resolved_level = _coerce_level(level)

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
        )
        root.addHandler(handler)

    root.setLevel(resolved_level)

    # Keep noisy third-party libraries at bay; adjust as needed.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)

    log.setLevel(resolved_level)
    log.propagate = True
    log.debug("Logging configured at level %s", logging.getLevelName(resolved_level))
    return log


def _coerce_level(level: str | int | None) -> int:
    """Return a numeric logging level from user input or environment.

    Args:
        level: Explicit level value. When ``None``, ``LOG_LEVEL`` from the
            environment is used instead.

    Returns:
        int: Numeric logging level understood by the standard ``logging`` module.
    """
    candidate = level if level is not None else os.getenv("LOG_LEVEL", "INFO")

    if isinstance(candidate, int):
        return candidate

    if isinstance(candidate, str):
        numeric = logging.getLevelName(candidate.upper())
        if isinstance(numeric, int):
            return numeric

    return logging.INFO
