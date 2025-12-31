"""Command line interface for garden."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

from . import backfill as backfill_mod
from . import engine, gitutil, split as split_mod, status as status_mod
from .config import Config, RepoTarget, load, save
from .tasks import available

DEFAULT_CONFIG = "garden.config.json"


def _parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a date: {value}") from exc


def _load(args) -> Config:
    return load(getattr(args, "config", None))


def _token() -> str | None:
    return os.environ.get("GARDEN_TOKEN") or os.environ.get("GITHUB_TOKEN")


# --------------------------------------------------------------------------- init


def cmd_init(args) -> int:
    cfg = _load(args)
    hub = args.ops_repo or cfg.ops_repo_path or os.path.join(os.getcwd(), "garden-ops")
    hub = os.path.abspath(hub)
    cfg.ops_repo_path = hub
    if args.fleet_repo:
        cfg.ops_repo_clone = args.fleet_repo
    if args.author_name:
        cfg.author_name = args.author_name
    if args.author_email:
        cfg.author_email = args.author_email
    engine.init_ops_repo(
        hub,
        clone=cfg.ops_repo_clone,
        author_name=cfg.author_name,
        author_email=cfg.author_email,
    )

    for entry in args.repo or []:
        name = entry.split("/")[-1]
        cfg.fleet.append(
            RepoTarget(name=name, path=None, clone=entry, tasks=list(args.tasks or cfg.tasks))
        )

    if args.tasks:
        cfg.tasks = list(args.tasks)
    if args.sources:
        cfg.sources = [os.path.abspath(s) for s in args.sources]

    path = os.path.abspath(args.config or os.path.join(os.getcwd(), DEFAULT_CONFIG))
    save(cfg, path)

    print(f"ops repository : {hub}")
    print(f"config         : {path}")
    print(f"fleet          : {len(cfg.fleet)} repo(s)")
    print(f"tasks          : {', '.join(cfg.tasks)}")
    print()
    print("Next:")
    print(f"  python -m garden day --dry-run     # see what a day would produce")
    print(f"  python -m garden status            # preview the graph")
    return 0


# --------------------------------------------------------------------------- day


def cmd_day(args) -> int:
    cfg = _load(args)
    when = args.date or dt.date.today()
    result = engine.run_day(
        cfg,
        when,
        repos=args.repo,
        push=args.push,
        dry_run=args.dry_run,
        cap=args.cap,
        token=_token(),
        workdir=args.workdir,
        hub_only=args.hub_only,
    )
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}garden run for {when.isoformat()}")
    for record in result.commits:
        sha = record.sha[:7] if record.sha else "-------"
        print(f"  {sha}  {record.repo:<12} {record.message}")
    print(f"{result.count} commit(s)")
    for warning in result.warnings:
        print(f"  ~ {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"  ! {error}", file=sys.stderr)
    return 0 if not result.errors else 1


# --------------------------------------------------------------------------- split


def cmd_split(args) -> int:
    target = os.path.abspath(args.path or os.getcwd())
    if not os.path.isdir(target):
        print(f"not a directory: {target}", file=sys.stderr)
        return 2
    result = split_mod.split_command(
        target,
        max_per_commit=args.max_per_commit,
        when=args.date,
        dry_run=args.dry_run,
        push=args.push,
    )
    if args.dry_run:
        print(split_mod.format_plan(result))
    else:
        print(f"{result.count} commit(s) created in {target}")
        for subject, _kind, files in result.commits:
            print(f"  {subject}  ({len(files)} file(s))")
    return 0


# --------------------------------------------------------------------------- backfill


def cmd_backfill(args) -> int:
    cfg = _load(args)
    end = args.to or dt.date.today()
    start = args.frm or (end - dt.timedelta(days=args.days))
    results = backfill_mod.backfill(
        cfg,
        start,
        end,
        tasks=args.tasks,
        push=args.push,
        dry_run=args.dry_run,
        token=_token(),
        workdir=args.workdir,
        cap=args.cap,
    )
    total = sum(r.count for r in results)
    print(f"backfill complete: {total} commit(s) across {len(results)} day(s)")
    for result in results:
        for error in result.errors:
            print(f"  ! {result.date}: {error}", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- push-fleet


def cmd_push_fleet(args) -> int:
    cfg = _load(args)
    token = _token()
    pushed = 0
    repos: list[tuple[str, str]] = []
    if cfg.ops_repo_path and gitutil.is_repo(cfg.ops_repo_path):
        repos.append(("hub", cfg.ops_repo_path))
    fleet = engine.resolve_fleet(
        cfg, args.workdir or os.path.join(os.path.dirname(cfg.ops_repo_path or "."), "_fleet"), token
    )
    for target, path in fleet:
        repos.append((target.name, path))

    for name, path in repos:
        if not gitutil.remote_url(path):
            print(f"  - {name}: no remote, skipped")
            continue
        ok, message = gitutil.push_result(path)
        if ok:
            print(f"  + {name}: pushed")
            pushed += 1
        else:
            denied = "denied" in message.lower() or "permission" in message.lower() \
                or "403" in message
            print(f"  ~ {name}: not pushed{'' if not denied else ' (no write access)'}")
            if denied:
                print("      add a GARDEN_PAT secret with write access to this repository")
            elif message:
                print(f"      {message}")
    print(f"{pushed} repo(s) pushed")
    return 0


# --------------------------------------------------------------------------- request-backfill


def cmd_request_backfill(args) -> int:
    """Write a request file that the workflow picks up on the next push."""
    start = args.frm or (dt.date.today() - dt.timedelta(days=args.days))
    end = args.to or dt.date.today()
    root = os.path.abspath(args.path or os.getcwd())
    folder = os.path.join(root, ".garden")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, "request.json")
    payload = {
        "mode": "rebuild" if args.rebuild else "backfill",
        "from": start.isoformat(),
        "to": end.isoformat(),
    }
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(f"wrote {target} - push it and the workflow will {payload['mode']} "
          f"{start} .. {end}")
    return 0


# --------------------------------------------------------------------------- rebuild


def cmd_rebuild(args) -> int:
    """Rebuild the ops repository history so every day has its planned shape."""
    from . import rebuild as rebuild_mod

    cfg = _load(args)
    end = args.to or dt.date.today()
    start = args.frm or (end - dt.timedelta(days=args.days - 1))
    result = rebuild_mod.rebuild(
        cfg,
        start,
        end,
        source=args.source,
        push=args.push,
        dry_run=args.dry_run,
        token=_token(),
        workdir=args.workdir,
    )
    print(f"rebuilt {result['commits']} commit(s), {result['quiet_days']} quiet day(s)")
    return 0


# --------------------------------------------------------------------------- rhythm


def cmd_rhythm(args) -> int:
    """Show the activity pattern the rhythm plans for a range of days."""
    from . import rhythm

    salt = args.salt or (_load(args).rhythm_salt or rhythm.DEFAULT_SALT)
    end = args.to or dt.date.today()
    start = args.frm or (end - dt.timedelta(days=args.days - 1))
    cursor = start
    counts: list[int] = []
    days: list[dt.date] = []
    while cursor <= end:
        counts.append(rhythm.target_for(cursor, salt))
        days.append(cursor)
        cursor += dt.timedelta(days=1)

    if not args.quiet:
        for day, count in zip(days, counts):
            bar = "#" * count
            label = "quiet" if count == 0 else f"{count:>2}  {bar}"
            print(f"  {day.isoformat()}  {label}")
        print()

    active = [c for c in counts if c > 0]
    histogram: dict[int, int] = {}
    for count in counts:
        histogram[count] = histogram.get(count, 0) + 1
    print(f"days            : {len(counts)}")
    print(f"active days     : {len(active)} ({round(100 * len(active) / max(1, len(counts)))}%)")
    print(f"mean per day    : {round(sum(active) / max(1, len(active)), 2)}")
    print(f"quiet days      : {histogram.get(0, 0)}")
    print(f"busiest         : {max(counts) if counts else 0} commit(s)")
    print("distribution    : " + ", ".join(
        f"{count}x{times}" for count, times in sorted(histogram.items())))
    return 0


# --------------------------------------------------------------------------- status


def cmd_status(args) -> int:
    cfg = _load(args)
    end = dt.date.today()
    since = end - dt.timedelta(days=args.weeks * 7 + 7)
    per_day = status_mod.collect_days(cfg, since, token=_token(), workdir=args.workdir)
    print(status_mod.render(per_day, end, weeks=args.weeks))
    stats = status_mod.summarise(per_day, end, window=args.weeks * 7)
    print()
    print(f"active days      : {stats['active_days']} / {stats['window_days']} "
          f"({stats['coverage_pct']}%)")
    print(f"current streak   : {stats['current_streak']} day(s)")
    print(f"contributions    : {stats['total_contributions']}")
    if stats["busiest"]:
        print("busiest days     : " + ", ".join(f"{d} ({c})" for d, c in stats["busiest"]))
    if stats["missing"] and args.verbose:
        gaps = stats["missing"]
        print(f"missing days     : {len(gaps)}")
        print("                   " + ", ".join(d.isoformat() for d in gaps[:40])
              + (" ..." if len(gaps) > 40 else ""))
    return 0


# --------------------------------------------------------------------------- tasks


def cmd_tasks(args) -> int:
    for name in available():
        from .tasks import get as get_task

        task = get_task(name)
        print(f"{name:<12} {task.description}")
    return 0


# --------------------------------------------------------------------------- install


ACTIONS_WORKFLOW = """name: garden

