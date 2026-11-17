import logging
from datetime import UTC, datetime, timedelta

import pytest

from hive_app.common.config import PiholeSettings, PresenceSettings
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.pihole import PiholeDevice, PiholeError
from hive_app.data.presence import PresenceRetriever

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
RANDOMISED_MAC = "c2:00:00:00:00:01"
FACTORY_MAC = "c0:00:00:00:00:01"
PASSWORD = "secret"


class FakePiholeSource:
    def __init__(self, devices: list[PiholeDevice]) -> None:
        self.devices = devices
        self.error: PiholeError | None = None

    def read_devices(self) -> list[PiholeDevice]:
        if self.error is not None:
            raise self.error
        return self.devices


def _settings(**overrides: list[str]) -> PresenceSettings:
    return PresenceSettings(
        pihole=PiholeSettings(url="http://pihole.example.lan", password=PASSWORD),
        **overrides,
    )


def _device(
    hostname: str, hwaddr: str = RANDOMISED_MAC, minutes_ago: float = 1
) -> PiholeDevice:
    return PiholeDevice(
        hwaddr=hwaddr,
        hostnames=[hostname],
        last_query=NOW - timedelta(minutes=minutes_ago),
    )


def _refresh(
    mariadb_client: MariaDBClient,
    devices: list[PiholeDevice],
    now: datetime = NOW,
    **overrides: list[str],
) -> None:
    PresenceRetriever(
        FakePiholeSource(devices), mariadb_client, _settings(**overrides)
    ).refresh(now)


def _periods(mariadb_client: MariaDBClient) -> list[model.presence_period]:
    with mariadb_client.session_read_scope() as session:
        rows = session.query(model.presence_period).order_by("id").all()
        session.expunge_all()
        return rows


def _open_state(mariadb_client: MariaDBClient) -> str | None:
    return mariadb_client.read_open_presence_state()


def test_a_phone_that_made_a_lookup_five_minutes_ago_opens_a_someone_home_period(
    mariadb_client: MariaDBClient,
) -> None:
    source = FakePiholeSource(
        [
            PiholeDevice(
                hwaddr=RANDOMISED_MAC,
                hostnames=["Alex-s-Phone"],
                last_query=NOW - timedelta(minutes=5),
            )
        ]
    )

    PresenceRetriever(source, mariadb_client, _settings()).refresh(NOW)

    periods = _periods(mariadb_client)
    assert len(periods) == 1
    assert periods[0].state == "someone_home"
    assert periods[0].ended_at is None
    assert periods[0].phones_seen == 1


def test_only_factory_mac_devices_active_opens_a_no_one_home_period(
    mariadb_client: MariaDBClient,
) -> None:
    _refresh(mariadb_client, [_device("Alex-s-Phone", hwaddr=FACTORY_MAC)])

    periods = _periods(mariadb_client)
    assert [(p.state, p.phones_seen) for p in periods] == [("no_one_home", 0)]


def test_an_active_tablet_does_not_make_someone_home(
    mariadb_client: MariaDBClient,
) -> None:
    _refresh(mariadb_client, [_device("Galaxy-Tab-S9", minutes_ago=1)])

    assert _open_state(mariadb_client) == "no_one_home"


def test_a_lookup_exactly_at_the_away_delay_still_counts_but_one_second_later_does_not(
    mariadb_client: MariaDBClient,
) -> None:
    _refresh(mariadb_client, [_device("Alex-s-Phone", minutes_ago=30)])
    assert _open_state(mariadb_client) == "someone_home"

    _refresh(
        mariadb_client,
        [
            PiholeDevice(
                RANDOMISED_MAC,
                ["Alex-s-Phone"],
                NOW - timedelta(minutes=30, seconds=1),
            )
        ],
    )
    assert _open_state(mariadb_client) == "no_one_home"


def test_a_device_without_a_mac_is_not_a_phone_unless_configured_as_one(
    mariadb_client: MariaDBClient,
) -> None:
    device = _device("Pixel-8", hwaddr="ip-192.0.2.5")

    _refresh(mariadb_client, [device])
    assert _open_state(mariadb_client) == "no_one_home"

    _refresh(mariadb_client, [device], phone_hostnames=["Pixel-8"])
    assert _open_state(mariadb_client) == "someone_home"


def test_a_configured_phone_hostname_counts_whatever_its_mac(
    mariadb_client: MariaDBClient,
) -> None:
    _refresh(
        mariadb_client,
        [_device("Kevin", hwaddr=FACTORY_MAC)],
        phone_hostnames=["Kevin"],
    )

    assert _open_state(mariadb_client) == "someone_home"


def test_an_ignored_hostname_is_never_a_phone(mariadb_client: MariaDBClient) -> None:
    _refresh(mariadb_client, [_device("Pixel-8")], ignored_hostnames=["Pixel-8"])

    assert _open_state(mariadb_client) == "no_one_home"


def test_a_change_of_state_closes_the_open_period_and_opens_the_next_at_the_same_instant(
    mariadb_client: MariaDBClient,
) -> None:
    _refresh(mariadb_client, [_device("Alex-s-Phone")])
    later = NOW + timedelta(hours=1)

    _refresh(mariadb_client, [], now=later)

    first, second = _periods(mariadb_client)
    assert (first.state, second.state) == ("someone_home", "no_one_home")
    assert first.ended_at == later.replace(tzinfo=None)
    assert second.started_at == later.replace(tzinfo=None)
    assert second.ended_at is None


def test_deciding_the_same_state_again_changes_no_row_even_with_a_new_retriever(
    mariadb_client: MariaDBClient,
) -> None:
    _refresh(mariadb_client, [_device("Alex-s-Phone")])
    before = [(p.id, p.started_at, p.ended_at) for p in _periods(mariadb_client)]

    _refresh(
        mariadb_client,
        [_device("Alex-s-Phone", minutes_ago=2)],
        now=NOW + timedelta(minutes=1),
    )

    after = [(p.id, p.started_at, p.ended_at) for p in _periods(mariadb_client)]
    assert after == before


def test_an_unreachable_pihole_opens_an_unknown_period_without_raising_or_logging_the_password(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    source = FakePiholeSource([])
    source.error = PiholeError("Pi-hole could not be read (ConnectTimeout)")

    with caplog.at_level(logging.DEBUG):
        PresenceRetriever(source, mariadb_client, _settings()).refresh(NOW)

    assert _open_state(mariadb_client) == "unknown"
    assert PASSWORD not in caplog.text


def test_a_pihole_outage_logs_one_warning_not_one_per_minute(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    source = FakePiholeSource([])
    source.error = PiholeError("Pi-hole could not be read (ConnectTimeout)")
    retriever = PresenceRetriever(source, mariadb_client, _settings())

    with caplog.at_level(logging.WARNING):
        for minute in range(3):
            retriever.refresh(NOW + timedelta(minutes=minute))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_a_transition_is_logged_with_the_phone_count_and_no_hostname_at_info(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        _refresh(mariadb_client, [_device("Alex-s-Phone")])

    assert "someone_home (1 phone(s) active)" in caplog.text
    assert "Alex-s-Phone" not in caplog.text
