import logging.config
from datetime import timedelta
from logging import Logger, getLogger

import requests

from hive_app.common.logging import APP_LOGGER_NAME, config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

REQUEST_TIMEOUT_SECONDS = 10
REAUTH_RUNBOOK_URL = (
    "https://github.com/mholubinka1/home-monitoring/blob/main/"
    "deployments/hive-app/REAUTH_RUNBOOK.md"
)


class _NtfyPoster:
    def __init__(self, topic_url: str) -> None:
        self._topic_url = topic_url

    def _post(self, body: str, headers: dict[str, str]) -> None:
        response = requests.post(
            self._topic_url,
            data=body.encode("utf-8"),
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()


def describe_duration(duration: timedelta) -> str:
    """Human text: minutes under 2 hours, hours and minutes up to 48 hours,
    then days and hours."""
    minutes = int(duration.total_seconds() // 60)
    if minutes < 120:
        return _plural(minutes, "minute")
    if minutes < 48 * 60:
        hours, minutes = divmod(minutes, 60)
        return f"{_plural(hours, 'hour')} {_plural(minutes, 'minute')}"
    days, hours = divmod(minutes // 60, 24)
    return f"{_plural(days, 'day')} {_plural(hours, 'hour')}"


def _plural(count: int, unit: str) -> str:
    return f"{count} {unit}" if count == 1 else f"{count} {unit}s"


class NtfyDataNotifier(_NtfyPoster):
    """Posts the "Hive data lost" and "Hive data restored" notifications
    (see ADR-0031), posted to a configured ntfy.sh topic URL. ntfy.sh's public-topic API needs
    no auth/JSON: a plain-text body POSTed to the topic URL, with
    Title/Priority/Tags/Click sent as headers (which must stay latin-1 safe,
    so ASCII only)."""

    def notify_data_lost(
        self, age: timedelta, reason: str | None, reminder: bool = False
    ) -> None:
        body = f"No Hive heating data for {describe_duration(age)}."
        if reason is not None:
            body += f" Latest poll failure: {reason}."
        self._post(
            body,
            {
                "Title": (
                    "hive-app: Hive data still lost"
                    if reminder
                    else "hive-app: Hive data lost"
                ),
                "Priority": "high",
                "Tags": "warning",
                "Click": REAUTH_RUNBOOK_URL,
            },
        )
        logger.info("Sent Hive data-lost alert to the configured ntfy.sh topic.")

    def notify_data_restored(self, outage: timedelta) -> None:
        self._post(
            f"Hive heating data is arriving again after {describe_duration(outage)}.",
            {
                "Title": "hive-app: Hive data restored",
                "Priority": "default",
                "Tags": "white_check_mark",
            },
        )
        logger.info("Sent Hive data-restored notice to the configured ntfy.sh topic.")
