"""Refreshes the heating model: refits it weekly and rewrites the day verdicts,
weekly rows and forecast from the stored (or newly fitted) model every run."""

import logging.config
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from logging import Logger, getLogger

from common import local_day
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import (
    heating_bootstrap,
    heating_forecast,
    heating_model,
    heating_verdict,
    heating_weeks,
    thermostat_check,
)
from octopus_app.data.gas_day import is_usable
from octopus_app.data.heating_model import BaselineKind, BaselinePoint, HeatingFit
from octopus_app.data.model import (
    ConsumptionSummary,
    Energy,
    HeatingDayVerdictRecord,
    HeatingForecastRecord,
    HeatingModelRecord,
    StoredHeatingModel,
)
from octopus_app.data.mysql.client import MariaDBClient

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

REFIT_AFTER_DAYS = 7  # a model this old or older is refitted
# How far back gas history is read: past the 1096-day window the consumption
# summary backfill covers, so it holds the whole history.
HISTORY_DAYS = 1500
FORECAST_DAYS = 7


def _require(value: float | None, name: str) -> float:
    if value is None:
        raise ValueError(f"a usable heating model is missing its {name}")
    return value


@dataclass(frozen=True)
class _RunInputs:
    """What one run reads: the usable gas days before today and the daily means."""

    today: date
    as_of: date
    summaries: list[ConsumptionSummary]
    gas: dict[date, float]
    means: dict[date, float]


@dataclass(frozen=True)
class _Derived:
    """What follows from the gas, the weather and a weight and threshold."""

    effective: dict[date, float]
    baseload: dict[date, BaselinePoint]
    away: frozenset[date]


@dataclass(frozen=True)
class _Params:
    """The numbers a day is judged and projected with."""

    threshold: float
    slope: float
    weight: float
    upper: float
    noise: heating_verdict.NoiseModel

    @classmethod
    def from_record(cls, record: HeatingModelRecord) -> "_Params":
        return cls(
            threshold=_require(record.threshold, "threshold"),
            slope=_require(record.slope, "slope"),
            weight=_require(record.weight, "weight"),
            upper=_require(record.upper_threshold, "upper threshold"),
            noise=heating_verdict.NoiseModel(
                _require(record.noise_base, "noise base"),
                _require(record.noise_per_degree_day, "noise slope"),
                _require(record.noise_floor, "noise floor"),
            ),
        )


def _usable_gas_summaries(
    mariadb: MariaDBClient, today: date
) -> list[ConsumptionSummary]:
    summaries = mariadb.read_daily_consumption_summary(
        Energy.gas, today - timedelta(days=HISTORY_DAYS), today
    )
    return [s for s in summaries if is_usable(s) and s.date < today]


def _load_inputs(mariadb: MariaDBClient, today: date) -> _RunInputs | None:
    summaries = _usable_gas_summaries(mariadb, today)
    if not summaries:
        return None
    gas = {s.date: float(s.total_kwh) for s in summaries}
    means = mariadb.read_complete_day_mean_temperatures(
        min(gas) - timedelta(days=1), today - timedelta(days=1)
    )
    return _RunInputs(today, max(gas), summaries, gas, means)


def _derive(inputs: _RunInputs, weight: float, threshold: float) -> _Derived:
    effective = heating_model.effective_temperatures(inputs.means, weight)
    baseload, away = heating_model.baseload_and_away_days(
        inputs.gas, effective, threshold
    )
    return _Derived(effective, baseload, away)


def _window_start(as_of: date) -> date:
    return as_of - timedelta(days=heating_model.FIT_WINDOW_DAYS - 1)


def _unusable_record(
    fit: HeatingFit, inputs: _RunInputs, now: datetime
) -> HeatingModelRecord:
    quality = fit.quality
    return HeatingModelRecord(
        fitted_at=now,
        window_start=_window_start(inputs.as_of),
        window_end=inputs.as_of,
        usable=False,
        unusable_reason=fit.reason.value if fit.reason else None,
        days_used=quality.days_used if quality else None,
        threshold=fit.threshold,
        upper_threshold=None,
        slope=fit.slope,
        weight=fit.weight,
        r_squared=quality.r_squared if quality else None,
        baseload=None,
        baseload_measured_days=None,
        baseload_interpolated_days=None,
        baseload_held_days=None,
        noise_base=None,
        noise_per_degree_day=None,
        noise_floor=None,
        thermostat_threshold=None,
        heating_days=None,
        non_heating_days=None,
        label=thermostat_check.ModelLabel.ESTIMATED.value,
    )


@dataclass(frozen=True)
class _Window:
    """The fit window's days that can be modelled: gas, an effective
    temperature, a baseload, and not away."""

    start: date
    days: list[date]


