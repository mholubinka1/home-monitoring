from datetime import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from octopus_app.common.config import ApplicationSettings, ComfortWindow, get_settings

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


def test_a_configured_day_overrides_its_default_and_the_others_keep_theirs(
    tmp_path: Path,
) -> None:
    # Given a config file that sets only Friday's window
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        yaml.safe_dump(VALID_CONFIG)
        + 'comfort_windows:\n  friday:\n    start: "8:16"\n    end: "01:00"\n',
        encoding="utf-8",
    )

    # When the settings load
    windows = get_settings(str(config_file)).comfort_windows

    # Then Friday uses it and every other day keeps its default
    assert (windows.friday.start, windows.friday.end) == (time(8, 16), time(1, 0))
    assert (windows.thursday.start, windows.thursday.end) == (time(7, 30), time(23, 0))
    assert (windows.saturday.start, windows.saturday.end) == (time(8, 30), time(0, 30))


def test_an_unquoted_comfort_time_stops_startup_telling_the_user_to_quote_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an unquoted 7:45, which YAML reads as the number 465
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        yaml.safe_dump(VALID_CONFIG)
        + "comfort_windows:\n  saturday:\n    start: 7:45\n    end: '00:00'\n",
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
    assert "comfort_windows.saturday.start" in " ".join(logged_messages)


def test_comfort_windows_default_to_the_household_hours() -> None:
    # Given a config with no comfort_windows setting
    # When the settings load
    settings = ApplicationSettings.model_validate(VALID_CONFIG)

    # Then each window ends by the next day's kind: 23:00 before a weekday and
    # 00:30 the next morning before a weekend day
    windows = settings.comfort_windows
    for day in ("monday", "tuesday", "wednesday", "thursday"):
        assert (getattr(windows, day).start, getattr(windows, day).end) == (
            time(7, 30),
            time(23, 0),
        )
    assert (windows.friday.start, windows.friday.end) == (time(7, 30), time(0, 30))
    assert (windows.saturday.start, windows.saturday.end) == (time(8, 30), time(0, 30))
    assert (windows.sunday.start, windows.sunday.end) == (time(8, 30), time(23, 0))


def test_a_comfort_time_on_an_odd_minute_is_rounded_earlier_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a Saturday window configured at 07:45, an odd minute
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        yaml.safe_dump(VALID_CONFIG)
        + 'comfort_windows:\n  saturday:\n    start: "07:45"\n    end: "23:59"\n',
        encoding="utf-8",
    )
    logged_messages: list[str] = []
    monkeypatch.setattr(
        "octopus_app.common.config.logger.warning", logged_messages.append
    )

    # When the settings load
    windows = get_settings(str(config_file)).comfort_windows

    # Then the window opens at 07:44 and closes at 23:58, and the log says so
    assert (windows.saturday.start, windows.saturday.end) == (time(7, 44), time(23, 58))
    assert any("07:45" in message and "07:44" in message for message in logged_messages)
    assert any("23:59" in message and "23:58" in message for message in logged_messages)


def test_a_comfort_time_on_an_even_minute_is_kept_and_not_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        yaml.safe_dump(VALID_CONFIG)
        + 'comfort_windows:\n  saturday:\n    start: "08:16"\n    end: "23:00"\n',
        encoding="utf-8",
    )
    logged_messages: list[str] = []
    monkeypatch.setattr(
        "octopus_app.common.config.logger.warning", logged_messages.append
    )

    windows = get_settings(str(config_file)).comfort_windows

    assert windows.saturday.start == time(8, 16)
    assert not logged_messages


def test_a_comfort_window_that_collapses_when_its_times_are_rounded_is_rejected() -> (
    None
):
    # Given a one-minute window, 07:44 to 07:45, whose end rounds down to its start
    # (and so would be read as lasting until the next day)
    with pytest.raises(ValueError, match="2-minute marks"):
        ComfortWindow.model_validate({"start": "07:44", "end": "07:45"})


def test_a_window_ending_at_midnight_is_not_a_collapsed_window() -> None:
    window = ComfortWindow.model_validate({"start": "07:45", "end": "00:00"})

    assert (window.start, window.end) == (time(7, 44), time(0, 0))


def test_a_collapsing_comfort_window_stops_startup_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a Monday window of 07:44 to 07:45, which rounds to nothing
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        yaml.safe_dump(VALID_CONFIG)
        + 'comfort_windows:\n  monday:\n    start: "07:44"\n    end: "07:45"\n',
        encoding="utf-8",
    )
    logged_messages: list[str] = []
    monkeypatch.setattr(
        "octopus_app.common.config.logger.critical", logged_messages.append
    )

    # When the settings load
    with pytest.raises(SystemExit) as exit_info:
        get_settings(str(config_file))

    # Then startup stops naming the field and the reason
    assert exit_info.value.code == 1
    logged = " ".join(logged_messages)
    assert "comfort_windows.monday" in logged
    assert "where it starts" in logged


def test_a_window_whose_start_equals_its_end_is_accepted_and_still_rounded() -> None:
    window = ComfortWindow.model_validate({"start": "07:45", "end": "07:45"})

    assert (window.start, window.end) == (time(7, 44), time(7, 44))


def test_the_old_weekday_and_weekend_keys_stop_startup_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a config written for the old shape
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        yaml.safe_dump(VALID_CONFIG)
        + 'comfort_windows:\n  weekday:\n    start: "07:30"\n    end: "23:00"\n',
        encoding="utf-8",
    )
    logged_messages: list[str] = []
    monkeypatch.setattr(
        "octopus_app.common.config.logger.critical", logged_messages.append
    )

    # When the settings load
    with pytest.raises(SystemExit) as exit_info:
        get_settings(str(config_file))

    # Then startup stops naming the old key, not silently using the defaults
    assert exit_info.value.code == 1
    assert "comfort_windows.weekday" in " ".join(logged_messages)
