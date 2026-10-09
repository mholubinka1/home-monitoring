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


def location_key(latitude: float, longitude: float) -> str:
    """The Weather Location's key: coordinates to two decimals (about 1 km),
    e.g. "51.50,-0.10". Never derived from the postcode."""
    # "+ 0.0" turns a rounded -0.0 into 0.0 so the key never reads "-0.00".
    return f"{round(latitude, 2) + 0.0:.2f},{round(longitude, 2) + 0.0:.2f}"
