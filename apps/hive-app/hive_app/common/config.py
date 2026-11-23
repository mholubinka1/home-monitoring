import logging.config
import sys
from datetime import datetime, time
from logging import Logger, getLogger
from typing import Literal, Self

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
from hive_app.common.logging import APP_LOGGER_NAME, config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)


class HiveSettings(BaseModel):
    username: str
    password: str
    auth_state_path: str = "/config/hive_auth_state.json"


class QuietHours(BaseModel):
    """The local-time window in which no ntfy message is sent (ADR-0031). It
    may span midnight; equal start and end means there are no quiet hours.
    Written as quoted "HH:MM" text: YAML reads an unquoted 22:00 as the
    number 1320, which must not be mistaken for a time."""

    start: time = time(22, 0)
    end: time = time(8, 0)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _must_be_hh_mm_text(cls, value: object) -> time:
        if isinstance(value, time):
            return value
        problem = f'must be quoted "HH:MM" text such as "22:00", got {value!r}'
        if not isinstance(value, str):
            raise ValueError(problem)  # noqa: TRY004 -- pydantic reports ValueError
        try:
            return datetime.strptime(value, "%H:%M").time()
        except ValueError:
            raise ValueError(problem) from None


class NtfySettings(BaseModel):
    topic_url: str
    quiet_hours: QuietHours = QuietHours()


class LocationSettings(BaseModel):
    latitude: float
    longitude: float


class PiholeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    # repr=False keeps the app password out of any logged settings object.
    password: str = Field(repr=False)

    @field_validator("url", "password")
    @classmethod
    def _must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("url")
    @classmethod
    def _url_must_name_its_scheme(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed.lower().startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return trimmed


def _yaml_boolean_as_word(value: object) -> object:
    """YAML 1.1 reads an unquoted off, no, on or yes as False or True."""
    if isinstance(value, bool):
        return "on" if value else "off"
    return value


class PresenceSettings(BaseModel):
    # A mistyped key (ignored_hostname) must refuse startup, not be dropped:
    # the device the household meant to ignore would still count as a phone.
    model_config = ConfigDict(extra="forbid")

    control: Literal["off", "dry_run", "on"] = "off"
    pihole: PiholeSettings
    away_delay_minutes: int = Field(default=30, gt=0)
    phone_hostnames: list[str] = []
    ignored_hostnames: list[str] = []

    @field_validator("control", mode="before")
    @classmethod
    def _read_yaml_booleans_as_the_words(cls, value: object) -> object:
        return _yaml_boolean_as_word(value)


class ComfortScheduleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The temperature outside the Comfort Window, and the one the Away Override
    # holds while no one is home; the thermostat accepts 5-32 C in half degrees.
    away_temperature: float = Field(default=10.0, ge=5, le=32, multiple_of=0.5)
    control: Literal["off", "dry_run", "on"] = "off"

    @field_validator("control", mode="before")
    @classmethod
    def _read_yaml_booleans_as_the_words(cls, value: object) -> object:
        return _yaml_boolean_as_word(value)


class HiveApplicationSettings(BaseModel):
    hive: HiveSettings
    mariadb: MariaDBSettings
    # Optional: the weather jobs always register. Without `location`, the
    # Weather Location is derived from the Octopus Account Postcode, then IP
    # geolocation (ADR-0028); observations and forecasts both come from
    # Open-Meteo. ntfy is consumed by the Hive data-lost watchdog (ADR-0031).
    # Both stay optional so a deployment doesn't need to populate config for
    # capabilities it doesn't use. Unknown keys (e.g. a section left over
    # from a removed provider) are ignored, not rejected.
    ntfy: NtfySettings | None = None
    location: LocationSettings | None = None
    # Presence from Pi-hole phones; without it no presence job is scheduled.
    presence: PresenceSettings | None = None
    comfort_schedule: ComfortScheduleSettings = Field(
        default_factory=ComfortScheduleSettings
    )
    # The IANA zone that defines a "local day"; must match octopus-app's.
    timezone: str = DEFAULT_TIMEZONE

    @field_validator("timezone")
    @classmethod
    def _timezone_must_be_a_known_zone(cls, value: str) -> str:
        validate_timezone(value)
        return value

    @model_validator(mode="after")
    def _presence_on_needs_the_comfort_schedule_on(self) -> Self:
        if (
            self.presence is not None
            and self.presence.control == "on"
            and self.comfort_schedule.control != "on"
        ):
            raise ValueError(
                "presence.control: 'on' needs comfort_schedule.control: on, "
                "so use 'dry_run' until the Comfort Schedule is on"
            )
        return self


def get_settings(
    config_file_path: str,
) -> HiveApplicationSettings:
    try:
        with open(config_file_path, "r", encoding="utf-8") as file:
            yaml_settings = yaml.safe_load(file)
        settings = HiveApplicationSettings.model_validate(yaml_settings)
        logger.info(f"Successfully loaded settings from {config_file_path}")
        return settings
    except ValidationError as e:
        # A cross-section rule has no field path, so name it by its message
        # (our own ValueError text, never the offending input).
        invalid_fields = ", ".join(
            ".".join(str(part) for part in error["loc"]) or error["msg"]
            for error in e.errors()
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
