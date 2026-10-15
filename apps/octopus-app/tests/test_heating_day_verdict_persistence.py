from datetime import date

from octopus_app.data.model import HeatingDayVerdictRecord
from octopus_app.data.mysql.client import MariaDBClient


def _verdict(
    day: date, needed: bool = True, away: bool = False
) -> HeatingDayVerdictRecord:
    return HeatingDayVerdictRecord(
        date=day,
        mean_temp=4.5,
        effective_temp=5.0,
        actual_gas=30.0,
        expected_gas=28.0,
        baseload=8.0,
        baseload_kind="measured",
        needed=needed,
        away=away,
        expected_heating=20.0,
        normal_variation=3.0,
        possible=1.0,
        clear=2.0,
        margin=0.5,
    )


def test_a_days_verdict_is_read_back_with_the_model_that_produced_it(
    mariadb_client: MariaDBClient,
) -> None:
    away_day = _verdict(date(2026, 10, 2), needed=False, away=True)
    ordinary_day = _verdict(date(2026, 10, 1))
    mariadb_client.replace_heating_day_verdicts(7, [away_day, ordinary_day])

    stored = mariadb_client.read_heating_day_verdicts(
        date(2026, 10, 1), date(2026, 10, 2)
    )

    assert stored == [(7, ordinary_day), (7, away_day)]


def test_a_day_with_no_temperature_reading_round_trips(
    mariadb_client: MariaDBClient,
) -> None:
    day = HeatingDayVerdictRecord(
        date=date(2026, 10, 1),
        mean_temp=None,
        effective_temp=None,
        actual_gas=30.0,
        expected_gas=28.0,
        baseload=8.0,
        baseload_kind="held",
        needed=False,
        away=False,
        expected_heating=20.0,
        normal_variation=3.0,
        possible=1.0,
        clear=2.0,
        margin=0.5,
    )
    mariadb_client.replace_heating_day_verdicts(1, [day])

    stored = mariadb_client.read_heating_day_verdicts(
        date(2026, 10, 1), date(2026, 10, 1)
    )

    assert stored == [(1, day)]


def test_replacing_the_verdicts_drops_days_no_longer_judged(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.replace_heating_day_verdicts(
        1, [_verdict(date(2026, 10, 1)), _verdict(date(2026, 10, 2))]
    )

    mariadb_client.replace_heating_day_verdicts(
        2, [_verdict(date(2026, 10, 2)), _verdict(date(2026, 10, 3))]
    )

    stored = mariadb_client.read_heating_day_verdicts(
        date(2026, 10, 1), date(2026, 10, 31)
    )
    assert [(model_id, record.date) for model_id, record in stored] == [
        (2, date(2026, 10, 2)),
        (2, date(2026, 10, 3)),
    ]


def test_replacing_the_verdicts_with_none_empties_the_table(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.replace_heating_day_verdicts(1, [_verdict(date(2026, 10, 1))])

    mariadb_client.replace_heating_day_verdicts(2, [])

    assert (
        mariadb_client.read_heating_day_verdicts(date(2026, 1, 1), date(2026, 12, 31))
        == []
    )


def test_verdicts_are_read_only_within_the_inclusive_date_range(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.replace_heating_day_verdicts(
        1, [_verdict(date(2026, 10, day)) for day in range(1, 6)]
    )

    stored = mariadb_client.read_heating_day_verdicts(
        date(2026, 10, 2), date(2026, 10, 4)
    )

    assert [record.date for _, record in stored] == [
        date(2026, 10, 2),
        date(2026, 10, 3),
        date(2026, 10, 4),
    ]
