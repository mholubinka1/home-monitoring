import logging
from datetime import UTC, datetime

import pytest
import requests
import responses
from responses import matchers

from hive_app.common.config import PiholeSettings
from hive_app.data.pihole import PiholeApiSource, PiholeError

BASE_URL = "http://pihole.example.lan"
AUTH_URL = f"{BASE_URL}/api/auth"
DEVICES_URL = f"{BASE_URL}/api/network/devices"
PASSWORD = "app-password-do-not-log"
SESSION_ID = "session-one"

DEVICES_BODY = {
    "devices": [
        {
            "hwaddr": "c2:00:00:00:00:01",
            "lastQuery": 1794916800,
            "ips": [{"ip": "192.0.2.10", "name": "Alex-s-Phone.lan"}, {"ip": "x"}],
        },
        {"hwaddr": "ip-192.0.2.5", "lastQuery": 0, "ips": []},
    ]
}


def _source() -> PiholeApiSource:
    return PiholeApiSource(PiholeSettings(url=BASE_URL, password=PASSWORD))


def _login_ok(sid: str = SESSION_ID) -> None:
    responses.add(responses.POST, AUTH_URL, json={"session": {"sid": sid}})


@responses.activate
def test_devices_are_read_with_their_hostnames_and_utc_last_lookup_time() -> None:
    _login_ok()
    responses.add(responses.GET, DEVICES_URL, json=DEVICES_BODY)

    phone, never_queried = _source().read_devices()

    assert phone.hwaddr == "c2:00:00:00:00:01"
    assert phone.hostnames == ["Alex-s-Phone.lan"]
    assert phone.last_query == datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    assert never_queried.hwaddr == "ip-192.0.2.5"
    assert never_queried.last_query is None


@responses.activate
def test_the_request_names_max_devices_and_max_addresses_and_sends_the_session_id() -> (
    None
):
    _login_ok()
    responses.add(
        responses.GET,
        DEVICES_URL,
        json=DEVICES_BODY,
        match=[
            matchers.query_param_matcher({"max_devices": "500", "max_addresses": "20"}),
            matchers.header_matcher({"X-FTL-SID": SESSION_ID}),
        ],
    )

    _source().read_devices()


@responses.activate
def test_the_session_is_reused_across_polls() -> None:
    _login_ok()
    responses.add(responses.GET, DEVICES_URL, json=DEVICES_BODY)
    source = _source()

    source.read_devices()
    source.read_devices()

    logins = [c for c in responses.calls if c.request.url == AUTH_URL]
    assert len(logins) == 1


@responses.activate
def test_a_401_triggers_one_fresh_login_and_one_retry() -> None:
    _login_ok("session-one")
    _login_ok("session-two")
    responses.add(responses.GET, DEVICES_URL, status=401)
    responses.add(
        responses.GET,
        DEVICES_URL,
        json=DEVICES_BODY,
        match=[matchers.header_matcher({"X-FTL-SID": "session-two"})],
    )

    devices = _source().read_devices()

    assert len(devices) == 2


@responses.activate
def test_a_second_401_is_one_pihole_error() -> None:
    _login_ok()
    _login_ok()
    responses.add(responses.GET, DEVICES_URL, status=401)
    responses.add(responses.GET, DEVICES_URL, status=401)

    with pytest.raises(PiholeError):
        _source().read_devices()


@responses.activate
def test_a_rejected_password_is_a_pihole_error_that_does_not_reveal_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses.add(responses.POST, AUTH_URL, status=401)

    with caplog.at_level(logging.DEBUG), pytest.raises(PiholeError) as raised:
        _source().read_devices()

    assert PASSWORD not in str(raised.value)
    assert PASSWORD not in caplog.text


@responses.activate
@pytest.mark.parametrize(
    "failure",
    [
        {"body": requests.ConnectTimeout(f"timed out for {PASSWORD}")},
        {"status": 500},
        {"body": "not json", "status": 200},
        {"json": {"unexpected": []}},
    ],
)
def test_every_kind_of_failure_is_one_pihole_error_without_the_password(
    failure: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    _login_ok()
    responses.add(responses.GET, DEVICES_URL, **failure)

    with caplog.at_level(logging.DEBUG), pytest.raises(PiholeError) as raised:
        _source().read_devices()

    assert PASSWORD not in str(raised.value)
    assert PASSWORD not in caplog.text


@responses.activate
def test_a_last_lookup_time_python_cannot_represent_is_one_pihole_error() -> None:
    _login_ok()
    body = {
        "devices": [{"hwaddr": "c2:00:00:00:00:01", "lastQuery": 10**20, "ips": []}]
    }
    responses.add(responses.GET, DEVICES_URL, json=body)

    with pytest.raises(PiholeError):
        _source().read_devices()


@responses.activate
def test_a_device_with_a_null_hardware_address_is_read_as_a_blank_address() -> None:
    _login_ok()
    body = {"devices": [{"hwaddr": None, "lastQuery": 1794916800, "ips": []}]}
    responses.add(responses.GET, DEVICES_URL, json=body)

    (device,) = _source().read_devices()

    assert device.hwaddr == ""


@responses.activate
def test_logout_releases_the_session_and_tolerates_failure() -> None:
    _login_ok()
    responses.add(responses.GET, DEVICES_URL, json=DEVICES_BODY)
    responses.add(
        responses.DELETE,
        AUTH_URL,
        body=requests.ConnectionError("down"),
        match=[matchers.header_matcher({"X-FTL-SID": SESSION_ID})],
    )
    source = _source()
    source.read_devices()

    source.logout()

    assert responses.calls[-1].request.method == "DELETE"