# Fallback workflow, used when the packaged copy of .github/workflows/garden.yml
# is not available. Prefer the file in the repository - it has the heal step
# and the backfill request handling.

on:
  schedule:
    - cron: "13 4 * * *"
    - cron: "37 12 * * *"
    - cron: "9 20 * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: garden
  cancel-in-progress: true

jobs:
  day:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          token: ${{ secrets.GARDEN_PAT || github.token }}

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install garden
        run: pip install -e .

      - name: Use a real git identity
        run: |
          git config user.name "Shounak Joshi"
          git config user.email "shounakjoshi88@gmail.com"

      - name: Run the day
        env:
          GARDEN_TOKEN: ${{ secrets.GARDEN_PAT || github.token }}
          GARDEN_PAT: ${{ secrets.GARDEN_PAT }}
        run: python -m garden day --push

      - name: Push fleet repos
        if: always()
        env:
          GARDEN_TOKEN: ${{ secrets.GARDEN_PAT || github.token }}
        run: python -m garden push-fleet
"""


def _canonical_workflow() -> str:
    """Prefer the workflow file that ships with this checkout, fall back to the
    embedded copy so the command still works from an installed package."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(os.path.dirname(here), ".github", "workflows", "garden.yml")
    if os.path.isfile(candidate):
        with open(candidate, "r", encoding="utf-8") as fh:
            return fh.read()
    return ACTIONS_WORKFLOW


