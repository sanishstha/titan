"""SEC submissions acceptance time.

EDGAR's index header records acceptance as an America/New_York wall time, for
example ``20190221163718`` on NVIDIA's 2019 10-K. The submissions JSON for that
same filing says ``2019-02-22T02:37:18.000Z``. The ``Z`` clock is one New York
offset later than the true UTC instant: ten hours ahead in EST and eight hours
ahead in EDT. Converting that published instant into America/New_York produces
the true UTC clock numbers.

``known_at`` is those numbers labeled UTC. ``eastern`` is that same instant in
America/New_York, which matches the index header. The original text is kept
unchanged, including trailing zeros in the fraction. ``precision`` is the
number of fractional digits in that text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from titan.ingest.errors import SecAcceptanceError

_EASTERN = ZoneInfo("America/New_York")
_ACCEPTANCE = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?P<fraction>\.[0-9]{1,6})?Z$"
)


@dataclass(frozen=True)
class AcceptanceTimestamp:
    """One SEC acceptance time, with the published text and the UTC instant."""

    original: str
    precision: int
    utc: datetime
    eastern: datetime


def parse_acceptance_datetime(value: object) -> AcceptanceTimestamp:
    """Convert one submissions ``acceptanceDateTime`` into UTC.

    The value must be ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``. A numeric offset is
    rejected so a real zone is not dropped. Fractional seconds are scaled to
    microseconds without rounding. More than six fractional digits are
    rejected because a ``timestamptz`` cannot store them exactly.
    """
    if not isinstance(value, str):
        raise SecAcceptanceError(
            "acceptanceDateTime must be YYYY-MM-DDTHH:MM:SS[.ffffff]Z"
        )
    match = _ACCEPTANCE.fullmatch(value)
    if match is None:
        raise SecAcceptanceError(
            "acceptanceDateTime must be YYYY-MM-DDTHH:MM:SS[.ffffff]Z"
        )
    microseconds, precision = _microseconds(match.group("fraction"))
    try:
        published = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            microseconds,
            tzinfo=UTC,
        )
    except ValueError as exc:
        raise SecAcceptanceError("acceptanceDateTime is not a calendar timestamp") from exc
    new_york_clock = published.astimezone(_EASTERN)
    utc = datetime(
        new_york_clock.year,
        new_york_clock.month,
        new_york_clock.day,
        new_york_clock.hour,
        new_york_clock.minute,
        new_york_clock.second,
        new_york_clock.microsecond,
        tzinfo=UTC,
    )
    eastern = utc.astimezone(_EASTERN)
    if eastern.utcoffset() is None or eastern.astimezone(UTC) != utc:
        raise SecAcceptanceError(
            "acceptanceDateTime could not be placed in America/New_York"
        )
    if utc.utcoffset() != timedelta(0):
        raise SecAcceptanceError("acceptanceDateTime did not convert to UTC")
    return AcceptanceTimestamp(
        original=value,
        precision=precision,
        utc=utc,
        eastern=eastern,
    )


def _microseconds(fraction: str | None) -> tuple[int, int]:
    if fraction is None:
        return 0, 0
    digits = fraction[1:]
    precision = len(digits)
    return int(digits) * (10 ** (6 - precision)), precision
