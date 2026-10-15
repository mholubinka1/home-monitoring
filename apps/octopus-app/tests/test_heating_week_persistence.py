from datetime import date

from octopus_app.data.heating_weeks import WeekRow, WeekStatus
from octopus_app.data.mysql.client import MariaDBClient


def _shown(week_start: date, efficiency: float = 1.2) -> WeekRow:
    return WeekRow(
        week_start=week_start,
        complete_days=7,
        avg_gas=30.0,
        avg_degree_days=8.0,
        avg_baseload=8.0,
        avg_heating_gas=22.0,
        efficiency=efficiency,
        status=WeekStatus.SHOWN,
    )


def test_weeks_are_read_back_in_order_with_their_model(
    mariadb_client: MariaDBClient,
) -> None:
    earlier = _shown(date(2026, 10, 5))
    later = _shown(date(2026, 10, 12), efficiency=1.4)
    mariadb_client.replace_heating_weeks(4, [later, earlier])

    stored = mariadb_client.read_heating_weeks()

    assert stored == [(4, earlier), (4, later)]


def test_a_week_with_too_few_days_keeps_its_missing_averages(
    mariadb_client: MariaDBClient,
) -> None:
    sparse = WeekRow(
        week_start=date(2026, 10, 5),
        complete_days=0,
        avg_gas=None,
        avg_degree_days=None,
        avg_baseload=None,
        avg_heating_gas=None,
        efficiency=None,
        status=WeekStatus.TOO_FEW_DAYS,
    )
    mariadb_client.replace_heating_weeks(4, [sparse])

    assert mariadb_client.read_heating_weeks() == [(4, sparse)]


def test_every_week_status_survives_a_round_trip(
    mariadb_client: MariaDBClient,
) -> None:
    weeks = [
        WeekRow(date(2026, 1, 5 + 7 * index), 7, 1.0, 1.0, 1.0, 1.0, 1.0, status)
        for index, status in enumerate(WeekStatus)
    ]
    mariadb_client.replace_heating_weeks(4, weeks)

    stored = mariadb_client.read_heating_weeks()

    assert [week for _, week in stored] == weeks


def test_replacing_the_weeks_drops_weeks_no_longer_summarised(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.replace_heating_weeks(
        1, [_shown(date(2026, 10, 5)), _shown(date(2026, 10, 12))]
    )

    mariadb_client.replace_heating_weeks(2, [_shown(date(2026, 10, 12))])

    assert mariadb_client.read_heating_weeks() == [(2, _shown(date(2026, 10, 12)))]
