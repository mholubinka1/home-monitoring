from datetime import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from octopus_app.common.config import ApplicationSettings, get_settings

VALID_CONFIG: dict[str, Any] = {
    "octopus": {"account_number": "A-1234ABCD", "api_key": "sk_live_test"},
    "mariadb": {
        "host": "localhost",
        "port": 3306,
        "database": "octopus",
        "username": "test",
        "password": "test",
    },
    "data_refresh": {
        "refresh_interval_hours": 4,
        "retention_days": 45,
    },
}


def test_valid_config_yaml_produces_correctly_typed_settings() -> None:
    settings = ApplicationSettings.model_validate(VALID_CONFIG)

    assert settings.octopus.account_number == "A-1234ABCD"
    assert settings.octopus.api_key == "sk_live_test"
    assert settings.mariadb.host == "localhost"
    assert settings.mariadb.port == 3306
    assert settings.mariadb.database == "octopus"
    assert settings.refresh_settings.refresh_interval == 4
    assert settings.refresh_settings.retention == 45


def test_missing_required_config_field_raises_a_validation_error_naming_the_field() -> (
    None
):
    invalid_config = {
        "octopus": {"account_number": "A-1234ABCD"},
        "mariadb": VALID_CONFIG["mariadb"],
        "data_refresh": VALID_CONFIG["data_refresh"],
    }

    with pytest.raises(ValidationError, match="api_key"):
        ApplicationSettings.model_validate(invalid_config)


def test_malformed_config_field_value_is_not_leaked_to_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaked_marker = "hunter2-do-not-log-me"
    mariadb_config = dict(VALID_CONFIG["mariadb"])
    mariadb_config["password"] = [leaked_marker]
    invalid_config = {
        "octopus": VALID_CONFIG["octopus"],
        "mariadb": mariadb_config,
        "data_refresh": VALID_CONFIG["data_refresh"],
    }
    config_file = tmp_path / "config.yml"
    config_file.write_text(yaml.safe_dump(invalid_config))

    logged_messages: list[str] = []
    monkeypatch.setattr(
        "octopus_app.common.config.logger.critical", logged_messages.append
    )

    with pytest.raises(SystemExit):
        get_settings(str(config_file))

    logged_text = " ".join(logged_messages)
    assert leaked_marker not in logged_text
    assert "password" in logged_text


def test_an_unparseable_config_logs_where_it_broke_but_not_the_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Parsing a string, PyYAML's error quotes the offending line (here the
    # password); parsing the open file, as get_settings does, it gives only the
    # line and column. Guards against loading the file as a string first.
    leaked_marker = "hunter2-do-not-log-me"
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        f"mariadb:\n  username: user\n  password: {leaked_marker}: oops\n",
        encoding="utf-8",
    )
    logged_messages: list[str] = []
    monkeypatch.setattr(
        "octopus_app.common.config.logger.critical", logged_messages.append
    )

    with pytest.raises(SystemExit):
        get_settings(str(config_file))

    logged_text = " ".join(logged_messages)
    assert leaked_marker not in logged_text
    assert "line 3" in logged_text


def test_no_timezone_setting_defaults_to_europe_london() -> None:
    settings = ApplicationSettings.model_validate(VALID_CONFIG)

    assert settings.timezone == "Europe/London"


def test_a_configured_timezone_is_kept() -> None:
    settings = ApplicationSettings.model_validate(
        {**VALID_CONFIG, "timezone": "America/New_York"}
    )

    assert settings.timezone == "America/New_York"


def test_an_unknown_timezone_fails_at_load_naming_the_timezone_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text(yaml.safe_dump({**VALID_CONFIG, "timezone": "Not/AZone"}))
    logged_messages: list[str] = []
    monkeypatch.setattr(
        "octopus_app.common.config.logger.critical", logged_messages.append
    )

    with pytest.raises(SystemExit) as exit_info:
        get_settings(str(config_file))

    assert exit_info.value.code == 1
    assert "invalid or missing field(s): timezone" in " ".join(logged_messages)


def test_a_configured_quoted_comfort_window_overrides_the_default(
    tmp_path: Path,
) -> None:
    # Given a config file that quotes the weekend start
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        yaml.safe_dump(VALID_CONFIG)
        + 'comfort_windows:\n  weekend:\n    start: "8:15"\n    end: "23:00"\n',
        encoding="utf-8",
    )

    # When the settings load
    windows = get_settings(str(config_file)).comfort_windows

    # Then the weekend uses it and the weekday keeps its default
    assert (windows.weekend.start, windows.weekend.end) == (time(8, 15), time(23, 0))
    assert windows.weekday.start == time(6, 30)


def test_an_unquoted_comfort_time_stops_startup_telling_the_user_to_quote_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an unquoted 7:45, which YAML reads as the number 465
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        yaml.safe_dump(VALID_CONFIG)
        + "comfort_windows:\n  weekend:\n    start: 7:45\n    end: '00:00'\n",
        encoding="utf-8",
    )
    logged_messages: list[str] = []
    monkeypatch.setattr(
        "octopus_app.common.config.logger.critical", logged_messages.append
    )

    # When the settings load
    with pytest.raises(SystemExit) as exit_info:
        get_settings(str(config_file))

    # Then startup stops naming the field
    assert exit_info.value.code == 1
    assert "comfort_windows.weekend.start" in " ".join(logged_messages)


def test_comfort_windows_default_to_the_household_hours() -> None:
    # Given a config with no comfort_windows setting
    # When the settings load
    settings = ApplicationSettings.model_validate(VALID_CONFIG)

    # Then weekdays are 06:30 to midnight and weekends 07:45 to midnight
    windows = settings.comfort_windows
    assert (windows.weekday.start, windows.weekday.end) == (time(6, 30), time(0, 0))
    assert (windows.weekend.start, windows.weekend.end) == (time(7, 45), time(0, 0))
