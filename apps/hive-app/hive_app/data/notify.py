import logging.config
from logging import Logger, getLogger
from typing import Protocol

import requests

from hive_app.common.logging import APP_LOGGER_NAME, config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

REQUEST_TIMEOUT_SECONDS = 10
REAUTH_REQUIRED_MESSAGE = (
    "hive-app: Hive re-authentication required. The remembered device is no "
    "longer recognized and a live SMS 2FA code is needed to recover."
)


class ReauthNotifier(Protocol):
    def notify_reauth_required(self) -> None: ...


class NtfyReauthNotifier:
    """Posts a plain-text alert to a configured ntfy.sh topic URL when
    called -- see ADR-0018. ntfy.sh's public-topic API needs no auth/JSON,
    just the message body POSTed as plain text to the topic URL."""

    def __init__(self, topic_url: str) -> None:
        self._topic_url = topic_url

    def notify_reauth_required(self) -> None:
        response = requests.post(
            self._topic_url,
            data=REAUTH_REQUIRED_MESSAGE.encode("utf-8"),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        logger.info("Sent Hive re-auth alert to the configured ntfy.sh topic.")


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
    """

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
                "Failed to send Hive re-auth alert; will retry on the next "
                "reauth failure. The original HiveReauthRequired error "
                "still propagates."
            )
            return
        self._notified = True

    def clear(self) -> None:
        self._notified = False
