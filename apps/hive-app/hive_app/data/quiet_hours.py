from datetime import datetime

from common.local_day import get_local_timezone
from hive_app.common.config import QuietHours


def is_quiet(quiet_hours: QuietHours | None, now: datetime) -> bool:
    """Whether `now` falls in the quiet window (local time). Watchdogs send
    nothing then and keep their state, so what is due goes out on the first
    check after it ends."""
    if quiet_hours is None:
        return False
    local = now.astimezone(get_local_timezone()).time()
    start, end = quiet_hours.start, quiet_hours.end
    if start <= end:
        return start <= local < end
    return local >= start or local < end
