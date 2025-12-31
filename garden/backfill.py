"""Fill gaps in the contribution history.

For each missing day, garden re-runs the checks for that date and commits the
results with a backdated *author* date - the same field GitHub's contribution
calendar reads. The content is genuinely computed from your code; only the
timestamp moves.

This only ever adds new commits on top of the current branch. It never
rewrites or drops existing history.

The number of commits a day receives comes from the rhythm, not from a fixed
number, so the filled history has the same uneven texture as a real one.
"""

from __future__ import annotations

import datetime as dt
import os

from . import engine, gitutil, rhythm
from .config import Config

LIGHT_TASKS = ["metrics", "todos", "notes"]


def existing_counts(
    cfg: Config,
    start: dt.date,
    end: dt.date,
    *,
    workdir: str | None = None,
    token: str | None = None,
) -> dict[dt.date, int]:
    """How many commits each day already has, across every monitored repository."""
    counts: dict[dt.date, int] = {}
    paths: list[str] = []
    if cfg.ops_repo_path:
        paths.append(cfg.ops_repo_path)
    fleet = engine.resolve_fleet(cfg, workdir or ".garden-fleet", token)
    paths.extend(path for _t, path in fleet)

    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        if not gitutil.is_repo(path):
            continue
        for day, count in gitutil.commits_per_day(path, since=start).items():
            counts[day] = counts.get(day, 0) + count
    return counts


def days_to_fill(
    cfg: Config,
    start: dt.date,
    end: dt.date,
    *,
    workdir: str | None = None,
    token: str | None = None,
) -> list[tuple[dt.date, int]]:
    """Return [(day, commits_needed)] for the days that are below their target.

    Quiet days in the rhythm are left alone, and days that already carry
    enough commits are left alone.
    """
    salt = cfg.rhythm_salt or rhythm.DEFAULT_SALT
    counts = existing_counts(cfg, start, end, workdir=workdir, token=token)
    plan: list[tuple[dt.date, int]] = []
    cursor = start
    while cursor <= end:
        target = rhythm.target_for(cursor, salt)
        if target > 0:
            needed = target - counts.get(cursor, 0)
            if needed > 0:
                plan.append((cursor, needed))
        cursor += dt.timedelta(days=1)
    return plan


def missing_days(
    cfg: Config,
    start: dt.date,
    end: dt.date,
    *,
    workdir: str | None = None,
    token: str | None = None,
) -> tuple[list[dt.date], set[dt.date]]:
    """Backwards-compatible view: which days have no commits at all."""
    counts = existing_counts(cfg, start, end, workdir=workdir, token=token)
    missing: list[dt.date] = []
    covered: set[dt.date] = set()
    cursor = start
    while cursor <= end:
        if counts.get(cursor, 0) > 0:
            covered.add(cursor)
        else:
            missing.append(cursor)
        cursor += dt.timedelta(days=1)
    return missing, covered


def backfill(
    cfg: Config,
    start: dt.date,
    end: dt.date,
    *,
    tasks: list[str] | None = None,
    push: bool = False,
    dry_run: bool = False,
    token: str | None = None,
    workdir: str | None = None,
    cap: int | None = None,
    verbose: bool = True,
) -> list[engine.DayResult]:
    chosen = tasks or LIGHT_TASKS
    original_tasks = cfg.tasks
    original_fleet = [(t.name, list(t.tasks)) for t in cfg.fleet]
    cfg.tasks = list(chosen)
    for target in cfg.fleet:
        target.tasks = list(chosen)

    plan = days_to_fill(cfg, start, end, workdir=workdir, token=token)
    if verbose:
        print(f"{len(plan)} day(s) below target between {start} and {end}")

    results: list[engine.DayResult] = []
    for index, (day, needed) in enumerate(plan, 1):
        if verbose:
            print(f"  [{index}/{len(plan)}] {day.isoformat()} "
                  f"(+{needed}) ... ", end="", flush=True)
        result = engine.run_day(
            cfg, day, push=False, dry_run=dry_run, cap=cap or needed,
            token=token, workdir=workdir,
        )
        results.append(result)
        if verbose:
            print(f"{result.count} commit(s)" + (f"  errors: {result.errors}" if result.errors else ""))

    # One push for the whole batch, not one per day.
    if push and not dry_run:
        repos = []
        if cfg.ops_repo_path:
            repos.append(("hub", cfg.ops_repo_path))
        fleet = engine.resolve_fleet(cfg, workdir or ".garden-fleet", token)
        repos.extend((target.name, path) for target, path in fleet)
        for name, path in repos:
            if not (gitutil.is_repo(path) and gitutil.remote_url(path)):
                continue
            ok, message = gitutil.push_result(path)
            if verbose:
                print(f"  {'+' if ok else '~'} {name}: {'pushed' if ok else message}")

    cfg.tasks = original_tasks
    for target, saved in original_fleet:
        for t in cfg.fleet:
            if t.name == target:
                t.tasks = saved
    return results
