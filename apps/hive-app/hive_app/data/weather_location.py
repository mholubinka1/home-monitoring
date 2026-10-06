import logging.config
from logging import Logger, getLogger

from hive_app.common.config import LocationSettings
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.geocoding_client import GeocodingClient
from hive_app.data.model import ResolvedLocation
from hive_app.data.mysql.client import MariaDBClient

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)


class WeatherLocationUnavailableError(Exception):
    """No Weather Location could be resolved. Propagates to the job wrapper,
    which retries with backoff and records the job_run failure."""


class WeatherLocationResolver:
    """Resolves the Weather Location lazily on each call (ADR-0028): explicit
    config wins and is never cached; otherwise the cached row; otherwise
    derive it and cache it."""

    def __init__(
        self, configured: LocationSettings | None, mariadb: MariaDBClient
    ) -> None:
        self._configured = configured
        self._mariadb = mariadb
        self._geocoding = GeocodingClient()

    @property
    def is_configured(self) -> bool:
        """True when the location is explicit config rather than derived."""
        return self._configured is not None

    def resolve(self) -> LocationSettings:
        if self._configured is not None:
            return self._configured
        location = self._mariadb.read_weather_location()
        if location is None or location.source == "ip":
            location = self._derive_or_keep(location)
        return LocationSettings(
            latitude=location.latitude, longitude=location.longitude
        )

    def _derive_or_keep(self, cached: ResolvedLocation | None) -> ResolvedLocation:
        """Derives and caches the location. A cached IP-derived location is
        upgraded once the Account Postcode exists, and kept if postcodes.io
        cannot locate it."""
        if cached is None:
            location = self._derive()
        else:
            postcode_location = self._locate_postcode()
            if postcode_location is None:
                return cached
            location = postcode_location
        self._mariadb.write_weather_location(location)
        logger.info(
            f"Weather Location derived from {location.source}: "
            f"{location.latitude}, {location.longitude}."
        )
        return location

    def _locate_postcode(self) -> ResolvedLocation | None:
        postcode = self._mariadb.read_account_postcode()
        if postcode is None:
            return None
        try:
            latitude, longitude = self._geocoding.geocode_postcode(postcode)
        except Exception:
            logger.warning("postcodes.io could not locate the Account Postcode.")
            return None
        return ResolvedLocation(latitude, longitude, "postcode")

    def _derive(self) -> ResolvedLocation:
        postcode_location = self._locate_postcode()
        if postcode_location is not None:
            return postcode_location
        try:
            latitude, longitude = self._geocoding.geolocate_ip()
        except Exception as e:
            message = (
                "Could not resolve a Weather Location (no Account Postcode "
                "location and IP geolocation failed); will retry on the next run."
            )
            logger.warning(message, exc_info=True)
            raise WeatherLocationUnavailableError(message) from e
        return ResolvedLocation(latitude, longitude, "ip")
