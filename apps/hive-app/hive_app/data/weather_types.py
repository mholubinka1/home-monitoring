import math
from typing import Annotated

from pydantic import AfterValidator

# Shared by the Open-Meteo and geocoding clients -- both are simple
# request/response HTTP calls with no long-running work, so one timeout
# value suits both rather than each picking its own.
REQUEST_TIMEOUT_SECONDS = 30


def _require_finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"expected a finite number, got {value!r}")
    return value


# Shared by the Open-Meteo and geocoding response models --
# a malformed upstream payload can carry a JSON NaN/Infinity literal (Python's
# json module accepts them), which would otherwise reach the weather_observation
# table's Float columns unchecked.
FiniteFloat = Annotated[float, AfterValidator(_require_finite)]
