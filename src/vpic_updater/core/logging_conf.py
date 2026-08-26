"""Structured logging setup, shared by the orchestrator entry point.

Logs to both stdout (for `docker compose logs` / live viewing) and a
rotating file under /app/logs (mounted to ./logs on the host). Every log
line is tagged with the current run's ID via a contextvar-backed filter,
so a single execution can be isolated from the rest of the file with a
simple grep, without threading run_id through every stage function's
signature.
"""

import logging
import sys
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_DIR = Path("logs")
DEFAULT_LOG_FILE = "vpic_updater.log"
MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MB per file
BACKUP_COUNT = 5                   # keep 5 rotated files (~50MB total)

_run_id_var: ContextVar[str] = ContextVar("run_id", default="-")


class RunIdFilter(logging.Filter):
    """Injects the current run's ID (from contextvar) into every log
    record as %(run_id)s. Falls back to '-' outside of a run context
    (e.g. import-time logging, ad-hoc scripts)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id_var.get()
        return True


def new_run_id() -> str:
    """Generate and activate a new run ID for the current execution.
    Call once at the start of run_update_check(); every log line emitted
    afterward (from any module) will carry this ID until the process exits
    or a new run_id is set."""
    run_id = uuid.uuid4().hex[:8]
    _run_id_var.set(run_id)
    return run_id


def configure_logging(
    level: int = logging.INFO,
    log_dir: Path = DEFAULT_LOG_DIR,
    log_to_file: bool = True,
) -> None:
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s [run=%(run_id)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    run_id_filter = RunIdFilter()

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
        handler.addFilter(run_id_filter)

    logging.basicConfig(level=level, handlers=handlers)