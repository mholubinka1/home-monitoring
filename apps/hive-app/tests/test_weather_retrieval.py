from datetime import UTC, date, datetime

import pytest

from hive_app.data.model import WeatherForecastDay, WeatherObservation
from hive_app.data.weather import WeatherRetriever


def _observation(source: str) -> WeatherObservation:
    return WeatherObservation(
        source=source,
        observed_at=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
        temp=14.5,
        humidity=72,
        pressure=1012.3,
        wind_speed=8.1,
        precipitation=0.0,
    )


class _FakeWeatherSourceObservationSucceeds:
    """A fake WeatherSource whose observation fetch succeeds -- proves
    WeatherRetriever.refresh() persists exactly what was fetched."""

    def __init__(self) -> None:
        self.persisted: WeatherObservation | None = None

    def fetch_current_observation(self) -> WeatherObservation:
        return _observation("open-meteo")

    def persist_current_observation(self, observation: WeatherObservation) -> None:
        self.persisted = observation

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        raise NotImplementedError

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        raise NotImplementedError


def test_refresh_persists_the_fetched_observation() -> None:
    source = _FakeWeatherSourceObservationSucceeds()

    WeatherRetriever(source).refresh()

    assert source.persisted is not None
    assert source.persisted.source == "open-meteo"


class _FakeWeatherSourceObservationFails:
    """A fake WeatherSource whose observation fetch raises -- proves
    WeatherRetriever.refresh() does no retry/backoff/swallowing of its own
    (that's the generic job-wrapper's job); it just propagates."""

    def fetch_current_observation(self) -> WeatherObservation:
        raise ConnectionError("api.open-meteo.com unreachable")

    def persist_current_observation(self, observation: WeatherObservation) -> None:
        raise AssertionError("persist_current_observation should never be reached")

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        raise NotImplementedError

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        raise NotImplementedError


def test_refresh_propagates_when_the_observation_fetch_fails() -> None:
    source = _FakeWeatherSourceObservationFails()

    with pytest.raises(ConnectionError, match="api.open-meteo.com unreachable"):
        WeatherRetriever(source).refresh()


def _forecast_day(target_date: str) -> WeatherForecastDay:
    return WeatherForecastDay(
        source="open-meteo",
        target_date=date.fromisoformat(target_date),
        max_temp=12.3,
        fetched_at=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
    )


class _FakeWeatherSourceForecastSucceeds:
    """A fake WeatherSource whose forecast fetch succeeds -- proves
    WeatherRetriever.refresh_forecast() persists exactly what fetch_forecast()
    returned, with no transformation or filtering along the way."""

    def __init__(self, forecast: list[WeatherForecastDay]) -> None:
        self._forecast = forecast
        self.persisted: list[WeatherForecastDay] | None = None

    def fetch_current_observation(self) -> WeatherObservation:
        raise NotImplementedError

    def persist_current_observation(self, observation: WeatherObservation) -> None:
        raise NotImplementedError

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        return self._forecast

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        self.persisted = forecast


def test_refresh_forecast_persists_the_fetched_forecast() -> None:
    forecast = [_forecast_day("2026-09-26"), _forecast_day("2026-09-27")]
    source = _FakeWeatherSourceForecastSucceeds(forecast)

    WeatherRetriever(source).refresh_forecast()

    assert source.persisted == forecast


class _FakeWeatherSourceForecastFails:
    """A fake WeatherSource whose forecast fetch raises -- proves
    WeatherRetriever.refresh_forecast() has no try/except of its own,
    just propagates
    straight to the generic job wrapper."""

    def fetch_current_observation(self) -> WeatherObservation:
        raise NotImplementedError

    def persist_current_observation(self, observation: WeatherObservation) -> None:
        raise NotImplementedError

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        raise ConnectionError("api.open-meteo.com unreachable")

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        raise AssertionError("persist_forecast should never be reached")


def test_refresh_forecast_propagates_when_the_forecast_fetch_fails() -> None:
    source = _FakeWeatherSourceForecastFails()

    with pytest.raises(ConnectionError, match="api.open-meteo.com unreachable"):
        WeatherRetriever(source).refresh_forecast()
