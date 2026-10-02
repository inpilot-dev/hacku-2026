"""Server clock and Hong Kong calendar periods.

Server time decides expiry. Tests and the labelled demo scenario lab swap in
a ``FixedClock``; the normal run uses ``SystemClock``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Asia/Hong_Kong has had no DST since 1979, so a fixed offset is exact and
# avoids depending on tzdata being installed on the demo machines.
HKT = timezone(timedelta(hours=8), "Asia/Hong_Kong")


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc).replace(microsecond=0)


class FixedClock:
    def __init__(self, at: datetime):
        self.at = at.replace(microsecond=0)

    def now(self) -> datetime:
        return self.at

    def advance(self, **kwargs) -> None:
        self.at = self.at + timedelta(**kwargs)


def iso(dt: datetime) -> str:
    """RFC 3339 in +08:00 with second precision; lexicographically ordered."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(HKT).replace(microsecond=0).isoformat()


def parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("timestamp needs an explicit offset")
    return dt


def period_bounds(period: str, at: datetime) -> tuple[datetime, datetime]:
    """[start, end) of the HK calendar week (Monday 00:00) or month containing ``at``."""
    local = at.astimezone(HKT)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "calendar_week":
        start = midnight - timedelta(days=local.weekday())
        return start, start + timedelta(days=7)
    if period == "calendar_month":
        start = midnight.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start, end
    raise ValueError(f"unknown period {period!r}")
