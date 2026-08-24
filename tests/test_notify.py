"""Unit tests for the notify (Slack) stage. Fully offline."""

from datetime import date

import os
import requests
import requests_mock

from vpic_updater.stages.notify import send_slack_notification

WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")


class TestSendSlackNotification:
    def test_successful_notification(self):
        with requests_mock.Mocker() as m:
            m.post(WEBHOOK_URL, status_code=200, text="ok")
            result = send_slack_notification(
                WEBHOOK_URL, version="4.08", released_on=date(2026, 8, 15),
                status="success", detail="New vPIC database deployed to production.",
            )

        assert result.sent is True
        assert result.status_code == 200
        assert result.error is None

        # Confirm the payload actually contains what the acceptance
        # criteria require: version, date, status.
        sent_payload = m.request_history[0].json()
        fields = sent_payload["attachments"][0]["fields"]
        field_values = {f["title"]: f["value"] for f in fields}
        assert field_values["Version"] == "4.08"
        assert field_values["Released"] == "2026-08-15"
        assert field_values["Status"] == "success"

    def test_failure_notification_uses_failure_color(self):
        with requests_mock.Mocker() as m:
            m.post(WEBHOOK_URL, status_code=200, text="ok")
            send_slack_notification(
                WEBHOOK_URL, version="4.08", released_on=date(2026, 8, 15),
                status="failure", detail="pg_restore failed",
            )

        sent_payload = m.request_history[0].json()
        assert sent_payload["attachments"][0]["color"] == "#d00000"

    def test_network_failure_does_not_raise(self):
        with requests_mock.Mocker() as m:
            m.post(WEBHOOK_URL, exc=requests.exceptions.ConnectTimeout)
            result = send_slack_notification(
                WEBHOOK_URL, version="4.08", released_on=date(2026, 8, 15),
                status="success",
            )

        assert result.sent is False
        assert result.error is not None

    def test_http_error_status_does_not_raise(self):
        with requests_mock.Mocker() as m:
            m.post(WEBHOOK_URL, status_code=500, text="Internal Server Error")
            result = send_slack_notification(
                WEBHOOK_URL, version="4.08", released_on=date(2026, 8, 15),
                status="success",
            )

        assert result.sent is False
        assert result.error is not None

    def test_accepts_string_released_on(self):
        """released_on may arrive as a plain string (e.g. read back from
        the control-db as text) rather than a date object -- both must work."""
        with requests_mock.Mocker() as m:
            m.post(WEBHOOK_URL, status_code=200, text="ok")
            result = send_slack_notification(
                WEBHOOK_URL, version="4.08", released_on="2026-08-15",
                status="success",
            )

        assert result.sent is True
        sent_payload = m.request_history[0].json()
        fields = sent_payload["attachments"][0]["fields"]
        field_values = {f["title"]: f["value"] for f in fields}
        assert field_values["Released"] == "2026-08-15"