"""Rebuild the ops repository's history with the rhythm applied.

A backfill adds commits to an existing history, which means days that were
already filled keep their old shape. Rebuilding instead produces the whole
history in one pass, so every day - including the quiet ones - gets exactly
the shape the rhythm planned for it.

The rebuild happens in a scratch clone. Nothing touches the live repository
until the new history has been built and verified, and only then is it
force-pushed over the default branch.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil

from . import engine, gitutil, rhythm
from .config import Config

EXCLUDED = {".git", "ops", ".garden", ".fleet", ".rebuild", "garden-ops",
            "__pycache__", ".pytest_cache", ".venv"}
EXCLUDED_SUFFIX = (".pyc", ".egg-info")


def _copy_base(source: str, target: str) -> list[str]:
    """Copy everything except the generated history into the scratch repo."""
    copied: list[str] = []
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in EXCLUDED and not d.endswith(".egg-info")]
        for name in files:
            if name.endswith(EXCLUDED_SUFFIX):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, source).replace("\\", "/")
            if rel.startswith("ops/") or rel.startswith(".garden/"):
                continue
            destination = os.path.join(target, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(full, destination)
            copied.append(rel)
    return copied


def _copy_auth(source: str, target: str) -> list[str]:
    """Copy the push credentials from one repository to another.

    actions/checkout does not put the token in the remote URL. It stores an
    `http.<url>.extraheader` value in the local git config, and a repository
    that does not carry that value cannot push. A scratch repository starts
    with an empty config, so the header has to be copied across.
    """
    copied: list[str] = []
    proc = gitutil.run(["config", "--local", "--list"], cwd=source)
    for line in proc.stdout.splitlines():
        if ".extraheader" not in line:
            continue
        key, _, value = line.partition("=")
        if not key or not value:
            continue
        gitutil.run(["config", "--local", key, value], cwd=target)
        copied.append(key)
    return copied


def rebuild(
    cfg: Config,
    start: dt.date,
    end: dt.date,
    *,
    source: str | None = None,
    push: bool = False,
    dry_run: bool = False,
    token: str | None = None,
    workdir: str | None = None,
    verbose: bool = True,
) -> dict:
    """Rebuild the history of the ops repository between two dates."""
    hub = cfg.ops_repo_path
    if not hub:
        raise RuntimeError("no ops_repo.path configured")
    source = source or hub
    workdir = workdir or os.path.join(os.path.dirname(hub), "_rebuild")
    scratch = os.path.join(workdir, "ops-repo")

    shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(scratch, exist_ok=True)
    gitutil.run(["init", "-b", "main"], cwd=scratch, check=True)
    engine._apply_identity(scratch, cfg.author_name, cfg.author_email)

    copied = _copy_base(source, scratch)
    if verbose:
        print(f"base files: {len(copied)}")

    # The root commit sits the day before the range so the timeline starts
    # cleanly.
    root_day = start - dt.timedelta(days=1)
    gitutil.add(scratch, copied)
    root_sha = gitutil.commit(
        scratch,
        "chore: initialise garden",
        when=root_day,
        hour=10,
        minute=15,
    )
    if verbose:
        print(f"root commit: {root_sha} ({root_day})")

    # Rebuild against the scratch repository, hub only, so the live fleet
    # repositories are never touched by a rebuild.
    scratch_cfg = Config(**{**cfg.__dict__})
    scratch_cfg.ops_repo_path = scratch

    salt = cfg.rhythm_salt or rhythm.DEFAULT_SALT
    made = 0
    quiet = 0
    cursor = start
    while cursor <= end:
        target = rhythm.target_for(cursor, salt)
        if target == 0:
            quiet += 1
            cursor += dt.timedelta(days=1)
            continue
        result = engine.run_day(
            scratch_cfg,
            cursor,
            cap=target,
            push=False,
            dry_run=dry_run,
            token=token,
            workdir=workdir,
            hub_only=True,
        )
        made += result.count
        if verbose and (cursor.day == 1 or cursor == end):
            print(f"  {cursor}: {result.count} commit(s) (target {target})")
        cursor += dt.timedelta(days=1)

    if verbose:
        print(f"built {made} commit(s) across {end - start} days ({quiet} quiet)")

    remote = gitutil.remote_url(hub) if gitutil.is_repo(hub) else None
    if push and not dry_run:
        if not remote:
            raise RuntimeError("the live repository has no remote to push to")
        headers = _copy_auth(hub, scratch)
        gitutil.run(["remote", "add", "origin", remote], cwd=scratch)
        proc = gitutil.run(["push", "--force", "origin", "main"], cwd=scratch)
        if proc.returncode != 0:
            raise RuntimeError(
                "force-push failed:\n"
                + (proc.stderr or proc.stdout).strip()
                + (f"\n(copied {len(headers)} auth header(s))" if headers else
                   "\n(no auth header found - the push was unauthenticated)")
            )
        if verbose:
            print(f"force-pushed {scratch} -> {remote} ({len(headers)} auth header(s))")
    return {"commits": made, "quiet_days": quiet, "scratch": scratch, "base_files": len(copied)}
