import logging.config
import sys
from logging import Logger, getLogger

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from common.config import MariaDBSettings
from common.local_day import DEFAULT_TIMEZONE, validate_timezone
from octopus_app.common.logging import APP_LOGGER_NAME, config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)


class OctopusAPISettings(BaseModel):
    account_number: str
    api_key: str


class RefreshSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    refresh_interval: int = Field(alias="refresh_interval_hours")
    retention: int = Field(alias="retention_days")


class ApplicationSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    octopus: OctopusAPISettings
    mariadb: MariaDBSettings
    refresh_settings: RefreshSettings = Field(alias="data_refresh")
    timezone: str = DEFAULT_TIMEZONE

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
