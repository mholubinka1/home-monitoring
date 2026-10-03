from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from apyhiveapi.helper import hive_exceptions

from hive_app.common.config import HiveSettings
from hive_app.data.hive_client import HiveApiSource
from hive_app.data.mysql.client import MariaDBClient
from hive_app.login import main


def _write_config(tmp_path: Path) -> tuple[Path, Path]:
    auth_state_path = tmp_path / "hive_auth_state.json"
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        "hive:\n"
        "  username: user@example.com\n"
        "  password: hunter2\n"
        f"  auth_state_path: {auth_state_path}\n"
        "mariadb:\n"
        "  host: localhost\n"
        "  port: 3306\n"
        "  database: main\n"
        "  username: test\n"
        "  password: test\n",
        encoding="utf-8",
    )
    return config_file, auth_state_path


@pytest.mark.usefixtures("mariadb_client")
def test_an_operator_who_enters_a_valid_code_has_the_auth_state_written_and_sees_success(
    tmp_path: Path,
    install_fake_hive: Callable[..., Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_file, auth_state_path = _write_config(tmp_path)
    install_fake_hive()

    exit_code = main(
        ["--config-file", str(config_file)], code_provider=lambda: "123456"
    )

    assert exit_code == 0
    assert auth_state_path.exists()
    assert "success" in capsys.readouterr().out.lower()


def test_a_restarted_hive_app_resumes_from_the_saved_state_without_another_sms_code(
    tmp_path: Path,
    mariadb_client: MariaDBClient,
    install_fake_hive: Callable[..., Any],
) -> None:
    config_file, auth_state_path = _write_config(tmp_path)
    fake_hive = install_fake_hive()
    main(["--config-file", str(config_file)], code_provider=lambda: "123456")
    calls_before_restart = len(fake_hive.call_order)

    # A restart is a fresh HiveApiSource reading what the command persisted.
    settings = HiveSettings(
        username="user@example.com",
        password="hunter2",
        auth_state_path=str(auth_state_path),
    )
    restarted = HiveApiSource(settings, mariadb_client)
    saved_state = restarted.read_auth_state()
    assert saved_state is not None
    resumed_state = restarted.resume(saved_state)

    resume_calls = fake_hive.call_order[calls_before_restart:]
    assert resume_calls == ["startSession"]
    assert fake_hive.submitted_codes == ["123456"]
    assert fake_hive.start_session_configs[-1] == {
        "tokens": {
            "token": "",
            "refreshToken": "refresh-tok",
            "accessToken": "",
        },
        "device_data": ("group-key", "device-key", "generated-device-password"),
    }
    assert resumed_state.refresh_token == "refresh-tok"


def _no_code_expected() -> str:
    raise AssertionError("no SMS code should have been requested")


def test_an_account_that_logs_in_with_a_new_device_and_no_challenge_saves_a_resumable_state(
    tmp_path: Path,
    mariadb_client: MariaDBClient,
    install_fake_hive: Callable[..., Any],
) -> None:
    config_file, auth_state_path = _write_config(tmp_path)
    fake_hive = install_fake_hive(
        login_result={
            "AuthenticationResult": {
                "IdToken": "id-tok",
                "AccessToken": "access-tok",
                "RefreshToken": "refresh-tok",
                "NewDeviceMetadata": {
                    "DeviceGroupKey": "group-key",
                    "DeviceKey": "device-key",
                },
            }
        }
    )

    exit_code = main(
        ["--config-file", str(config_file)], code_provider=_no_code_expected
    )

    assert exit_code == 0
    assert auth_state_path.exists()
    calls_before_restart = len(fake_hive.call_order)
    settings = HiveSettings(
        username="user@example.com",
        password="hunter2",
        auth_state_path=str(auth_state_path),
    )
    restarted = HiveApiSource(settings, mariadb_client)
    saved_state = restarted.read_auth_state()
    assert saved_state is not None
    restarted.resume(saved_state)

    assert fake_hive.call_order[calls_before_restart:] == ["startSession"]
    assert fake_hive.start_session_configs[-1] == {
        "tokens": {
            "token": "",
            "refreshToken": "refresh-tok",
            "accessToken": "",
        },
        "device_data": ("group-key", "device-key", "generated-device-password"),
    }


@pytest.mark.usefixtures("mariadb_client")
def test_an_invalid_code_fails_the_command_and_leaves_the_existing_auth_state_untouched(
    tmp_path: Path,
    install_fake_hive: Callable[..., Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_file, auth_state_path = _write_config(tmp_path)
    auth_state_path.write_text("previous", encoding="utf-8")
    install_fake_hive(sms_error=hive_exceptions.HiveInvalid2FACode())

    exit_code = main(
        ["--config-file", str(config_file)], code_provider=lambda: "000000"
    )

    assert exit_code == 1
    assert auth_state_path.read_text(encoding="utf-8") == "previous"
    assert capsys.readouterr().err != ""


@pytest.mark.usefixtures("mariadb_client")
def test_an_unexpected_challenge_fails_the_command_and_writes_no_auth_state(
    tmp_path: Path,
    install_fake_hive: Callable[..., Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_file, auth_state_path = _write_config(tmp_path)
    install_fake_hive(login_result={"ChallengeName": "SOMETHING_ELSE"})

    exit_code = main(
        ["--config-file", str(config_file)], code_provider=lambda: "123456"
    )

    assert exit_code == 1
    assert not auth_state_path.exists()
    assert capsys.readouterr().err != ""


@pytest.mark.usefixtures("mariadb_client")
def test_a_login_that_yields_no_device_to_remember_fails_the_command_and_writes_no_auth_state(
    tmp_path: Path,
    install_fake_hive: Callable[..., Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_file, auth_state_path = _write_config(tmp_path)
    install_fake_hive(
        login_result={
            "AuthenticationResult": {
                "IdToken": "id-tok",
                "AccessToken": "access-tok",
                "RefreshToken": "refresh-tok",
            }
        }
    )

    exit_code = main(
        ["--config-file", str(config_file)], code_provider=_no_code_expected
    )

    assert exit_code == 1
    assert not auth_state_path.exists()
    assert "remembered device" in capsys.readouterr().err


def test_a_config_that_cannot_be_loaded_fails_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        ["--config-file", str(tmp_path / "missing.yml")], code_provider=lambda: "1"
    )

    assert exit_code == 1
    assert capsys.readouterr().err != ""


def test_running_the_command_without_a_config_file_says_so_rather_than_naming_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main([], code_provider=lambda: "1")

    assert exit_code == 1
    error_output = capsys.readouterr().err
    assert "--config-file" in error_output
    assert "None" not in error_output
