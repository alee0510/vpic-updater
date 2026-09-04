"""
Orchestrator: wires Check -> Extract -> Transform -> Load -> Notify ->
Cleanup into a single update cycle.

This module is intentionally the only place stage functions are chained
together -- it is what gets replaced by a Prefect @flow later, with each
stage function becoming a @task largely unchanged.

Safety properties this function is responsible for upholding:
  - Only one update process may run at a time (advisory lock on control-db).
  - "No new version" is a normal, quiet, successful exit -- not an error.
  - Any failure between download and promote() must leave current_deployment
    and all existing databases untouched.
  - A Slack notification is sent on every completed attempt (success or
    failure) once a new version was detected -- never on "nothing new."
  - Temp files are cleaned up regardless of outcome.
"""

import logging

from vpic_updater.core.config import Settings, get_settings
from vpic_updater.core.db import connect
from vpic_updater.core.logging_conf import configure_logging, new_run_id

from vpic_updater.models.extract import ExtractError
from vpic_updater.models.load import LoadError
from vpic_updater.models.transform import TransformError
from vpic_updater.models.version import VersionCheckError

from vpic_updater.stages.check import fetch_current_version, is_new_version
from vpic_updater.stages.cleanup import cleanup_temp_files
from vpic_updater.stages.extract import download_file
from vpic_updater.stages.load import (
    acquire_lock,
    create_database,
    db_name_for,
    get_last_deployed_version,
    grant_app_access,
    promote,
    record_history,
    release_lock,
    restore_dump,
    validate_database,
)
from vpic_updater.stages.notify import send_slack_notification
from vpic_updater.stages.transform import unzip_dump

logger = logging.getLogger("vpic_updater.orchestrator")


def run_update_check(settings: Settings) -> None:
    run_id = new_run_id()
    logger.info("Starting update check (run_id=%s)", run_id)

    control_conn = connect(settings.control_dsn)

    if not acquire_lock(control_conn):
        logger.info("Another update process is already running -- exiting")
        control_conn.close()
        return

    zip_path = None
    extract_target_dir = None

    try:
        try:
            remote = fetch_current_version()
        except VersionCheckError as exc:
            logger.error("Version check failed: %s", exc)
            send_slack_notification(
                settings.slack_webhook_url,
                version="unknown",
                released_on="unknown",
                status="failure",
                detail=f"Could not check vPIC version: {exc}",
            )
            return

        last_version = get_last_deployed_version(control_conn)
        if not is_new_version(remote, last_version):
            send_slack_notification(
                settings.slack_webhook_url,
                version="unknown",
                released_on="unknown",
                status="info",
                detail=f"No new version of vPIC is available for deployment (current={remote.version}, last deployed={last_version}).",
            )
            logger.info(
                "No new version of vPIC is available for deployment (current=%s, last deployed=%s)",
                remote.version, last_version,
            )
            return

        logger.info(
            "New version detected: %s (released %s)", remote.version, remote.released_on
        )
        db_name = db_name_for(remote.year_month)
        extract_target_dir = settings.extract_dir / remote.year_month

        try:
            download_result = download_file(
                remote.postgres_custom_url, settings.download_dir
            )
            zip_path = download_result.file_path

            extracted = unzip_dump(zip_path, extract_target_dir)

            create_database(settings.target_admin_dsn, db_name)
            restore_dump(extracted.dump_path, settings.target_admin_dsn, db_name)
            validation = validate_database(settings.target_admin_dsn, db_name)
            grant_app_access(settings.target_admin_dsn, db_name, app_role=settings.app_role)
            promote(control_conn, db_name, remote.version, remote.released_on)

            record_history(
                control_conn, remote.version, remote.released_on,
                status="success", db_name=db_name,
            )
            send_slack_notification(
                settings.slack_webhook_url,
                version=remote.version,
                released_on=remote.released_on,
                status="success",
                detail=(
                    f"Deployed to {db_name}. "
                    f"Row counts: {validation.table_row_counts}. "
                    f"Decode smoke test passed: {validation.smoke_test_passed}."
                ),
            )
            logger.info("Update cycle completed successfully: %s", remote.version)

        except (ExtractError, TransformError, LoadError) as exc:
            logger.exception("Update failed at stage: %s", exc)
            record_history(
                control_conn, remote.version, remote.released_on,
                status="failure", db_name=db_name, error=str(exc),
            )
            send_slack_notification(
                settings.slack_webhook_url,
                version=remote.version,
                released_on=remote.released_on,
                status="failure",
                detail=f"Update failed, production database unchanged. Error: {exc}",
            )

            # current_deployment is untouched here because promote() was
            # never reached -- that's the actual safety guarantee.
            #
            # DEFAULT BEHAVIOR: a partially-created db_name (if create_database
            # got that far) is deliberately LEFT IN PLACE, not dropped. This
            # gives you a forensic artifact to inspect after a failed run
            # (partial restore state, whatever got as far as landing before
            # the failure) instead of silently erasing evidence of what went
            # wrong. It also means a re-run against the same version will
            # correctly refuse via create_database()'s "already exists" guard
            # rather than clobbering something that might still be useful.
            #
            # If this ever becomes undesirable (e.g. disk pressure from
            # repeated failed attempts, or you'd rather auto-retry cleanly),
            # uncomment the block below to auto-drop on failure instead:
            #
            # try:
            #     drop_database_if_exists(settings.target_admin_dsn, db_name)
            #     logger.info("Auto-dropped failed partial database: %s", db_name)
            # except Exception as drop_exc:
            #     logger.warning(
            #         "Could not auto-drop failed database %s: %s", db_name, drop_exc
            #     )
            #
            # Note: if you enable this, do it in its own try/except (as
            # shown) so a drop failure never masks or replaces the original
            # LoadError/ExtractError/TransformError being handled here.

    finally:
        if zip_path is not None:
            cleanup_temp_files(zip_path)
        if extract_target_dir is not None:
            cleanup_temp_files(extract_target_dir)
        release_lock(control_conn)
        control_conn.close()


def main() -> None:
    settings = get_settings()
    configure_logging(log_dir=settings.log_dir)
    run_update_check(settings)


if __name__ == "__main__":
    main()