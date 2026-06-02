"""
Centralized logging for the agent service.

Writes to both console (stdout) and agent/logs/agent.log.
The log file is reset on each call to reset_log_file().
"""

import logging
import os
import sys
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "agent.log"

# Single shared logger for the agent package
logger = logging.getLogger("agent")
logger.setLevel(logging.DEBUG)

# Formatter matching the existing [TAG] prefix style
_formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

# Console handler — always present
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.INFO)
_console.setFormatter(_formatter)
logger.addHandler(_console)

# File handler — added on first reset / startup
_file_handler: logging.FileHandler | None = None


def reset_log_file() -> Path:
    """Clear (or create) the log file and attach a fresh file handler.

    Call this once at FastAPI startup so each dev-server run starts with
    a clean log file.  Returns the path for convenience.
    """
    global _file_handler

    LOG_DIR.mkdir(parents=True, exist_ok=True)

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
