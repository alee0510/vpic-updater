"""Structured logging setup, shared by the orchestrator entry point.

Logs to both stdout (for `docker compose logs` / live viewing) and a
rotating file under /app/logs (mounted to ./logs on the host), so
scheduled runs leave a durable record without needing the container to
still be alive to inspect them.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_DIR = Path("logs")
DEFAULT_LOG_FILE = "vpic_updater.log"
MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MB per file
BACKUP_COUNT = 5                  # keep 5 rotated files (~50MB total)


def configure_logging(
    level: int = logging.INFO,
    log_dir: Path = DEFAULT_LOG_DIR,
    log_to_file: bool = True,
) -> None:
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    handlers: list[logging.Handler] = [logging.StreamHandler(stream=sys.stdout)]

    if log_to_file:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / DEFAULT_LOG_FILE,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
        )
        handlers.append(file_handler)

    for handler in handlers:
        handler.setFormatter(formatter)

    logging.basicConfig(level=level, handlers=handlers)