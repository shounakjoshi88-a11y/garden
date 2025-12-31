"""The rhythm of a working day.

A calendar where every single day has exactly the same number of commits is
its own kind of suspicious. Real work comes in clusters: a day with one tidy
commit, a day where you could not stop, a day you spent on something else
entirely.

This module decides, for a given date, what that day should look like. It is
deterministic - the same date always produces the same plan, so a backfill is
reproducible - but it varies from day to day in the way real activity does.

The shape comes from a weighted distribution rather than a random number, so
the result looks like a person: mostly small days, a long tail of big ones,
and the occasional day off.
"""

from __future__ import annotations

import datetime as dt
import hashlib

# (commits that day, weight). Tuned so the mean lands around 4-5 with a long
# tail: most days are small, a minority are noticeably bright.
DISTRIBUTION: list[tuple[int, int]] = [
    (0, 4), (1, 12), (2, 16), (3, 15), (4, 12), (5, 9), (6, 7),
    (7, 6), (8, 5), (9, 4), (10, 3), (11, 3), (12, 4),
]

# A quiet day usually means something else took the day. Two in a row is rare.
QUIET_WEIGHT = DISTRIBUTION[0][1]
TOTAL_WEIGHT = sum(weight for _count, weight in DISTRIBUTION)

# Plausible working hours for a student with classes and a part-time job.
WORK_START = 9
WORK_END = 23

DEFAULT_SALT = "garden-v1"


def _digest(day: dt.date, salt: str) -> bytes:
    return hashlib.sha256(f"{day.isoformat()}|{salt}".encode("utf-8")).digest()


def _pick(day: dt.date, salt: str) -> int:
    """Weighted pick from DISTRIBUTION."""
    roll = int.from_bytes(_digest(day, salt)[:4], "big") % TOTAL_WEIGHT
    cursor = 0
    for count, weight in DISTRIBUTION:
        cursor += weight
        if roll < cursor:
            return count
    return DISTRIBUTION[-1][0]


def _is_quiet(day: dt.date, salt: str) -> bool:
    """A quiet day, but never two in a row."""
    if _pick(day, salt) != 0:
        return False
    previous = day - dt.timedelta(days=1)
    return _pick(previous, salt) != 0


def target_for(day: dt.date, salt: str = DEFAULT_SALT) -> int:
    """How many commits this day should carry."""
    if _is_quiet(day, salt):
        return 0
    return max(1, _pick(day, salt))


def times_for(day: dt.date, count: int, salt: str = DEFAULT_SALT) -> list[tuple[int, int]]:
    """Clock times for the day's commits.

    Spread across the working day with a bias towards the evening, because
    that is when the work actually happens. Returns fewer pairs than `count`
    when the day is busy; the caller cycles through them.
    """
    if count <= 0:
        return []
    digest = _digest(day, salt + "|time")
    slots = max(1, min(count, 6))
    span = WORK_END - WORK_START
    times: list[tuple[int, int]] = []
    for index in range(slots):
        raw = int.from_bytes(digest[index * 4 : index * 4 + 4], "big")
        fraction = (raw % 1000) / 1000.0
        # Square the fraction to bias towards later hours.
        offset = int(span * (fraction ** 1.6))
        hour = WORK_START + offset
        minute = (raw // 1000) % 60
        times.append((hour, minute))
    times.sort()
    return times


def describe(day: dt.date, salt: str = DEFAULT_SALT) -> str:
    target = target_for(day, salt)
    if target == 0:
        return f"{day.isoformat()}: quiet day"
    return f"{day.isoformat()}: {target} commit(s)"
