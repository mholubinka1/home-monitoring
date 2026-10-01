from datetime import UTC, datetime

import pytest

from hive_app.common.exceptions import HiveReauthRequired
from hive_app.data.heating import HeatingRetriever
from hive_app.data.model import HeatingStatus, HiveAuthState
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.notify import ReauthAlert


class _FakeHiveSource:
    """A fake HiveSource for tests: a fixed HeatingStatus and a real
    MariaDBClient underneath, mirroring test_pricing_retrieval.py's
    _RealPricingSource seam shape for PricingRetriever. Auth verbs are
    unused by HeatingRetriever -- stubbed only to satisfy the HiveSource
    Protocol's full shape, same spirit as _FakeAuthHiveSource's unused
    heating verbs in test_hive_authentication.py."""

    def __init__(self, mariadb: MariaDBClient, status: HeatingStatus) -> None:
        self._mariadb = mariadb
        self._status = status

    def fetch_heating_status(self) -> HeatingStatus:
        return self._status

    def persist_heating_status(self, status: HeatingStatus) -> None:
        self._mariadb.write_heating_status(status)

    def read_auth_state(self) -> HiveAuthState | None:
        raise NotImplementedError

    def login(self) -> HiveAuthState:
        raise NotImplementedError

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        raise NotImplementedError

    def persist_auth_state(self, state: HiveAuthState) -> None:
        raise NotImplementedError


def _make_status() -> HeatingStatus:
    return HeatingStatus(
        polled_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        current_temp=19.5,
        target_temp=21.0,
        mode="SCHEDULE",
        state="ON",
        boost_active=False,
        boost_ends_at=None,
        schedule={"now": "21.0", "next": "18.0", "later": "19.0"},
    )


def test_refresh_persists_the_polled_heating_status(
    mariadb_client: MariaDBClient,
) -> None:
    status = _make_status()
    source = _FakeHiveSource(mariadb_client, status)

    HeatingRetriever(source).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.heating_status).all()

    assert len(stored) == 1
    assert stored[0].current_temp == status.current_temp
    assert stored[0].target_temp == status.target_temp
    assert stored[0].mode == status.mode
    assert stored[0].state == status.state
    assert stored[0].boost_active == status.boost_active
    assert stored[0].schedule == status.schedule


class _FailingHiveSource:
    """A fake HiveSource whose poll raises an ordinary transient error --
    proves HeatingRetriever.refresh() does no retry/backoff/swallowing of
    its own (that's the generic job-wrapper's job); it just propagates."""

    def fetch_heating_status(self) -> HeatingStatus:
        raise ConnectionError("Hive backend unreachable")

    def persist_heating_status(self, status: HeatingStatus) -> None:
        raise AssertionError("persist_heating_status should never be reached")

    def read_auth_state(self) -> HiveAuthState | None:
        raise NotImplementedError

    def login(self) -> HiveAuthState:
        raise NotImplementedError

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        raise NotImplementedError

    def persist_auth_state(self, state: HiveAuthState) -> None:
        raise NotImplementedError


def test_refresh_propagates_a_transient_poll_failure_without_swallowing_it() -> None:
    source = _FailingHiveSource()

    with pytest.raises(ConnectionError, match="Hive backend unreachable"):
        HeatingRetriever(source).refresh()


class _ReauthRequiredHiveSource:
    """A fake HiveSource whose poll raises the specific unrecoverable
    re-auth error -- proves HeatingRetriever.refresh() notifies on this
    error type specifically, distinct from ordinary transient failures."""

    def fetch_heating_status(self) -> HeatingStatus:
        raise HiveReauthRequired("Hive's remembered device is no longer recognized.")

    def persist_heating_status(self, status: HeatingStatus) -> None:
        raise AssertionError("persist_heating_status should never be reached")

    def read_auth_state(self) -> HiveAuthState | None:
        raise NotImplementedError

    def login(self) -> HiveAuthState:
        raise NotImplementedError

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        raise NotImplementedError

    def persist_auth_state(self, state: HiveAuthState) -> None:
        raise NotImplementedError


class _SpyReauthNotifier:
    def __init__(self) -> None:
        self.calls = 0

    def notify_reauth_required(self) -> None:
        self.calls += 1


