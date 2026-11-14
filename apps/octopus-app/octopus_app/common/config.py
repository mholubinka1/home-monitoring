import logging.config
import re
import sys
from datetime import time
from logging import Logger, getLogger

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from common.config import MariaDBSettings
from common.local_day import DEFAULT_TIMEZONE, validate_timezone
from octopus_app.common.logging import APP_LOGGER_NAME, config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

_CLOCK_TIME = re.compile(r"(\d{1,2}):(\d{2})")


class OctopusAPISettings(BaseModel):
    account_number: str
    api_key: str


class RefreshSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    refresh_interval: int = Field(alias="refresh_interval_hours")
    retention: int = Field(alias="retention_days")


class ComfortWindow(BaseModel):
    """When the room should be comfortable, in local time. An end of 00:00 is the
    midnight at the end of the day."""

    start: time
    end: time

    @field_validator("start", "end", mode="before")
    @classmethod
    def _time_must_be_a_quoted_clock_time(cls, value: object) -> time:
        # YAML 1.1 reads an unquoted 7:45 as the number 465 (base 60), so only a
        # quoted string is a time the household meant (a time object is the default).
        if isinstance(value, time):
            return value
        match = _CLOCK_TIME.fullmatch(value) if isinstance(value, str) else None
        if match is None or int(match[1]) > 23 or int(match[2]) > 59:
            raise ValueError('use a quoted clock time such as "07:45"')
        return time(int(match[1]), int(match[2]))


class ComfortWindows(BaseModel):
    weekday: ComfortWindow = ComfortWindow(start=time(6, 30), end=time(0, 0))
    weekend: ComfortWindow = ComfortWindow(start=time(7, 45), end=time(0, 0))


class ApplicationSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    octopus: OctopusAPISettings
    mariadb: MariaDBSettings
    refresh_settings: RefreshSettings = Field(alias="data_refresh")
    timezone: str = DEFAULT_TIMEZONE
    comfort_windows: ComfortWindows = ComfortWindows()

    @field_validator("timezone")
    @classmethod
    def _timezone_must_be_a_known_zone(cls, value: str) -> str:
        validate_timezone(value)
        return value


def get_settings(
    config_file_path: str,
) -> ApplicationSettings:
    try:
        with open(config_file_path, "r", encoding="utf-8") as file:
            yaml_settings = yaml.safe_load(file)
        settings = ApplicationSettings.model_validate(yaml_settings)
        logger.info(f"Successfully loaded settings from {config_file_path}")
        return settings
    except ValidationError as e:
        invalid_fields = ", ".join(
            ".".join(str(part) for part in error["loc"]) for error in e.errors()
        )
        logger.critical(
            f"Failed to load application settings from {config_file_path}: "
            f"invalid or missing field(s): {invalid_fields}"
        )
        sys.exit(1)
    except Exception as e:
        logger.critical(
            f"Failed to load application settings from {config_file_path}: {e}"
        )
        sys.exit(1)
