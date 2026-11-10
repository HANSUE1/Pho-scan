"""Local-time <-> POSIX timestamp helpers that also work for pre-1970 dates.

On Windows `datetime.timestamp()` and `datetime.fromtimestamp()` raise
OSError for anything before the epoch, which would silently drop the capture
date of old (scanned) photos. Both helpers fall back to plain arithmetic
against a naive epoch; the two fallbacks are inverses of each other, so a
value written by one is displayed correctly by the other.
"""

from datetime import datetime, timedelta

_EPOCH = datetime(1970, 1, 1)


def to_timestamp(dt: datetime) -> float:
    """Naive local datetime -> timestamp."""
    try:
        return dt.timestamp()
    except (OSError, OverflowError, ValueError):
        return (dt - _EPOCH).total_seconds()


def from_timestamp(ts: float) -> datetime | None:
    """Timestamp -> naive local datetime, or None if out of range."""
    try:
        return datetime.fromtimestamp(ts)
    except (OSError, OverflowError, ValueError):
        try:
            return _EPOCH + timedelta(seconds=ts)
        except OverflowError:
            return None


def format_timestamp(ts: float | None, fmt: str) -> str:
    """strftime() of a timestamp, or "" when missing or out of range."""
    if ts is None:
        return ""
    dt = from_timestamp(ts)
    return dt.strftime(fmt) if dt is not None else ""
