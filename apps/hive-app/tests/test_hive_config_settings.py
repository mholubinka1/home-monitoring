from datetime import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from hive_app.common.config import HiveApplicationSettings, get_settings

VALID_CONFIG: dict[str, Any] = {
    "hive": {"username": "someone@example.com", "password": "hunter2"},
    "mariadb": {
        "host": "localhost",
        "port": 3306,
        "database": "octopus",
        "username": "test",
        "password": "test",
    },
    "ntfy": {"topic_url": "https://ntfy.sh/hive-app-reauth"},
    "location": {"latitude": 51.5, "longitude": -0.1},
}


def test_valid_config_yaml_produces_correctly_typed_settings() -> None:
    settings = HiveApplicationSettings.model_validate(VALID_CONFIG)

    assert settings.hive.username == "someone@example.com"
    assert settings.hive.password == "hunter2"
    assert settings.mariadb.host == "localhost"
    assert settings.mariadb.port == 3306
    assert settings.mariadb.database == "octopus"
    assert settings.ntfy is not None
    assert settings.ntfy.topic_url == "https://ntfy.sh/hive-app-reauth"
    assert settings.location is not None
    assert settings.location.latitude == 51.5
    assert settings.location.longitude == -0.1


def test_config_without_the_not_yet_used_optional_sections_still_loads() -> None:
    settings = HiveApplicationSettings.model_validate(
        {"hive": VALID_CONFIG["hive"], "mariadb": VALID_CONFIG["mariadb"]}
    )

    assert settings.hive.username == "someone@example.com"
    assert settings.ntfy is None
    assert settings.location is None


def test_a_stale_weather_underground_section_is_ignored_and_the_config_still_loads() -> (
    None
):
    settings = HiveApplicationSettings.model_validate(
        {
            **VALID_CONFIG,
            "weather_underground": {"api_key": "wu-test-key", "station_id": "IBECKE4"},
        }
    )

    assert settings.hive.username == "someone@example.com"
    assert settings.location is not None
    assert not hasattr(settings, "weather_underground")


def test_missing_required_config_field_raises_a_validation_error_naming_the_field() -> (
    None
):
    invalid_config = {
        "hive": {"username": "someone@example.com"},
        "mariadb": VALID_CONFIG["mariadb"],
        "ntfy": VALID_CONFIG["ntfy"],
        "location": VALID_CONFIG["location"],
    }

    with pytest.raises(ValidationError, match="password"):
        HiveApplicationSettings.model_validate(invalid_config)


def test_malformed_config_field_value_is_not_leaked_to_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaked_marker = "hunter2-do-not-log-me"
    hive_config = dict(VALID_CONFIG["hive"])
    hive_config["password"] = [leaked_marker]
    invalid_config = {
        "hive": hive_config,
        "mariadb": VALID_CONFIG["mariadb"],
        "ntfy": VALID_CONFIG["ntfy"],
        "location": VALID_CONFIG["location"],
    }
    config_file = tmp_path / "config.yml"
    config_file.write_text(yaml.safe_dump(invalid_config))

    logged_messages: list[str] = []
    monkeypatch.setattr(
        "hive_app.common.config.logger.critical", logged_messages.append
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
        f"hive:\n  username: user\n  password: {leaked_marker}: oops\n",
        encoding="utf-8",
    )
    logged_messages: list[str] = []
    monkeypatch.setattr(
        "hive_app.common.config.logger.critical", logged_messages.append
    )

    with pytest.raises(SystemExit):
        get_settings(str(config_file))

    logged_text = " ".join(logged_messages)
    assert leaked_marker not in logged_text
    assert "line 3" in logged_text


def test_no_timezone_setting_defaults_to_europe_london() -> None:
    settings = HiveApplicationSettings.model_validate(VALID_CONFIG)

    assert settings.timezone == "Europe/London"


def test_a_configured_timezone_is_kept() -> None:
    settings = HiveApplicationSettings.model_validate(
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
        "hive_app.common.config.logger.critical", logged_messages.append
    )

    with pytest.raises(SystemExit) as exit_info:
        get_settings(str(config_file))

    assert exit_info.value.code == 1
    assert "invalid or missing field(s): timezone" in " ".join(logged_messages)


def test_quiet_hours_default_to_10pm_until_8am() -> None:
    settings = HiveApplicationSettings.model_validate(VALID_CONFIG)

    assert settings.ntfy is not None
    assert settings.ntfy.quiet_hours.start == time(22, 0)
    assert settings.ntfy.quiet_hours.end == time(8, 0)


def test_quiet_hours_can_be_set_in_the_ntfy_section() -> None:
    settings = HiveApplicationSettings.model_validate(
        {
            **VALID_CONFIG,
            "ntfy": {
                "topic_url": "https://ntfy.sh/hive-app-reauth",
                "quiet_hours": {"start": "23:30", "end": "07:15"},
            },
        }
    )

    assert settings.ntfy is not None
    assert settings.ntfy.quiet_hours.start == time(23, 30)
    assert settings.ntfy.quiet_hours.end == time(7, 15)


@pytest.mark.parametrize("bad", ["25:00", "late", "22", 1320])
def test_quiet_hours_that_are_not_hh_mm_text_are_rejected(bad: object) -> None:
    with pytest.raises(ValidationError, match="HH:MM"):
        HiveApplicationSettings.model_validate(
            {
                **VALID_CONFIG,
                "ntfy": {
                    "topic_url": "https://ntfy.sh/hive-app-reauth",
                    "quiet_hours": {"start": bad, "end": "08:00"},
                },
            }
        )


PRESENCE_CONFIG: dict[str, Any] = {
    "pihole": {"url": "http://pihole.example.lan", "password": "app-password"},
}


