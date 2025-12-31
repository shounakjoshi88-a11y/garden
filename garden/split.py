"""`garden split` - turn one messy batch of work into several clean commits.

Why this exists: GitHub counts *commits* per day for the contribution graph, not
lines changed. A day where you touched 40 files but committed once shows up as
one faint square. The same day committed as six logical commits shows up as a
solid one. This module does that split for you, with real conventional-commit
messages instead of "WIP" x6.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

from . import gitutil
from .classify import group_changes, summarise


@dataclass
class SplitResult:
    commits: list[tuple[str, str, list[str]]]  # (message, kind, files)
    skipped: list[str]

    @property
    def count(self) -> int:
        return len(self.commits)


def _diff_for(path: str, files: list[str]) -> dict[str, str]:
    """Per-file diff body for the given files (staged + unstaged)."""
    out: dict[str, str] = {}
    for f in files:
        normalised = f.replace("\\", "/")
        proc = gitutil.run(["diff", "HEAD", "--", normalised], cwd=path)
        body = proc.stdout
        if not body.strip():
            proc = gitutil.run(["diff", "--cached", "--", normalised], cwd=path)
            body = proc.stdout
        out[normalised] = body
    return out


def plan(repo_path: str, *, max_per_commit: int = 8) -> SplitResult:
    """Work out the commit plan without touching the repo."""
    statuses = gitutil.status_files(repo_path)
    files = [f for _code, f in statuses if f]
    if not files:
        return SplitResult(commits=[], skipped=[])

    diffs = _diff_for(repo_path, files)
    buckets = group_changes(files, diffs, max_per_commit=max_per_commit)

    commits: list[tuple[str, str, list[str]]] = []
    for bucket in buckets:
        diff_text = "\n".join(diffs.get(f, "") for f in bucket.files)
        summary = summarise(bucket.files, diff_text)
        scope = bucket.scope
        subject = f"{bucket.kind}({scope}): {summary}" if scope else f"{bucket.kind}: {summary}"
        commits.append((subject, bucket.kind, bucket.files))
    return SplitResult(commits=commits, skipped=[])


def apply(
    repo_path: str,
    *,
    max_per_commit: int = 8,
    when: dt.date | None = None,
    hour: int = 12,
    minute: int = 0,
    dry_run: bool = False,
    message_overrides: dict[int, str] | None = None,
) -> SplitResult:
    """Create the commits. Each bucket is staged and committed on its own."""
    result = plan(repo_path, max_per_commit=max_per_commit)
    if dry_run or not result.commits:
        return result

    overrides = message_overrides or {}
    for index, (subject, _kind, files) in enumerate(result.commits):
        message = overrides.get(index, subject)
        gitutil.add(repo_path, files)
        if not gitutil.has_staged_changes(repo_path):
            continue
        gitutil.commit(repo_path, message, when=when, hour=hour, minute=minute)
    return result


def split_command(
    repo_path: str,
    *,
    max_per_commit: int = 8,
    when: dt.date | None = None,
    dry_run: bool = False,
    push: bool = False,
) -> SplitResult:
    result = apply(
        repo_path,
        max_per_commit=max_per_commit,
        when=when,
        dry_run=dry_run,
    )
    if push and not dry_run:
        gitutil.pull(repo_path)
        gitutil.push(repo_path)
    return result


def format_plan(result: SplitResult) -> str:
    if not result.commits:
        return "Nothing to split - the working tree is clean."
    lines = [f"{result.count} commit(s) planned:", ""]
    for i, (subject, kind, files) in enumerate(result.commits, 1):
        lines.append(f"  {i:>2}. [{kind}] {subject}")
        for f in files:
            lines.append(f"        {f}")
    return "\n".join(lines)
