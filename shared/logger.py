"""
Structured logger for the signal master.
Writes to both console (for live monitoring) and rotating file (for audit).
"""

import logging
import logging.handlers
import sys
from pathlib import Path


def setup_logger(
    name: str = "xauusd_master",
    log_dir: str = "logs",
    level: int = logging.INFO,
) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # Already configured

    logger.setLevel(level)
    fmt = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Rotating file handler
    try:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_path / f"{name}.log",
            maxBytes=10_000_000,   # 10 MB per file
            backupCount=10,        # Keep 10 backups
            encoding='utf-8'
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception as e:
        logger.warning(f"Could not set up file logger: {e}")

    return logger


def get_logger(name: str = "xauusd_master") -> logging.Logger:
    """Get a configured logger by name. Returns cached instance."""
    return logging.getLogger(name)
