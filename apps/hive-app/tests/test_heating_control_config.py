"""Config for presence-based heating control (issue #803): the Away Temperature
under `comfort_schedule`, and `presence.control: on` refused until the Comfort
Schedule exists (#808 relaxes that)."""

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
}
PRESENCE: dict[str, Any] = {
    "pihole": {"url": "http://pihole.example.lan", "password": "app-password"}
}


def test_the_away_temperature_defaults_to_seven_degrees() -> None:
    settings = HiveApplicationSettings.model_validate(VALID_CONFIG)

    assert settings.comfort_schedule.away_temperature == 7.0


def test_a_configured_away_temperature_is_kept() -> None:
    settings = HiveApplicationSettings.model_validate(
        {**VALID_CONFIG, "comfort_schedule": {"away_temperature": 12.5}}
    )

    assert settings.comfort_schedule.away_temperature == 12.5


@pytest.mark.parametrize("temperature", [4.5, 32.5, 7.3, 0, 40])
def test_an_away_temperature_outside_5_to_32_or_off_a_half_degree_is_refused(
    temperature: float,
) -> None:
    with pytest.raises(ValidationError):
        HiveApplicationSettings.model_validate(
            {**VALID_CONFIG, "comfort_schedule": {"away_temperature": temperature}}
        )


def test_a_misspelled_comfort_schedule_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        HiveApplicationSettings.model_validate(
            {**VALID_CONFIG, "comfort_schedule": {"away_temp": 7.0}}
        )


def test_startup_is_refused_for_presence_control_on_until_the_comfort_schedule_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {**VALID_CONFIG, "presence": {**PRESENCE, "control": "on"}}
    config_file = tmp_path / "config.yml"
    config_file.write_text(yaml.safe_dump(config))
    logged: list[str] = []
    monkeypatch.setattr("hive_app.common.config.logger.critical", logged.append)

    with pytest.raises(SystemExit):
        get_settings(str(config_file))

    assert "presence.control" in " ".join(logged)
