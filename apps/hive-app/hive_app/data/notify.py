import logging.config
from datetime import timedelta
from logging import Logger, getLogger
from typing import Protocol

import requests

from hive_app.common.logging import APP_LOGGER_NAME, config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

REQUEST_TIMEOUT_SECONDS = 10
REAUTH_RUNBOOK_URL = (
    "https://github.com/mholubinka1/home-monitoring/blob/main/"
    "deployments/hive-app/REAUTH_RUNBOOK.md"
)
REAUTH_REQUIRED_BODY = "Hive needs a live SMS 2FA code to recover. See the runbook."
AUTH_RECOVERED_BODY = "Hive login restored. Heating polling has resumed."


class ReauthNotifier(Protocol):
    def notify_reauth_required(self) -> None: ...

    def notify_auth_recovered(self) -> None: ...


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


class NtfyReauthNotifier(_NtfyPoster):
    """Posts one of exactly two structured notifications to a configured
    ntfy.sh topic URL -- "re-auth required" (high priority, links the
    runbook) and "auth recovered" (default priority) -- see ADR-0018.
    ntfy.sh's public-topic API needs no auth/JSON: a plain-text body POSTed
    to the topic URL, with Title/Priority/Tags/Click sent as headers (which
    must stay latin-1 safe, so ASCII only)."""

    def notify_reauth_required(self) -> None:
        self._post(
            REAUTH_REQUIRED_BODY,
            {
                "Title": "hive-app: re-authentication required",
                "Priority": "high",
                "Tags": "warning,key",
                "Click": REAUTH_RUNBOOK_URL,
            },
        )
        logger.info("Sent Hive re-auth alert to the configured ntfy.sh topic.")

    def notify_auth_recovered(self) -> None:
        self._post(
            AUTH_RECOVERED_BODY,
            {
                "Title": "hive-app: authentication recovered",
                "Priority": "default",
                "Tags": "white_check_mark",
            },
        )
        logger.info("Sent Hive auth-recovered notice to the configured ntfy.sh topic.")


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
    (see ADR-0031), in the same ntfy.sh style as NtfyReauthNotifier."""

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


class ReauthAlert:
    """Notifies at most once per Hive re-auth incident, shared by every
    caller that can hit HiveReauthRequired (startup auth and the poll).

    Only the first *successful* notification of an incident sets the flag --
    every retry attempt and subsequent scheduled run raises the same
    HiveReauthRequired until someone completes the live SMS login, so
    without this guard one incident would page repeatedly instead of once
    (see ADR-0018's "narrowly-scoped, not a general alert channel"
    framing). A failed delivery attempt does NOT set the flag, so it is
    retried on the next failure rather than being permanently suppressed.

    clear() (a successful poll or startup auth) sends the one "auth
    recovered" notice, but only if a "required" alert was actually
    delivered by this process (the marker is in memory, so a restart in
    between forgets it) -- a normal successful poll sends nothing. State resets
    either way; a failed "recovered" delivery is logged and swallowed,
    never retried, so polling is unaffected.

    The flag is plain, unsynchronised state: startup auth completes before
    the scheduler registers the heating poll, so the two callers never run
    concurrently. Revisit if that ordering ever changes.
    """

    _notifier: ReauthNotifier | None
    _notified: bool

    def __init__(self, notifier: ReauthNotifier | None) -> None:
        self._notifier = notifier
        self._notified = False

    def notify_once(self) -> None:
        if self._notified or self._notifier is None:
            return
        try:
            self._notifier.notify_reauth_required()
        except Exception:
            logger.exception(
                "Failed to send Hive re-auth alert (raised from startup auth "
                "or the heating poll); will retry on the next reauth "
                "failure. The original HiveReauthRequired error still "
                "propagates."
            )
            return
        self._notified = True

    def clear(self) -> None:
        notifier, was_notified = self._notifier, self._notified
        self._notified = False
        if not was_notified or notifier is None:
            return
        try:
            notifier.notify_auth_recovered()
        except Exception:
            logger.exception(
                "Failed to send Hive auth-recovered notice; not retrying. "
                "Polling continues."
            )
