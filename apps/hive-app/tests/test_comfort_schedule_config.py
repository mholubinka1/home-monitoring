"""Config for the Comfort Schedule (issue #808): `comfort_schedule.control`, and
`presence.control: on` accepted only when the Comfort Schedule is `on`."""

from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from hive_app.common.config import HiveApplicationSettings

VALID_CONFIG: dict[str, Any] = {
    "hive": {"username": "someone@example.com", "password": "hunter2"},
    "mariadb": {
        "host": "localhost",
        "port": 3306,
        "database": "octopus",
        "username": "test",
        "password": "test",
    },
}
PRESENCE: dict[str, Any] = {
    "pihole": {"url": "http://pihole.example.lan", "password": "app-password"}
}


def test_the_comfort_schedule_control_defaults_to_off() -> None:
    # Given a config with no comfort_schedule section
    # When the settings load
    settings = HiveApplicationSettings.model_validate(VALID_CONFIG)

    # Then the Comfort Schedule is off
    assert settings.comfort_schedule.control == "off"


@pytest.mark.parametrize(
    ("written", "control"),
    [
        ("on", "on"),
        ("yes", "on"),
        ("off", "off"),
        ("no", "off"),
        ("dry_run", "dry_run"),
    ],
)
def test_control_written_unquoted_in_yaml_is_read_as_the_word(
    written: str, control: str
) -> None:
    # Given YAML 1.1, which reads an unquoted on, yes, off or no as a boolean
    loaded = yaml.safe_load(
        "hive: {username: u, password: p}\n"
        "mariadb: {host: h, port: 3306, database: d, username: u, password: p}\n"
        f"comfort_schedule:\n  control: {written}\n"
    )

    # When the settings load
    settings = HiveApplicationSettings.model_validate(loaded)

    # Then the control is the word the household meant
    assert settings.comfort_schedule.control == control


def test_presence_control_on_is_accepted_when_the_comfort_schedule_is_on() -> None:
    # Given presence control on and the Comfort Schedule on
    config = {
        **VALID_CONFIG,
        "presence": {**PRESENCE, "control": "on"},
        "comfort_schedule": {"control": "on"},
    }

    # When the settings load
    settings = HiveApplicationSettings.model_validate(config)

    # Then startup succeeds with both on
    assert settings.presence is not None
    assert settings.presence.control == "on"
    assert settings.comfort_schedule.control == "on"


@pytest.mark.parametrize("comfort_schedule", [{}, {"control": "dry_run"}])
def test_presence_control_on_is_refused_unless_the_comfort_schedule_is_on(
    comfort_schedule: dict[str, Any],
) -> None:
    # Given presence control on with the Comfort Schedule off (the default) or in
    # a dry run
    config = {
        **VALID_CONFIG,
        "presence": {**PRESENCE, "control": "on"},
        "comfort_schedule": comfort_schedule,
    }

    # When the settings load
    # Then startup is refused, naming the Comfort Schedule
    with pytest.raises(ValidationError, match="Comfort Schedule"):
        HiveApplicationSettings.model_validate(config)


def test_presence_control_dry_run_is_accepted_whatever_the_comfort_schedule() -> None:
    # Given presence in a dry run and the Comfort Schedule off
    config = {**VALID_CONFIG, "presence": {**PRESENCE, "control": "dry_run"}}

    # When the settings load
    settings = HiveApplicationSettings.model_validate(config)

    # Then startup succeeds
    assert settings.comfort_schedule.control == "off"
