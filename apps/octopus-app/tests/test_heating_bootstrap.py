import math
import random

import pytest

from octopus_app.data.heating_bootstrap import (
    bootstrap_threshold_upper,
)
from octopus_app.data.heating_model import THRESHOLDS

PLANTED_THRESHOLD = 12.0
SLOPE = 3.0


def _hockey_stick(noise: float, count: int = 200) -> list[tuple[float, float]]:
    """Warm days use no gas above the baseload; cold days use SLOPE kWh per
    degree below the planted threshold. Same temperatures for every noise."""
    rng = random.Random(7)
    days = []
    for _ in range(count):
        temperature = rng.uniform(-2.0, 24.0)
        wobble = rng.gauss(0.0, 1.0) * noise
        excess = SLOPE * max(0.0, PLANTED_THRESHOLD - temperature) + wobble
        days.append((temperature, excess))
    return days


def test_planted_hockey_stick_gives_an_upper_end_on_the_grid_near_the_planted_threshold() -> (
    None
):
    upper = bootstrap_threshold_upper(_hockey_stick(noise=1.0))

    assert upper in THRESHOLDS
    assert upper is not None
    assert 11.0 <= upper <= 15.0


def test_the_same_days_and_seed_always_give_the_same_upper_end() -> None:
    days = _hockey_stick(noise=2.0)

    assert bootstrap_threshold_upper(days, seed=1) == bootstrap_threshold_upper(
        days, seed=1
    )


def test_a_different_seed_still_gives_a_grid_value() -> None:
    upper = bootstrap_threshold_upper(_hockey_stick(noise=2.0), seed=99)

    assert upper in THRESHOLDS


def test_noisier_days_give_an_upper_end_at_least_as_high() -> None:
    clean = bootstrap_threshold_upper(_hockey_stick(noise=0.5))
    noisy = bootstrap_threshold_upper(_hockey_stick(noise=8.0))

    assert clean is not None
    assert noisy is not None
    assert noisy >= clean


def test_a_single_sample_gives_that_samples_best_threshold() -> None:
    # Index ceil(0.95 * 1) - 1 = 0: the only refitted threshold is the answer,
    # and a noiseless hockey stick refits to a threshold near the planted one
    # whichever days the resample draws.
    upper = bootstrap_threshold_upper(_hockey_stick(noise=0.0), samples=1)

    assert upper is not None
    assert 11.0 <= upper <= 13.0


def test_fewer_than_two_days_gives_no_upper_end() -> None:
    assert bootstrap_threshold_upper([]) is None
    assert bootstrap_threshold_upper([(5.0, 10.0)]) is None


def test_two_days_are_enough_to_score() -> None:
    assert bootstrap_threshold_upper([(5.0, 10.0), (6.0, 8.0)]) is not None


def test_all_warm_days_give_no_upper_end() -> None:
    days = [(25.0 + i * 0.1, 0.0) for i in range(50)]

    assert bootstrap_threshold_upper(days) is None


@pytest.mark.parametrize("samples", [0, -5])
def test_a_non_positive_sample_count_raises(samples: int) -> None:
    with pytest.raises(ValueError, match="samples"):
        bootstrap_threshold_upper(_hockey_stick(noise=1.0), samples=samples)


@pytest.mark.parametrize("bad", [math.nan, math.inf])
@pytest.mark.parametrize("position", [0, 1])
def test_non_finite_days_raise(bad: float, position: int) -> None:
    day = [5.0, 10.0]
    day[position] = bad

    with pytest.raises(ValueError, match="finite"):
        bootstrap_threshold_upper([(day[0], day[1]), (6.0, 8.0)])
