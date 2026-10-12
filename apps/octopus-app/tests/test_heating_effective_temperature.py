from datetime import date

import pytest

from octopus_app.data.heating_model import effective_temperature, effective_temperatures


def test_with_weight_zero_the_effective_temperature_is_todays_mean() -> None:
    assert effective_temperature(10.0, 2.0, 0.0) == 10.0


def test_with_weight_one_half_today_and_yesterday_count_equally() -> None:
    assert effective_temperature(10.0, 2.0, 0.5) == 6.0


def test_with_weight_point_eight_yesterday_dominates() -> None:
    # 0.2 * 10 + 0.8 * 2 = 3.6
    assert effective_temperature(10.0, 2.0, 0.8) == pytest.approx(3.6)


def test_a_missing_mean_for_either_day_gives_no_effective_temperature() -> None:
    assert effective_temperature(None, 2.0, 0.5) is None
    assert effective_temperature(10.0, None, 0.5) is None


def test_a_range_gives_each_day_with_a_previous_day_and_omits_the_rest() -> None:
    means = {date(2026, 1, 5): 4.0, date(2026, 1, 6): 8.0, date(2026, 1, 8): 6.0}

    result = effective_temperatures(means, 0.5)

    # The 5th has no 4th; the 8th has no 7th.
    assert result == {date(2026, 1, 6): 6.0}
