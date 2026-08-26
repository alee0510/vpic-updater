"""Unit tests for the orchestrator. Mocks every stage function -- this
file tests wiring and control flow (lock handling, error routing,
cleanup-always-runs), not the stages themselves, which have their own
dedicated test files."""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vpic_updater.core.config import Settings
from vpic_updater.core.orchestrator import run_update_check

from vpic_updater.models.download import DownloadResult
from vpic_updater.models.extract import ExtractedDump, ExtractError
from vpic_updater.models.load import LoadError
from vpic_updater.models.validation import ValidationResult
from vpic_updater.models.version import VpicVersion, VersionCheckError


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        control_db_password="test",
        target_db_password="test",
        slack_webhook_url="https://hooks.slack.com/services/test",
        download_dir=tmp_path / "downloads",
        extract_dir=tmp_path / "extracted",
    )


def _make_version(version="4.08") -> VpicVersion:
    return VpicVersion(
        version=version,
        released_on=date(2026, 8, 15),
        postgres_custom_url="https://vpic.nhtsa.dot.gov/downloads/vPICList_lite_2026_08.custom.zip",
        postgres_plain_url="https://vpic.nhtsa.dot.gov/downloads/vPICList_lite_2026_08.plain.zip",
    )


PATCH_TARGET = "vpic_updater.core.orchestrator"


class TestLockHandling:
    @patch(f"{PATCH_TARGET}.connect")
    @patch(f"{PATCH_TARGET}.acquire_lock")
    @patch(f"{PATCH_TARGET}.fetch_current_version")
    def test_exits_immediately_if_lock_not_acquired(
        self, mock_fetch, mock_acquire, mock_connect, settings
    ):
        mock_connect.return_value = MagicMock()
        mock_acquire.return_value = False

        run_update_check(settings)

        mock_fetch.assert_not_called()
        mock_connect.return_value.close.assert_called_once()

    @patch(f"{PATCH_TARGET}.release_lock")
    @patch(f"{PATCH_TARGET}.connect")
    @patch(f"{PATCH_TARGET}.acquire_lock")
    @patch(f"{PATCH_TARGET}.get_last_deployed_version")
    @patch(f"{PATCH_TARGET}.fetch_current_version")
    def test_lock_released_even_when_no_new_version(
        self, mock_fetch, mock_last_version, mock_acquire,
        mock_connect, mock_release, settings,
    ):
        mock_connect.return_value = MagicMock()
        mock_acquire.return_value = True
        version = _make_version()
        mock_fetch.return_value = version
        mock_last_version.return_value = version.version  # same -> not new

        run_update_check(settings)

        mock_release.assert_called_once()


class TestNoNewVersion:
    @patch(f"{PATCH_TARGET}.send_slack_notification")
    @patch(f"{PATCH_TARGET}.release_lock")
    @patch(f"{PATCH_TARGET}.connect")
    @patch(f"{PATCH_TARGET}.acquire_lock")
    @patch(f"{PATCH_TARGET}.get_last_deployed_version")
    @patch(f"{PATCH_TARGET}.fetch_current_version")
    @patch(f"{PATCH_TARGET}.download_file")
    def test_no_new_version_is_a_quiet_noop(
        self, mock_download, mock_fetch, mock_last_version, mock_acquire,
        mock_connect, mock_release, mock_notify, settings,
    ):
        mock_connect.return_value = MagicMock()
        mock_acquire.return_value = True
        version = _make_version()
        mock_fetch.return_value = version
        mock_last_version.return_value = version.version

        run_update_check(settings)

        mock_download.assert_not_called()
        mock_notify.assert_not_called()  # no-op is silent, not notified


class TestVersionCheckFailure:
    @patch(f"{PATCH_TARGET}.send_slack_notification")
    @patch(f"{PATCH_TARGET}.release_lock")
    @patch(f"{PATCH_TARGET}.connect")
    @patch(f"{PATCH_TARGET}.acquire_lock")
    @patch(f"{PATCH_TARGET}.fetch_current_version")
    def test_check_failure_notifies_and_exits(
        self, mock_fetch, mock_acquire, mock_connect, mock_release, mock_notify, settings
    ):
        mock_connect.return_value = MagicMock()
        mock_acquire.return_value = True
        mock_fetch.side_effect = VersionCheckError("page structure changed")

        run_update_check(settings)

        mock_notify.assert_called_once()
        call_kwargs = mock_notify.call_args.kwargs
        assert call_kwargs["status"] == "failure"
        mock_release.assert_called_once()