def test_no_presence_section_means_no_presence_settings() -> None:
    assert HiveApplicationSettings.model_validate(VALID_CONFIG).presence is None


def test_a_presence_section_defaults_to_control_off_2_and_10_minute_windows_and_no_hostnames() -> (
    None
):
    settings = HiveApplicationSettings.model_validate(
        {**VALID_CONFIG, "presence": PRESENCE_CONFIG}
    )

    assert settings.presence is not None
    assert settings.presence.control == "off"
    assert settings.presence.last_seen_minutes == 2
    assert settings.presence.dns_minutes == 10
    assert settings.presence.phone_hostnames == []
    assert settings.presence.ignored_hostnames == []


def test_presence_values_set_in_config_are_kept() -> None:
    settings = HiveApplicationSettings.model_validate(
        {
            **VALID_CONFIG,
            "presence": {
                **PRESENCE_CONFIG,
                "control": "dry_run",
                "last_seen_minutes": 3,
                "dns_minutes": 15,
                "phone_hostnames": ["Alex-s-Phone"],
                "ignored_hostnames": ["Spare-Pixel-8"],
            },
        }
    )

    assert settings.presence is not None
    assert settings.presence.control == "dry_run"
    assert settings.presence.last_seen_minutes == 3
    assert settings.presence.dns_minutes == 15
    assert settings.presence.phone_hostnames == ["Alex-s-Phone"]
    assert settings.presence.ignored_hostnames == ["Spare-Pixel-8"]


@pytest.mark.parametrize("field", ["url", "password"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_pihole_url_or_password_is_refused(field: str, blank: str) -> None:
    pihole = {**PRESENCE_CONFIG["pihole"], field: blank}

    with pytest.raises(ValidationError, match="must not be blank"):
        HiveApplicationSettings.model_validate(
            {**VALID_CONFIG, "presence": {"pihole": pihole}}
        )


@pytest.mark.parametrize(
    ("written", "control"),
    [
        ("off", "off"),
        ("no", "off"),
        ("dry_run", "dry_run"),
    ],
)
def test_control_written_unquoted_in_yaml_is_read_as_the_word(
    written: str, control: str
) -> None:
    """YAML 1.1 reads an unquoted off or on as a boolean."""
    loaded = _presence_config_with_control_written(written)

    settings = HiveApplicationSettings.model_validate(loaded)

    assert settings.presence is not None
    assert settings.presence.control == control


@pytest.mark.parametrize("written", ["on", "yes"])
def test_control_written_unquoted_as_on_is_read_as_on_and_refused_until_the_comfort_schedule_exists(
    written: str,
) -> None:
    """YAML 1.1 reads an unquoted on or yes as a boolean, which means `on`;
    #808 relaxes the refusal once the Comfort Schedule exists."""
    loaded = _presence_config_with_control_written(written)

    with pytest.raises(ValidationError, match="Comfort Schedule"):
        HiveApplicationSettings.model_validate(loaded)


def _presence_config_with_control_written(written: str) -> Any:
    return yaml.safe_load(
        "hive: {username: u, password: p}\n"
        "mariadb: {host: h, port: 3306, database: d, username: u, password: p}\n"
        f"presence:\n  control: {written}\n"
        "  pihole: {url: 'http://pi.lan', password: p}\n"
    )


@pytest.mark.parametrize(
    "misspelled",
    [
        {"ignored_hostname": ["Spare-Pixel-8"]},
        {"phone_hostname": ["Alex-s-Phone"]},
        {"pihole": {**PRESENCE_CONFIG["pihole"], "passwd": "typo"}},
    ],
)
def test_a_misspelled_presence_key_is_refused_not_silently_ignored(
    misspelled: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        HiveApplicationSettings.model_validate(
            {**VALID_CONFIG, "presence": {**PRESENCE_CONFIG, **misspelled}}
        )


def test_a_pihole_url_without_a_scheme_is_refused() -> None:
    pihole = {**PRESENCE_CONFIG["pihole"], "url": "192.0.2.50"}

    with pytest.raises(ValidationError, match="http"):
        HiveApplicationSettings.model_validate(
            {**VALID_CONFIG, "presence": {"pihole": pihole}}
        )


def test_a_pihole_url_with_stray_spaces_is_stored_trimmed() -> None:
    pihole = {**PRESENCE_CONFIG["pihole"], "url": " http://pihole.example.lan "}

    settings = HiveApplicationSettings.model_validate(
        {**VALID_CONFIG, "presence": {"pihole": pihole}}
    )

    assert settings.presence is not None
    assert settings.presence.pihole.url == "http://pihole.example.lan"


@pytest.mark.parametrize("field", ["last_seen_minutes", "dns_minutes"])
@pytest.mark.parametrize("minutes", [0, -5])
def test_a_non_positive_presence_window_is_refused(field: str, minutes: int) -> None:
    with pytest.raises(ValidationError, match=field):
        HiveApplicationSettings.model_validate(
            {
                **VALID_CONFIG,
                "presence": {**PRESENCE_CONFIG, field: minutes},
            }
        )


def test_the_old_away_delay_key_is_refused_naming_it() -> None:
    with pytest.raises(ValidationError, match="away_delay_minutes"):
        HiveApplicationSettings.model_validate(
            {
                **VALID_CONFIG,
                "presence": {**PRESENCE_CONFIG, "away_delay_minutes": 30},
            }
        )


def test_the_pihole_password_is_not_in_the_settings_repr() -> None:
    settings = HiveApplicationSettings.model_validate(
        {**VALID_CONFIG, "presence": PRESENCE_CONFIG}
    )

    assert "app-password" not in repr(settings)