def _fit_window(inputs: _RunInputs, derived: _Derived) -> _Window:
    start = _window_start(inputs.as_of)
    days = [
        day
        for day in sorted(inputs.gas)
        if day >= start
        and day in derived.effective
        and day in derived.baseload
        and day not in derived.away
    ]
    return _Window(start, days)


def _baseload_kind_count(
    inputs: _RunInputs, derived: _Derived, window: _Window, kind: BaselineKind
) -> int:
    return sum(
        1
        for offset in range((inputs.as_of - window.start).days + 1)
        if (point := derived.baseload.get(window.start + timedelta(days=offset)))
        and point.kind is kind
    )


def _thermostat(
    mariadb: MariaDBClient, inputs: _RunInputs, derived: _Derived, window: _Window
) -> thermostat_check.ThermostatCheck | None:
    readings = mariadb.read_heating_readings(
        local_day.start_of_local_day(window.start),
        local_day.start_of_local_day(inputs.as_of + timedelta(days=1)),
    )
    return thermostat_check.thermostat_threshold(
        thermostat_check.classify_days(readings), derived.effective
    )


def _noise_and_upper(
    inputs: _RunInputs, derived: _Derived, window: _Window, fit: HeatingFit
) -> tuple[heating_verdict.NoiseModel, float]:
    threshold = _require(fit.threshold, "threshold")
    slope = _require(fit.slope, "slope")
    degree_days = {
        day: max(0.0, threshold - derived.effective[day]) for day in window.days
    }
    excess = {day: inputs.gas[day] - derived.baseload[day].kwh for day in window.days}
    noise = heating_verdict.fit_noise_model(
        [(degree_days[d], excess[d] - slope * degree_days[d]) for d in window.days]
    )
    upper = heating_bootstrap.bootstrap_threshold_upper(
        [(derived.effective[d], excess[d]) for d in window.days]
    )
    return noise, threshold if upper is None else max(upper, threshold)


def _usable_record(
    mariadb: MariaDBClient, inputs: _RunInputs, fit: HeatingFit, now: datetime
) -> HeatingModelRecord:
    quality = fit.quality
    threshold = _require(fit.threshold, "threshold")
    derived = _derive(inputs, _require(fit.weight, "weight"), threshold)
    if quality is None or inputs.as_of not in derived.baseload:
        raise ValueError("a usable fit has no quality or baseload")
    window = _fit_window(inputs, derived)
    noise, upper = _noise_and_upper(inputs, derived, window, fit)
    check = _thermostat(mariadb, inputs, derived, window)
    label = thermostat_check.label_model(threshold, check)
    return HeatingModelRecord(
        fitted_at=now,
        window_start=window.start,
        window_end=inputs.as_of,
        usable=True,
        unusable_reason=None,
        days_used=quality.days_used,
        threshold=threshold,
        upper_threshold=upper,
        slope=fit.slope,
        weight=fit.weight,
        r_squared=quality.r_squared,
        baseload=derived.baseload[inputs.as_of].kwh,
        baseload_measured_days=_baseload_kind_count(
            inputs, derived, window, BaselineKind.MEASURED
        ),
        baseload_interpolated_days=_baseload_kind_count(
            inputs, derived, window, BaselineKind.INTERPOLATED
        ),
        baseload_held_days=_baseload_kind_count(
            inputs, derived, window, BaselineKind.HELD
        ),
        noise_base=noise.base,
        noise_per_degree_day=noise.per_degree_day,
        noise_floor=noise.floor,
        thermostat_threshold=check.threshold if check else None,
        heating_days=check.heating_days if check else None,
        non_heating_days=check.non_heating_days if check else None,
        label=label.value,
    )


def _verdict_rows(
    inputs: _RunInputs, derived: _Derived, params: _Params
) -> list[HeatingDayVerdictRecord]:
    judging = heating_verdict.JudgingModel(
        params.threshold, params.slope, params.upper, params.noise
    )
    rows = []
    for day in sorted(inputs.gas):
        if day not in derived.effective or day not in derived.baseload:
            continue
        point = derived.baseload[day]
        verdict = heating_verdict.judge_day(
            gas=inputs.gas[day],
            effective=derived.effective[day],
            baseload=point.kwh,
            model=judging,
            away=day in derived.away,
        )
        rows.append(
            HeatingDayVerdictRecord(
                date=day,
                mean_temp=inputs.means.get(day),
                effective_temp=derived.effective[day],
                actual_gas=inputs.gas[day],
                expected_gas=verdict.expected_gas,
                baseload=point.kwh,
                baseload_kind=point.kind.value,
                needed=verdict.status is heating_verdict.DayStatus.NEEDED,
                away=verdict.status is heating_verdict.DayStatus.AWAY,
                expected_heating=verdict.expected_heating,
                normal_variation=verdict.normal_variation,
                possible=verdict.possible,
                clear=verdict.clear,
                margin=verdict.margin,
            )
        )
    return rows


