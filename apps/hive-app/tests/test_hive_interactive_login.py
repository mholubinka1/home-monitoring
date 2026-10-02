from collections.abc import Callable
from typing import Any

import pytest
from apyhiveapi.helper import hive_exceptions

from hive_app.common.config import HiveSettings
from hive_app.data.hive_client import HiveApiSource
from hive_app.data.mysql.client import MariaDBClient


def _hive_settings() -> HiveSettings:
    return HiveSettings(username="user@example.com", password="hunter2")


def test_an_operator_who_enters_a_valid_sms_code_gets_a_remembered_device_auth_state(
    mariadb_client: MariaDBClient, install_fake_hive: Callable[..., Any]
) -> None:
    fake_hive = install_fake_hive()

    state = HiveApiSource(_hive_settings(), mariadb_client).interactive_login(
        lambda: "123456"
    )

    assert fake_hive.submitted_codes == ["123456"]
    assert fake_hive.submitted_sessions == ["sms-session"]
    assert fake_hive.call_order == [
        "login",
        "sms2fa",
        "device_registration",
        "startSession",
    ]
    assert state.refresh_token == "refresh-tok"
    assert state.device_group_key == "group-key"
    assert state.device_key == "device-key"
    assert state.device_password == "generated-device-password"


def _unused_code_provider() -> str:
    raise AssertionError("no SMS code should have been requested")


def test_an_account_that_logs_in_without_a_challenge_is_never_asked_for_a_code(
    mariadb_client: MariaDBClient, install_fake_hive: Callable[..., Any]
) -> None:
    fake_hive = install_fake_hive(login_result={"AuthenticationResult": {}})
    fake_hive.tokens.tokenData["refreshToken"] = "existing-refresh-tok"
    fake_hive.auth.device_group_key = "group-key"
    fake_hive.auth.device_key = "device-key"
    fake_hive.auth.device_password = "device-password"

    state = HiveApiSource(_hive_settings(), mariadb_client).interactive_login(
        _unused_code_provider
    )

    assert fake_hive.call_order == ["login", "startSession"]
    assert state.refresh_token == "existing-refresh-tok"
    assert state.device_key == "device-key"


def test_an_operator_who_enters_an_invalid_sms_code_sees_the_apyhiveapi_error(
    mariadb_client: MariaDBClient, install_fake_hive: Callable[..., Any]
) -> None:
    fake_hive = install_fake_hive(sms_error=hive_exceptions.HiveInvalid2FACode())

    with pytest.raises(hive_exceptions.HiveInvalid2FACode):
        HiveApiSource(_hive_settings(), mariadb_client).interactive_login(
            lambda: "000000"
        )

    assert "device_registration" not in fake_hive.call_order


def test_a_login_where_cognito_offers_no_device_to_remember_is_reported_as_an_error(
    mariadb_client: MariaDBClient, install_fake_hive: Callable[..., Any]
) -> None:
    # Without NewDeviceMetadata the device cannot be remembered, so the next
    # restart would need another SMS code -- the command must not report
    # success (and let the caller persist a state that cannot resume).
    install_fake_hive(
        sms_result={
            "AuthenticationResult": {
                "IdToken": "id-tok",
                "AccessToken": "access-tok",
                "RefreshToken": "refresh-tok",
            }
        }
    )

    with pytest.raises(RuntimeError, match="remember"):
        HiveApiSource(_hive_settings(), mariadb_client).interactive_login(
            lambda: "123456"
        )


def test_an_unexpected_login_challenge_is_reported_as_an_error(
    mariadb_client: MariaDBClient, install_fake_hive: Callable[..., Any]
) -> None:
    install_fake_hive(login_result={"ChallengeName": "SOMETHING_ELSE"})

    with pytest.raises(RuntimeError):
        HiveApiSource(_hive_settings(), mariadb_client).interactive_login(
            _unused_code_provider
        )
