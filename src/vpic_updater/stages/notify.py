"""
Stage 4: Notify.

Sends a Slack notification via webhook reporting the outcome of an update
cycle -- version, release date, and deployment status. Deliberately never
raises on delivery failure: a broken Slack webhook should be logged and
surfaced in the pipeline's own logs, but must never be treated the same
as an actual deployment failure, and must never block or roll back a
deployment that already succeeded.
"""

import logging
from datetime import date

import requests

from vpic_updater.core.config import DEFAULT_SLACK_NOTIFICATION_TIMEOUT_SECONDS
from vpic_updater.models.notification import NotificationResult

logger = logging.getLogger("vpic_updater.notify")

_STATUS_COLORS = {
    "success": "#36a64f",
    "failure": "#d00000",
}


def send_slack_notification(
    webhook_url: str,
    version: str,
    released_on: date | str,
    status: str,
    detail: str = "",
    timeout: int = DEFAULT_SLACK_NOTIFICATION_TIMEOUT_SECONDS,
) -> NotificationResult:
    """Post a formatted deployment notification to Slack.

    `status` should be "success" or "failure" -- anything else still gets
    sent (with a neutral color) rather than rejected, since a notification
    attempt should never itself become a new failure mode.
    """
    color = _STATUS_COLORS.get(status, "#999999")
    released_on_str = str(released_on)

    payload = {
        "attachments": [
            {
                "color": color,
                "title": f"vPIC Database Update — {status.upper()}",
                "fields": [
                    {"title": "Version", "value": version, "short": True},
                    {"title": "Released", "value": released_on_str, "short": True},
                    {"title": "Status", "value": status, "short": True},
                ],
                "text": detail,
            }
        ]
    }

    try:
        response = requests.post(webhook_url, json=payload, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        # Deliberately not re-raised -- see module docstring. A failed
        # Slack post is logged and reported back via NotificationResult,
        # but never propagated as a pipeline error.
        logger.error("Slack notification failed: %s", exc)
        return NotificationResult(sent=False, error=str(exc))

    logger.info(
        "Slack notification sent (version=%s status=%s status_code=%d)",
        version, status, response.status_code,
    )
    return NotificationResult(sent=True, status_code=response.status_code)