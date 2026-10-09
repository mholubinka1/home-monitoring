from enum import Enum

from octopus_app.data import local_day
from octopus_app.data.model import ConsumptionSummary


class GasDayStatus(Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNVERIFIED = "unverified"


def gas_day_status(summary: ConsumptionSummary) -> GasDayStatus:
    # An all-zero day is treated as missing data, not a genuinely zero day.
    if summary.total_kwh <= 0:
        return GasDayStatus.INCOMPLETE
    if summary.half_hour_count is None:
        return GasDayStatus.UNVERIFIED
    if summary.half_hour_count == local_day.expected_half_hour_count(summary.date):
        return GasDayStatus.COMPLETE
    return GasDayStatus.INCOMPLETE
