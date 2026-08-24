"""
Stage 5: Cleanup.

Removes downloaded zips and extracted dump files after each run, success
or failure, so disk usage doesn't grow unbounded across the monthly
schedule. Called from the orchestrator's `finally` block -- must never
raise, since a cleanup failure should be logged, not allowed to mask or
override the real outcome of the run.
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger("vpic_updater.cleanup")


def cleanup_temp_files(*paths: Path) -> None:
    """Remove each given file or directory if it exists.

    Deliberately swallows and logs individual failures rather than
    raising, so one un-removable path doesn't prevent cleanup of the
    others, and never turns a successful (or already-failed) run into a
    cleanup-triggered failure.
    """
    for path in paths:
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                logger.info("Removed directory: %s", path)
            elif path.exists():
                path.unlink()
                logger.info("Removed file: %s", path)
            else:
                logger.debug("Nothing to clean up at: %s", path)
        except OSError as exc:
            logger.warning("Could not clean up %s: %s", path, exc)