import logging
from datetime import UTC, date, datetime

import pytest

from hive_app.data.model import (
    WeatherForecastDay,
    WeatherForecastHour,
    WeatherObservation,
)
from hive_app.data.weather import WeatherRetriever


def _observation(source: str, hour: int = 12) -> WeatherObservation:
    return WeatherObservation(
        source=source,
        location="51.50,-0.10",
        observed_at=datetime(2026, 9, 25, hour, 0, tzinfo=UTC),
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
        self.fetched = [_observation("open-meteo", hour) for hour in (10, 11, 12)]
        self.persisted: list[WeatherObservation] | None = None

    def fetch_recent_observations(self) -> list[WeatherObservation]:
        return self.fetched

    def persist_observations(self, observations: list[WeatherObservation]) -> None:
        self.persisted = observations

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        raise NotImplementedError

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        raise NotImplementedError

    def fetch_hourly_forecast(self) -> list[WeatherForecastHour]:
        raise NotImplementedError

    def persist_hourly_forecast(self, hours: list[WeatherForecastHour]) -> None:
        raise NotImplementedError


def test_refresh_persists_every_fetched_hour() -> None:
    source = _FakeWeatherSourceObservationSucceeds()

    WeatherRetriever(source).refresh()

    assert source.persisted == source.fetched


def test_a_refresh_that_finds_no_hours_to_store_says_so_in_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = _FakeWeatherSourceObservationSucceeds()
    source.fetched = []

    with caplog.at_level(logging.WARNING):
        WeatherRetriever(source).refresh()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "no hours" in warnings[0].getMessage()


class _FakeWeatherSourceObservationFails:
    """A fake WeatherSource whose observation fetch raises -- proves
    WeatherRetriever.refresh() does no retry/backoff/swallowing of its own
    (that's the generic job-wrapper's job); it just propagates."""

    def fetch_recent_observations(self) -> list[WeatherObservation]:
        raise ConnectionError("api.open-meteo.com unreachable")

    def persist_observations(self, observations: list[WeatherObservation]) -> None:
        raise AssertionError("persist_observations should never be reached")

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        raise NotImplementedError

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        raise NotImplementedError

    def fetch_hourly_forecast(self) -> list[WeatherForecastHour]:
        raise NotImplementedError

    def persist_hourly_forecast(self, hours: list[WeatherForecastHour]) -> None:
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

    def __init__(
        self,
        forecast: list[WeatherForecastDay],
        hours: list[WeatherForecastHour] | None = None,
        hourly_error: Exception | None = None,
    ) -> None:
        self._forecast = forecast
        self._hours = hours or []
        self._hourly_error = hourly_error
        self.persisted: list[WeatherForecastDay] | None = None
        self.persisted_hours: list[WeatherForecastHour] | None = None
        self.persist_order: list[str] = []

    def fetch_recent_observations(self) -> list[WeatherObservation]:
        raise NotImplementedError

    def persist_observations(self, observations: list[WeatherObservation]) -> None:
        raise NotImplementedError

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        return self._forecast

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        self.persisted = forecast
        self.persist_order.append("daily")

    def fetch_hourly_forecast(self) -> list[WeatherForecastHour]:
        if self._hourly_error is not None:
            raise self._hourly_error
        return self._hours

    def persist_hourly_forecast(self, hours: list[WeatherForecastHour]) -> None:
        self.persisted_hours = hours
        self.persist_order.append("hourly")


def test_refresh_forecast_persists_the_fetched_forecast() -> None:
    forecast = [_forecast_day("2026-09-26"), _forecast_day("2026-09-27")]
    source = _FakeWeatherSourceForecastSucceeds(forecast)

    WeatherRetriever(source).refresh_forecast()

    assert source.persisted == forecast


def test_refresh_forecast_persists_the_daily_forecast_and_then_the_hourly_one() -> None:
    hours = [
        WeatherForecastHour(
            source="open-meteo",
            location="51.50,-0.10",
            forecast_at=datetime(2026, 9, 25, 12, 0),
            temp=14.5,
            shortwave_radiation=300.0,
            fetched_at=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
        )
    ]
    source = _FakeWeatherSourceForecastSucceeds([_forecast_day("2026-09-26")], hours)

    WeatherRetriever(source).refresh_forecast()

    assert source.persist_order == ["daily", "hourly"]
    assert source.persisted_hours == hours


def test_a_failing_hourly_fetch_still_persists_the_daily_forecast_and_fails_the_refresh() -> (
    None
):
    forecast = [_forecast_day("2026-09-26")]
    source = _FakeWeatherSourceForecastSucceeds(
        forecast, hourly_error=ConnectionError("api.open-meteo.com unreachable")
    )

    with pytest.raises(ConnectionError, match="api.open-meteo.com unreachable"):
        WeatherRetriever(source).refresh_forecast()

    assert source.persisted == forecast


class _FakeWeatherSourceForecastFails:
    """A fake WeatherSource whose forecast fetch raises -- proves
    WeatherRetriever.refresh_forecast() has no try/except of its own,
    just propagates
    straight to the generic job wrapper."""

    def fetch_recent_observations(self) -> list[WeatherObservation]:
        raise NotImplementedError

    def persist_observations(self, observations: list[WeatherObservation]) -> None:
        raise NotImplementedError

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        raise ConnectionError("api.open-meteo.com unreachable")

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        raise AssertionError("persist_forecast should never be reached")

    def fetch_hourly_forecast(self) -> list[WeatherForecastHour]:
        raise NotImplementedError

    def persist_hourly_forecast(self, hours: list[WeatherForecastHour]) -> None:
        raise NotImplementedError


def test_refresh_forecast_propagates_when_the_forecast_fetch_fails() -> None:
    source = _FakeWeatherSourceForecastFails()

    with pytest.raises(ConnectionError, match="api.open-meteo.com unreachable"):
        WeatherRetriever(source).refresh_forecast()