def cmd_install_actions(args) -> int:
    root = os.path.abspath(args.path or os.getcwd())
    workflow_dir = os.path.join(root, ".github", "workflows")
    os.makedirs(workflow_dir, exist_ok=True)
    target = os.path.join(workflow_dir, "garden.yml")
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_canonical_workflow())
    print(f"wrote {target}")
    return 0


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="garden",
        description="A daily engineering rhythm that shows up on the contribution graph.",
    )
    parser.add_argument("--config", help=f"path to {DEFAULT_CONFIG}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="set up the ops repository and config")
    p.add_argument("--ops-repo", help="path to the ops (hub) repository")
    p.add_argument("--repo", action="append", help="owner/name to monitor (repeatable)")
    p.add_argument("--tasks", action="append", help="tasks to run (repeatable)")
    p.add_argument("--sources", action="append", help="folder of markdown notes for the cards task")
    p.add_argument("--author-name", help="git author name for the commits garden makes")
    p.add_argument("--author-email", help="git author email (must be verified on GitHub)")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("day", help="run one day of checks and commit the results")
    p.add_argument("--date", type=_parse_date, help="day to run for (default: today)")
    p.add_argument("--repo", action="append", help="limit to these repos")
    p.add_argument("--cap", type=int, help="max commits for the day")
    p.add_argument("--push", action="store_true", help="push after committing")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--hub-only", action="store_true", help="only write to the ops hub")
    p.add_argument("--workdir", help="where to clone fleet repositories")
    p.set_defaults(func=cmd_day)

    p = sub.add_parser("split", help="split a batch of changes into logical commits")
    p.add_argument("path", nargs="?", help="repository (default: current directory)")
    p.add_argument("--max-per-commit", type=int, default=8)
    p.add_argument("--date", type=_parse_date, help="author date for the new commits")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--push", action="store_true")
    p.set_defaults(func=cmd_split)

    p = sub.add_parser("backfill", help="fill missing days in the history")
    p.add_argument("--from", dest="frm", type=_parse_date, help="first day to fill")
    p.add_argument("--to", type=_parse_date, help="last day to fill")
    p.add_argument("--days", type=int, default=30, help="fill the last N days (default: 30)")
    p.add_argument("--tasks", action="append", help="tasks to run while filling")
    p.add_argument("--cap", type=int, help="override the commit count the rhythm chose")
    p.add_argument("--push", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--workdir")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("push-fleet", help="push the ops repo and every monitored repository")
    p.add_argument("--workdir")
    p.set_defaults(func=cmd_push_fleet)

    p = sub.add_parser("request-backfill", help="ask the workflow to fill or rebuild a date range")
    p.add_argument("--from", dest="frm", type=_parse_date, help="first day")
    p.add_argument("--to", type=_parse_date, help="last day")
    p.add_argument("--days", type=int, default=30, help="the last N days")
    p.add_argument("--rebuild", action="store_true",
                   help="rebuild the whole history instead of topping it up")
    p.add_argument("--path", help="repository root (default: current directory)")
    p.set_defaults(func=cmd_request_backfill)

    p = sub.add_parser("rebuild", help="rebuild the ops history with the rhythm applied")
    p.add_argument("--from", dest="frm", type=_parse_date)
    p.add_argument("--to", type=_parse_date)
    p.add_argument("--days", type=int, default=294)
    p.add_argument("--source", help="repository to copy the base files from")
    p.add_argument("--workdir")
    p.add_argument("--push", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("rhythm", help="show the planned activity pattern")
    p.add_argument("--from", dest="frm", type=_parse_date)
    p.add_argument("--to", type=_parse_date)
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--salt", help="change the pattern")
    p.add_argument("--quiet", action="store_true", help="summary only")
    p.set_defaults(func=cmd_rhythm)

    p = sub.add_parser("status", help="preview the contribution calendar")
    p.add_argument("--weeks", type=int, default=26)
    p.add_argument("--workdir")
    p.add_argument("--quiet", dest="verbose", action="store_false")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("tasks", help="list available tasks")
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("install-actions", help="write the GitHub Actions workflow")
    p.add_argument("--path", help="repository root (default: current directory)")
    p.set_defaults(func=cmd_install_actions)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
