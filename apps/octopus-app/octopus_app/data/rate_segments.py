from datetime import datetime
from itertools import pairwise

from octopus_app.data.octopus.model import Rate

RateSegment = tuple[datetime, datetime, Rate]


def _day_overlap(
    rate: Rate, day_start: datetime, day_end: datetime
) -> tuple[datetime, datetime] | None:
    overlap_start = max(rate.valid_from, day_start)
    overlap_end = min(rate.valid_to or day_end, day_end)
    if overlap_end <= overlap_start:
        return None
    return overlap_start, overlap_end


def day_segments(
    rates: list[Rate], day_start: datetime, day_end: datetime
) -> list[RateSegment]:
    # Partitions [day_start, day_end) into non-overlapping segments, each
    # assigned a single winning rate -- the one with the latest
    # valid_from among every rate whose (day-clipped) window fully
    # spans that segment ("most-recently-started wins", the same
    # tiebreak read_current_product_rate already uses for its own
    # overlapping-row lookup). A stretch no rate's window fully spans
    # produces no segment at all, surfacing as a gap for the callers'
    # coverage checks to catch (segments_fully_cover_day, or the
    # midday-only check for a standing-charge-only day).
    clipped = [
        (overlap[0], overlap[1], rate)
        for rate in rates
        if (overlap := _day_overlap(rate, day_start, day_end)) is not None
    ]
    boundaries = sorted({day_start, day_end, *(b for c in clipped for b in c[:2])})
    segments: list[RateSegment] = []
    for segment_start, segment_end in pairwise(boundaries):
        covering = [c for c in clipped if c[0] <= segment_start and segment_end <= c[1]]
        if not covering:
            continue
        winner = max(covering, key=lambda c: c[2].valid_from)
        segments.append((segment_start, segment_end, winner[2]))
    return segments


def segments_fully_cover_day(
    segments: list[RateSegment],
    day_start: datetime,
    day_end: datetime,
) -> bool:
    if not segments or segments[0][0] != day_start or segments[-1][1] != day_end:
        return False
    return all(segments[i][1] == segments[i + 1][0] for i in range(len(segments) - 1))