class TestSuccessfulRun:
    @patch(f"{PATCH_TARGET}.send_slack_notification")
    @patch(f"{PATCH_TARGET}.record_history")
    @patch(f"{PATCH_TARGET}.promote")
    @patch(f"{PATCH_TARGET}.grant_app_access")
    @patch(f"{PATCH_TARGET}.validate_database")
    @patch(f"{PATCH_TARGET}.restore_dump")
    @patch(f"{PATCH_TARGET}.create_database")
    @patch(f"{PATCH_TARGET}.unzip_dump")
    @patch(f"{PATCH_TARGET}.download_file")
    @patch(f"{PATCH_TARGET}.release_lock")
    @patch(f"{PATCH_TARGET}.connect")
    @patch(f"{PATCH_TARGET}.acquire_lock")
    @patch(f"{PATCH_TARGET}.get_last_deployed_version")
    @patch(f"{PATCH_TARGET}.fetch_current_version")
    @patch(f"{PATCH_TARGET}.cleanup_temp_files")
    def test_full_success_path_promotes_and_notifies(
        self, mock_cleanup, mock_fetch, mock_last_version, mock_acquire,
        mock_connect, mock_release, mock_download, mock_unzip,
        mock_create_db, mock_restore, mock_validate, mock_grant,
        mock_promote, mock_record, mock_notify, settings, tmp_path,
    ):
        mock_connect.return_value = MagicMock()
        mock_acquire.return_value = True
        version = _make_version()
        mock_fetch.return_value = version
        mock_last_version.return_value = "4.07"  # older -> is new

        zip_path = tmp_path / "downloads" / "vPICList_lite_2026_08.custom.zip"
        mock_download.return_value = DownloadResult(
            file_path=zip_path, url=version.postgres_custom_url, size_bytes=1000
        )
        dump_path = tmp_path / "extracted" / "2026_08" / "sample.backup"
        mock_unzip.return_value = ExtractedDump(dump_path=dump_path, size_bytes=500)
        mock_validate.return_value = ValidationResult(
            db_name="vpic_2026_08", table_row_counts={"pattern": 1_100_000},
            smoke_test_passed=True,
        )

        run_update_check(settings)

        mock_create_db.assert_called_once()
        mock_restore.assert_called_once()
        mock_grant.assert_called_once()
        mock_promote.assert_called_once()

        record_call_kwargs = mock_record.call_args.kwargs
        assert record_call_kwargs["status"] == "success"

        notify_call_kwargs = mock_notify.call_args.kwargs
        assert notify_call_kwargs["status"] == "success"
        assert notify_call_kwargs["version"] == "4.08"

        # cleanup must run for both the zip and the extract dir
        assert mock_cleanup.call_count == 2
        mock_release.assert_called_once()


class TestFailurePathLeavesProductionUntouched:
    @patch(f"{PATCH_TARGET}.send_slack_notification")
    @patch(f"{PATCH_TARGET}.record_history")
    @patch(f"{PATCH_TARGET}.promote")
    @patch(f"{PATCH_TARGET}.create_database")
    @patch(f"{PATCH_TARGET}.unzip_dump")
    @patch(f"{PATCH_TARGET}.download_file")
    @patch(f"{PATCH_TARGET}.release_lock")
    @patch(f"{PATCH_TARGET}.connect")
    @patch(f"{PATCH_TARGET}.acquire_lock")
    @patch(f"{PATCH_TARGET}.get_last_deployed_version")
    @patch(f"{PATCH_TARGET}.fetch_current_version")
    @patch(f"{PATCH_TARGET}.cleanup_temp_files")
    def test_extract_failure_never_calls_promote(
        self, mock_cleanup, mock_fetch, mock_last_version, mock_acquire,
        mock_connect, mock_release, mock_download, mock_unzip,
        mock_create_db, mock_promote, mock_record, mock_notify, settings,
    ):
        mock_connect.return_value = MagicMock()
        mock_acquire.return_value = True
        version = _make_version()
        mock_fetch.return_value = version
        mock_last_version.return_value = "4.07"
        mock_download.side_effect = ExtractError("download failed: connection reset")

        run_update_check(settings)

        mock_promote.assert_not_called()
        mock_create_db.assert_not_called()

        record_call_kwargs = mock_record.call_args.kwargs
        assert record_call_kwargs["status"] == "failure"

        notify_call_kwargs = mock_notify.call_args.kwargs
        assert notify_call_kwargs["status"] == "failure"

        mock_release.assert_called_once()

    @patch(f"{PATCH_TARGET}.send_slack_notification")
    @patch(f"{PATCH_TARGET}.record_history")
    @patch(f"{PATCH_TARGET}.promote")
    @patch(f"{PATCH_TARGET}.validate_database")
    @patch(f"{PATCH_TARGET}.restore_dump")
    @patch(f"{PATCH_TARGET}.create_database")
    @patch(f"{PATCH_TARGET}.unzip_dump")
    @patch(f"{PATCH_TARGET}.download_file")
    @patch(f"{PATCH_TARGET}.release_lock")
    @patch(f"{PATCH_TARGET}.connect")
    @patch(f"{PATCH_TARGET}.acquire_lock")
    @patch(f"{PATCH_TARGET}.get_last_deployed_version")
    @patch(f"{PATCH_TARGET}.fetch_current_version")
    @patch(f"{PATCH_TARGET}.cleanup_temp_files")
    def test_validation_failure_never_calls_promote(
        self, mock_cleanup, mock_fetch, mock_last_version, mock_acquire,
        mock_connect, mock_release, mock_download, mock_unzip,
        mock_create_db, mock_restore, mock_validate, mock_promote,
        mock_record, mock_notify, settings, tmp_path,
    ):
        mock_connect.return_value = MagicMock()
        mock_acquire.return_value = True
        version = _make_version()
        mock_fetch.return_value = version
        mock_last_version.return_value = "4.07"
        mock_download.return_value = DownloadResult(
            file_path=tmp_path / "a.zip", url=version.postgres_custom_url, size_bytes=1000
        )
        mock_unzip.return_value = ExtractedDump(
            dump_path=tmp_path / "a.backup", size_bytes=500
        )
        mock_validate.side_effect = LoadError("row count too low")

        run_update_check(settings)

        mock_promote.assert_not_called()

        record_call_kwargs = mock_record.call_args.kwargs
        assert record_call_kwargs["status"] == "failure"
        assert "row count too low" in record_call_kwargs["error"]