def _forecast_rows(
    mariadb: MariaDBClient, inputs: _RunInputs, derived: _Derived, params: _Params
) -> list[HeatingForecastRecord]:
    # From yesterday: when yesterday's observed mean is missing, its forecast
    # stands in for it.
    forecast_means = mariadb.read_weather_forecast_mean_temps(
        inputs.today - timedelta(days=1),
        inputs.today + timedelta(days=FORECAST_DAYS - 1),
    )
    model = heating_forecast.ForecastModel(
        params.threshold,
        params.slope,
        params.weight,
        derived.baseload[max(derived.baseload)].kwh,
    )
    return [
        HeatingForecastRecord(
            day.date, day.forecast_mean, day.effective_temperature, day.expected_gas
        )
        for day in heating_forecast.project_expected_gas(
            forecast_means,
            inputs.means,
            heating_forecast.ForecastWindow(inputs.today, FORECAST_DAYS),
            model,
        )
    ]


def _judge_and_write(
    mariadb: MariaDBClient,
    inputs: _RunInputs,
    stored: StoredHeatingModel,
    now: datetime,
) -> bool:
    """Rewrites the verdicts, weeks and forecast from the stored model; False
    when nothing could be judged."""
    model_id = stored.id
    params = _Params.from_record(stored.model)
    derived = _derive(inputs, params.weight, params.threshold)
    if not derived.baseload:
        logger.warning(
            f"Heating model refresh: no baseload can be measured for model "
            f"{model_id}; nothing judged."
        )
        return False
    mariadb.replace_heating_day_verdicts(
        model_id, _verdict_rows(inputs, derived, params)
    )
    mariadb.replace_heating_weeks(
        model_id,
        heating_weeks.weekly_rows(
            inputs.gas,
            derived.effective,
            derived.baseload,
            derived.away,
            params.threshold,
        ),
    )
    forecast = _forecast_rows(mariadb, inputs, derived, params)
    if not forecast:
        # A forecast from days ago is worse than none, so the table is emptied.
        logger.warning(
            f"Heating model refresh: no usable weather forecast for model "
            f"{model_id}; the forecast table is emptied."
        )
    mariadb.replace_heating_forecast_days(model_id, forecast, now)
    return True


def _is_stale(latest: StoredHeatingModel | None, now: datetime) -> bool:
    """Whether the latest fit is REFIT_AFTER_DAYS or more local days old. Whole
    local days, not elapsed time: a run a few seconds earlier than last week's
    must not slip the refit to the next day."""
    if latest is None:
        return True
    age = local_day.to_local_date(now) - local_day.to_local_date(latest.model.fitted_at)
    return age >= timedelta(days=REFIT_AFTER_DAYS)


class HeatingModelJob:
    _mariadb: MariaDBClient

    def __init__(self, mariadb: MariaDBClient) -> None:
        self._mariadb = mariadb

    def refresh(self, now: datetime | None = None) -> None:
        """Refit when the latest fit is REFIT_AFTER_DAYS or more local days old,
        or when no usable model exists yet, then re-judge every day, the weeks
        and the forecast with the newest usable model. A refit that comes out
        unusable is recorded, and that run re-judges with the previous usable
        model."""
        if now is None:
            now = datetime.now(UTC)
        inputs = _load_inputs(self._mariadb, local_day.to_local_date(now))
        if inputs is None:
            logger.info("Heating model refresh: no usable gas days; nothing written.")
            return
        usable = self._mariadb.read_latest_usable_heating_model()
        latest = self._mariadb.read_latest_heating_model()
        refit = usable is None or _is_stale(latest, now)
        refitted = self._refit(inputs, now) if refit else None
        stored = refitted or usable
        if stored is None:
            logger.info("Heating model refresh: no usable model yet; nothing judged.")
            return
        if _judge_and_write(self._mariadb, inputs, stored, now):
            logger.info(
                f"Heating model refresh: {'refitted' if refitted else 'kept'} "
                f"model {stored.id}."
            )

    def _refit(self, inputs: _RunInputs, now: datetime) -> StoredHeatingModel | None:
        fit = heating_model.fit_heating_model(
            inputs.summaries, inputs.means, inputs.as_of
        )
        if not fit.usable:
            self._mariadb.write_heating_model(_unusable_record(fit, inputs, now))
            logger.info(
                f"Heating model refresh: unusable fit "
                f"({fit.reason.value if fit.reason else 'no reason'}); "
                "re-judging with the previous usable model, if any."
            )
            return None
        record = _usable_record(self._mariadb, inputs, fit, now)
        return StoredHeatingModel(self._mariadb.write_heating_model(record), record)

    def ready_for_first_fit(self, now: datetime | None = None) -> bool:
        if self._mariadb.read_latest_heating_model() is not None:
            return False
        today = local_day.to_local_date(now or datetime.now(UTC))
        usable = _usable_gas_summaries(self._mariadb, today)
        return len(usable) >= heating_model.MIN_USABLE_DAYS