def test_refresh_notifies_reauth_required_when_hive_source_needs_live_sms() -> None:
    source = _ReauthRequiredHiveSource()
    notifier = _SpyReauthNotifier()

    with pytest.raises(HiveReauthRequired):
        HeatingRetriever(source, ReauthAlert(notifier)).refresh()

    assert notifier.calls == 1


def test_refresh_does_not_notify_reauth_required_for_an_ordinary_transient_failure() -> (
    None
):
    source = _FailingHiveSource()
    notifier = _SpyReauthNotifier()

    with pytest.raises(ConnectionError, match="Hive backend unreachable"):
        HeatingRetriever(source, ReauthAlert(notifier)).refresh()

    assert notifier.calls == 0


class _FailingReauthNotifier:
    """A fake notifier whose own POST fails -- proves a notifier failure
    doesn't displace the original HiveReauthRequired that triggered it."""

    def notify_reauth_required(self) -> None:
        raise ConnectionError("ntfy.sh unreachable")


def test_refresh_still_raises_the_original_reauth_error_when_notifying_fails() -> None:
    source = _ReauthRequiredHiveSource()
    notifier = _FailingReauthNotifier()

    with pytest.raises(
        HiveReauthRequired, match="Hive's remembered device is no longer recognized."
    ):
        HeatingRetriever(source, ReauthAlert(notifier)).refresh()


class _NotifierThatFailsOnce:
    """A fake notifier whose first call raises, and whose later calls
    succeed -- proves a failed delivery attempt is retried on the next
    reauth failure rather than being permanently suppressed."""

    def __init__(self) -> None:
        self.calls = 0
        self._raised_once = False

    def notify_reauth_required(self) -> None:
        self.calls += 1
        if not self._raised_once:
            self._raised_once = True
            raise ConnectionError("ntfy.sh unreachable")


def test_refresh_retries_notifying_after_a_failed_delivery_attempt() -> None:
    source = _ReauthRequiredHiveSource()
    notifier = _NotifierThatFailsOnce()
    retriever = HeatingRetriever(source, ReauthAlert(notifier))

    with pytest.raises(HiveReauthRequired):
        retriever.refresh()
    with pytest.raises(HiveReauthRequired):
        retriever.refresh()
    with pytest.raises(HiveReauthRequired):
        retriever.refresh()

    assert notifier.calls == 2


def test_refresh_notifies_only_once_across_repeated_reauth_failures() -> None:
    source = _ReauthRequiredHiveSource()
    notifier = _SpyReauthNotifier()
    retriever = HeatingRetriever(source, ReauthAlert(notifier))

    for _ in range(3):
        with pytest.raises(HiveReauthRequired):
            retriever.refresh()

    assert notifier.calls == 1


class _ScriptedHiveSource:
    """A fake HiveSource that plays back a fixed sequence of outcomes, one
    per fetch_heating_status() call -- proves HeatingRetriever resets its
    notify-once state after a recovery, so a later, distinct reauth
    incident is notified again rather than being silently deduplicated."""

    def __init__(self, mariadb: MariaDBClient, outcomes: list[bool]) -> None:
        self._mariadb = mariadb
        self._outcomes = list(outcomes)

    def fetch_heating_status(self) -> HeatingStatus:
        if self._outcomes.pop(0):
            raise HiveReauthRequired(
                "Hive's remembered device is no longer recognized."
            )
        return _make_status()

    def persist_heating_status(self, status: HeatingStatus) -> None:
        self._mariadb.write_heating_status(status)

    def read_auth_state(self) -> HiveAuthState | None:
        raise NotImplementedError

    def login(self) -> HiveAuthState:
        raise NotImplementedError

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        raise NotImplementedError

    def persist_auth_state(self, state: HiveAuthState) -> None:
        raise NotImplementedError


def test_refresh_notifies_again_after_a_recovery_and_a_new_reauth_failure(
    mariadb_client: MariaDBClient,
) -> None:
    source = _ScriptedHiveSource(mariadb_client, outcomes=[True, False, True])
    notifier = _SpyReauthNotifier()
    retriever = HeatingRetriever(source, ReauthAlert(notifier))

    with pytest.raises(HiveReauthRequired):
        retriever.refresh()
    retriever.refresh()
    with pytest.raises(HiveReauthRequired):
        retriever.refresh()

    assert notifier.calls == 2
