"""
Centralized logging for the agent service.

Logs to stdout, which the host captures (e.g. Railway runtime logs) — so nothing
accumulates on the container's ephemeral disk. A file handler is attached ONLY
when the LOG_FILE env var is set, which is handy for local dev and off in prod.

Env vars:
  LOG_LEVEL  console level (default INFO)
  LOG_FILE   path to a dev log file; unset = stdout only (recommended in prod)
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# Optional local-dev log file. Unset in prod so logs go to stdout only.
LOG_FILE: Optional[Path] = Path(os.environ["LOG_FILE"]) if os.getenv("LOG_FILE") else None

# Single shared logger for the agent package
logger = logging.getLogger("agent")
logger.setLevel(logging.DEBUG)

# Formatter matching the existing [TAG] prefix style
_formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

# Console handler — always present (stdout → host log capture)
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_console.setFormatter(_formatter)
logger.addHandler(_console)

# File handler — only attached when LOG_FILE is configured (local dev)
_file_handler: Optional[logging.FileHandler] = None


def reset_log_file() -> Optional[Path]:
    """Attach a fresh file handler when LOG_FILE is set (local dev only).

    Returns the log path, or None when LOG_FILE is unset (e.g. on Railway) so
    logs stream to stdout only and nothing grows on the ephemeral container disk.
    """
    global _file_handler

    if LOG_FILE is None:
        return None

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Remove previous file handler if any
    if _file_handler is not None:
        logger.removeHandler(_file_handler)
        _file_handler.close()

    # Truncate / create the file
    LOG_FILE.write_text("")

    # Attach new handler (append mode — we just truncated)
    _file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(_formatter)
    logger.addHandler(_file_handler)

    return LOG_FILE
