import logging.config
from logging import Logger, getLogger

from hive_app.common.exceptions import HiveReauthRequired
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.heating import HiveSource
from hive_app.data.model import HiveAuthState
from hive_app.data.notify import ReauthAlert

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)


class HiveAuthenticator:
    """Startup auth branching for a HiveSource: resumes via token/device
    refresh when a hive_auth_state row already exists, otherwise performs a
    full interactive login -- persisting the result either way so a
    subsequent restart can resume. Kept as its own small class (rather than
    folded into main()) so this branching logic stays unit-testable in
    isolation and main() stays thin wiring.

    A HiveReauthRequired here (or from the poll) is only *alerted*, never
    recovered from: completing a live re-login needs an interactive SMS 2FA
    step this codebase cannot perform unattended; the alert only tells the
    owner that someone must complete that step by hand."""

    _client: HiveSource
    _alert: ReauthAlert

    def __init__(self, client: HiveSource, alert: ReauthAlert | None = None) -> None:
        self._client = client
        self._alert = alert if alert is not None else ReauthAlert(None)

    def authenticate(self) -> None:
        try:
            state = self._resume_or_login()
        except HiveReauthRequired:
            self._alert.notify_once()
            raise
        self._client.persist_auth_state(state)
        self._alert.clear()

    def _resume_or_login(self) -> HiveAuthState:
        state = self._client.read_auth_state()
        if state is None:
            logger.info("No persisted Hive auth state found; logging in.")
            return self._client.login()
        logger.info("Persisted Hive auth state found; resuming via refresh.")
        return self._client.resume(state)
