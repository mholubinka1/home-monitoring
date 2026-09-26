from datetime import date, datetime

from octopus_app.common.config import ApplicationSettings
from octopus_app.data.model import (
    Consumption,
    ConsumptionSummary,
    CostForecast,
    DailyCostSummary,
    Energy,
)
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.octopus.agile_predict import AgilePredictClient
from octopus_app.data.octopus.api import OctopusEnergyAPIClient
from octopus_app.data.octopus.kraken import BillingPeriodClient, KrakenTransport
from octopus_app.data.octopus.model import (
    Account,
    AgileForecastReading,
    Agreement,
    BillingPeriod,
    Meter,
    Product,
    Rate,
)
from octopus_app.data.octopus.x2r import X2rClient


class MonitoringClient:
    octopus: OctopusEnergyAPIClient
    mariadb: MariaDBClient
    _billing_period: BillingPeriodClient
    _agile_predict: AgilePredictClient
    _x2r: X2rClient

    account: Account
    meters: list[Meter]
    region_code: str

    def __init__(self, settings: ApplicationSettings) -> None:
        self.octopus = OctopusEnergyAPIClient(settings.octopus)
        self.mariadb = MariaDBClient(settings.mariadb)
        self._billing_period = BillingPeriodClient(settings.octopus, KrakenTransport())
        self._agile_predict = AgilePredictClient()
        self._x2r = X2rClient()

        account, meters = self.octopus.get_account_meter_information()
        self.account = account
        self.meters = meters

        self.region_code = self.octopus.get_region_code(self.account.postcode)

    def refresh_meters(
        self,
    ) -> None:
        _, meters = self.octopus.get_account_meter_information()
        self.meters = meters

    def fetch_consumption(
        self, meter: Meter, period_from: datetime
    ) -> tuple[str | None, list[Consumption]]:
        return self.octopus.get_consumption(meter, period_from)

    def fetch_consumption_page(
        self, energy: Energy, next_page: str
    ) -> tuple[str | None, list[Consumption]]:
        return self.octopus.get_consumption_directly_from_endpoint(energy, next_page)

    def persist_consumption(self, meter: Meter, consumption: list[Consumption]) -> None:
        self.mariadb.write_consumption(meter, consumption)

    def persist_consumption_summary(self, summaries: list[ConsumptionSummary]) -> None:
        self.mariadb.write_consumption_summary(summaries)

    def persist_agreement(self, meter: Meter, agreements: list[Agreement]) -> None:
        self.mariadb.write_agreement(meter, agreements)

    def fetch_products(self) -> list[Product]:
        return self.octopus.get_products()

    def is_product_available_in_region(self, product_code: str, region: str) -> bool:
        return self.octopus.get_product_region_availability(product_code, region)

    def persist_product(self, product: Product) -> None:
        self.mariadb.write_product(product)

    def fetch_electricity_rates(
        self,
        product_code: str,
        tariff_code: str,
        period_from: datetime | None,
        period_to: datetime | None,
    ) -> list[Rate]:
        return self.octopus.get_electricity_rates(
            product_code, tariff_code, period_from, period_to
        )

    def fetch_gas_rates(
        self,
        product_code: str,
        tariff_code: str,
        period_from: datetime | None,
        period_to: datetime | None,
    ) -> list[Rate]:
        return self.octopus.get_gas_rates(
            product_code, tariff_code, period_from, period_to
        )

    def persist_rate(self, product_code: str, region: str, rates: list[Rate]) -> None:
        self.mariadb.write_product_rate(product_code, region, rates)

    def fetch_electricity_tariff_code(
        self, product_code: str, region: str
    ) -> str | None:
        return self.octopus.get_electricity_tariff_code(product_code, region)

    def get_current_billing_period(self) -> BillingPeriod:
        return self._billing_period.get_current_billing_period()

    def fetch_agile_forecast(self, region: str) -> list[AgileForecastReading]:
        return self._agile_predict.get_forecast(region)

    def fetch_agile_forecast_fallback(self, region: str) -> list[AgileForecastReading]:
        return self._x2r.get_forecast(region)

    def persist_agile_forecast(
        self, region: str, readings: list[AgileForecastReading], fetched_at: datetime
    ) -> None:
        self.mariadb.write_agile_forecast(region, readings, fetched_at)

    def read_agile_forecast(
        self, region: str, as_of: datetime
    ) -> list[AgileForecastReading]:
        return self.mariadb.read_agile_forecast(region, as_of)

    def read_elapsed_billing_period_costs(
        self, period_from: datetime, period_to: datetime, region: str, energy: Energy
    ) -> list[DailyCostSummary]:
        return self.mariadb.read_elapsed_billing_period_costs(
            period_from, period_to, region, energy
        )

    def read_current_product_rate(
        self, product_code: str, region: str, as_of: datetime
    ) -> Rate | None:
        return self.mariadb.read_current_product_rate(product_code, region, as_of)

    def read_daily_consumption_summary(
        self, energy: Energy, start_date: date, end_date: date
    ) -> list[ConsumptionSummary]:
        return self.mariadb.read_daily_consumption_summary(energy, start_date, end_date)

    def read_weather_observation_daily_max_temps(
        self, start_date: date, end_date: date
    ) -> dict[date, float]:
        return self.mariadb.read_weather_observation_daily_max_temps(
            start_date, end_date
        )

    def read_weather_forecast_max_temps(
        self, start_date: date, end_date: date
    ) -> dict[date, float]:
        return self.mariadb.read_weather_forecast_max_temps(start_date, end_date)

    def persist_cost_forecast(self, forecast: CostForecast) -> None:
        self.mariadb.write_cost_forecast(forecast)
