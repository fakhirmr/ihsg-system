"""
IHSG Trading System — Logging Setup
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from config import LOGS_DIR


def setup_logger(name: str = "ihsg_system") -> logging.Logger:
    """Configure and return a logger that writes to console + rotating file."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler (INFO and above)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)

    # File handler (DEBUG and above)
    log_file = LOGS_DIR / "ihsg_system.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    logger.addHandler(ch)
    logger.addHandler(fh)

    return logger


# Module-level default logger
log = setup_logger()
