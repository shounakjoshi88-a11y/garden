"""Local view of the contribution graph.

Renders the same calendar GitHub draws, from your local clones, so you can see
the effect of a run before you push anything.
"""

from __future__ import annotations

import datetime as dt
import os
from collections import defaultdict

from . import engine, gitutil
from .config import Config

LEVELS = ["  ", "::", "::", "::", "::"]  # 0..4 shades, rendered with a ramp


def _shade(count: int) -> str:
    if count == 0:
        return " . "
    if count <= 2:
        return " o "
    if count <= 4:
        return " O "
    if count <= 6:
        return " 0 "
    return " # "


def collect_days(cfg: Config, since: dt.date, token: str | None = None, workdir: str | None = None):
    """day -> number of contributions, across every monitored repository."""
    per_day: dict[dt.date, int] = defaultdict(int)
    paths: list[str] = []
    if cfg.ops_repo_path:
        paths.append(cfg.ops_repo_path)
    fleet = engine.resolve_fleet(cfg, workdir or ".garden-fleet", token)
    paths.extend(path for _t, path in fleet)

    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue  # the same checkout listed twice is still one repository
        seen.add(key)
        if not gitutil.is_repo(path):
            continue
        for commit in gitutil.log(path, since=since):
            per_day[commit.date] += 1
    return per_day


def render(per_day: dict[dt.date, int], end: dt.date, weeks: int = 26) -> str:
    """Render a GitHub-style calendar ending on `end`."""
    lines: list[str] = []
    weekday_labels = {0: "Mon", 1: "Wed", 3: "Fri"}
    grid: list[list[tuple[dt.date, int]]] = []
    cursor = end
    while cursor.weekday() != 6:  # finish the week on Sunday
        cursor -= dt.timedelta(days=1)
    cursor -= dt.timedelta(days=7 * (weeks - 1))

    for _week in range(weeks):
        week: list[tuple[dt.date, int]] = []
        for offset in range(7):
            day = cursor + dt.timedelta(days=_week * 7 + offset)
            week.append((day, per_day.get(day, 0)))
        grid.append(week)

    for row in range(7):
        label = weekday_labels.get(row, "    ")
        row_cells = "".join(_shade(week[row][1]) for week in grid)
        lines.append(f"{label} {row_cells}")

    month_marks = ""
    for index, week in enumerate(grid):
        month = week[0][0]
        prev = grid[index - 1][0][0] if index else None
        if prev is None or month.month != prev.month:
            month_marks += " " * (index * 4) + month.strftime("%b")
    lines.append("     " + month_marks)
    return "\n".join(lines)


def summarise(per_day: dict[dt.date, int], end: dt.date, window: int = 90) -> dict:
    start = end - dt.timedelta(days=window - 1)
    active = [d for d in per_day if start <= d <= end]
    days = (end - start).days + 1
    streak = 0
    cursor = end
    while cursor >= start and per_day.get(cursor, 0) > 0:
        streak += 1
        cursor -= dt.timedelta(days=1)
    busiest = sorted(per_day.items(), key=lambda kv: -kv[1])[:5]
    return {
        "window_days": days,
        "active_days": len(active),
        "coverage_pct": round(100 * len(active) / days, 1) if days else 0.0,
        "current_streak": streak,
        "total_contributions": sum(per_day.values()),
        "busiest": busiest,
        "missing": [start + dt.timedelta(days=i) for i in range(days)
                    if per_day.get(start + dt.timedelta(days=i), 0) == 0],
    }
