import logging.config
import re
import sys
from datetime import time
from logging import Logger, getLogger

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from common.config import MariaDBSettings
from common.local_day import DEFAULT_TIMEZONE, validate_timezone
from octopus_app.common.logging import APP_LOGGER_NAME, config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

_CLOCK_TIME = re.compile(r"(\d{1,2}):(\d{2})")
COMFORT_TIME_STEP_MINUTES = 2


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

    @model_validator(mode="after")
    def _times_must_stay_apart_on_the_forecast_steps(self) -> "ComfortWindow":
        """Rounds both times to the forecast's 2-minute marks. A window that would
        end where it starts once rounded is refused: it would otherwise be read as
        lasting until the next day."""
        start, end = _earlier_even_minute(self.start), _earlier_even_minute(self.end)
        if start == end and self.start != self.end:
            raise ValueError(
                f"comfort window {self.start:%H:%M} to {self.end:%H:%M} would end "
                "where it starts once its times are rounded earlier to 2-minute marks"
            )
        self.start, self.end = start, end
        return self


def _earlier_even_minute(clock_time: time) -> time:
    """The room forecast steps in 2 minutes, so a comfort time on an odd minute
    is rounded earlier to the 2-minute mark."""
    if clock_time.minute % COMFORT_TIME_STEP_MINUTES == 0:
        return clock_time
    rounded = clock_time.replace(minute=clock_time.minute - 1)
    logger.warning(
        f"Comfort window time {clock_time:%H:%M} is not on a 2-minute mark; "
        f"using {rounded:%H:%M}."
    )
    return rounded


class ComfortWindows(BaseModel):
    weekday: ComfortWindow = ComfortWindow(start=time(6, 30), end=time(0, 0))
    weekend: ComfortWindow = ComfortWindow(start=time(7, 44), end=time(0, 0))


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
        reasons = "; ".join(error["msg"] for error in e.errors())
        logger.critical(
            f"Failed to load application settings from {config_file_path}: "
            f"invalid or missing field(s): {invalid_fields} ({reasons})"
        )
        sys.exit(1)
    except Exception as e:
        logger.critical(
            f"Failed to load application settings from {config_file_path}: {e}"
        )
        sys.exit(1)
