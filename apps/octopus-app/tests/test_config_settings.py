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
