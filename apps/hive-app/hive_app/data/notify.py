import logging.config
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


class NtfyReauthNotifier:
    """Posts one of exactly two structured notifications to a configured
    ntfy.sh topic URL -- "re-auth required" (high priority, links the
    runbook) and "auth recovered" (default priority) -- see ADR-0018.
    ntfy.sh's public-topic API needs no auth/JSON: a plain-text body POSTed
    to the topic URL, with Title/Priority/Tags/Click sent as headers (which
    must stay latin-1 safe, so ASCII only)."""

    def __init__(self, topic_url: str) -> None:
        self._topic_url = topic_url

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

    def _post(self, body: str, headers: dict[str, str]) -> None:
        response = requests.post(
            self._topic_url,
            data=body.encode("utf-8"),
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()


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
    delivered -- a normal successful poll sends nothing. State resets
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
