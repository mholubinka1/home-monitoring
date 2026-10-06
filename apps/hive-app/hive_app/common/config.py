import logging.config
import sys
from logging import Logger, getLogger

import yaml
from pydantic import BaseModel, ValidationError

from common.config import MariaDBSettings
from hive_app.common.logging import APP_LOGGER_NAME, config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)


class HiveSettings(BaseModel):
    username: str
    password: str
    auth_state_path: str = "/config/hive_auth_state.json"


class WeatherUndergroundSettings(BaseModel):
    api_key: str
    station_id: str | None = None


class NtfySettings(BaseModel):
    topic_url: str


class LocationSettings(BaseModel):
    latitude: float
    longitude: float


class HiveApplicationSettings(BaseModel):
    hive: HiveSettings
    mariadb: MariaDBSettings
    # Optional: the weather jobs always register. Without `location`, the
    # Weather Location is derived from the Octopus Account Postcode, then IP
    # geolocation (ADR-0028); without `weather_underground`, observations come
    # from Open-Meteo. ntfy is consumed by #509's re-auth alerting. All three
    # stay optional so a deployment doesn't need to populate config for
    # capabilities it doesn't use.
    weather_underground: WeatherUndergroundSettings | None = None
    ntfy: NtfySettings | None = None
    location: LocationSettings | None = None


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
