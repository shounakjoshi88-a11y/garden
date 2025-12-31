"""Thin, dependency-free wrappers around the git CLI.

Everything here shells out to `git` rather than using a library so the engine
runs identically on a laptop, in GitHub Actions, and on a fresh container.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
from dataclasses import dataclass
from typing import Iterable, Sequence

GIT_TIMEOUT = 900


class GitError(RuntimeError):
    """Raised when a git invocation fails and the caller asked for a hard error."""


@dataclass
class Commit:
    sha: str
    date: dt.date
    subject: str


def run(
    args: Sequence[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
    timeout: int = GIT_TIMEOUT,
) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=merged,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed in {cwd or '.'} (exit {proc.returncode})\n"
            f"stdout: {proc.stdout.strip()}\nstderr: {proc.stderr.strip()}"
        )
    return proc


def is_repo(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    return run(["rev-parse", "--is-inside-work-tree"], cwd=path).stdout.strip() == "true"


def toplevel(path: str) -> str | None:
    proc = run(["rev-parse", "--show-toplevel"], cwd=path)
    if proc.returncode != 0:
        return None
    top = proc.stdout.strip()
    return top or None


def is_shallow(path: str) -> bool:
    """True when the clone has a truncated history."""
    return os.path.isfile(os.path.join(path, ".git", "shallow"))


def current_branch(path: str) -> str:
    proc = run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    branch = proc.stdout.strip()
    return branch if branch and branch != "HEAD" else "main"


def remote_url(path: str) -> str | None:
    proc = run(["remote", "get-url", "origin"], cwd=path)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def status_files(path: str) -> list[tuple[str, str]]:
    """Return [(status, relative_path)] for staged, unstaged and untracked work."""
    proc = run(["status", "--porcelain=v1", "--untracked-files=all"], cwd=path)
    out: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        code = line[:2].strip() or "?"
        item = line[3:]
        if " -> " in item:  # rename: "old -> new"
            item = item.split(" -> ", 1)[1]
        out.append((code, item.strip('"')))
    return out


def diff_stat(path: str, files: Iterable[str] | None = None) -> str:
    args = ["diff", "--stat", "--no-renames"]
    if files:
        args += ["--", *files]
    else:
        args.append("HEAD")
    return run(args, cwd=path).stdout.strip()


def staged_diff(path: str) -> str:
    return run(["diff", "--cached", "--no-renames"], cwd=path).stdout


def add(path: str, files: Iterable[str]) -> None:
    files = list(files)
    if not files:
        return
    run(["add", "--", *files], cwd=path)


def has_staged_changes(path: str) -> bool:
    proc = run(["diff", "--cached", "--name-only"], cwd=path)
    return bool(proc.stdout.strip())


def commit(
    path: str,
    message: str,
    *,
    when: dt.date | None = None,
    hour: int = 12,
    minute: int = 0,
    author_name: str | None = None,
    author_email: str | None = None,
) -> str | None:
    """Create a commit. `when` sets the *author* date, which is what GitHub's
    contribution calendar reads.

    Returns the new commit sha, or None when there was nothing to commit.
    """
    env: dict[str, str] = {}
    if when is not None:
        stamp = dt.datetime(when.year, when.month, when.day, hour, minute, 0).astimezone()
        iso = stamp.isoformat()
        env["GIT_AUTHOR_DATE"] = iso
        env["GIT_COMMITTER_DATE"] = iso
    if author_name:
        env["GIT_AUTHOR_NAME"] = author_name
    if author_email:
        env["GIT_AUTHOR_EMAIL"] = author_email
    run(["commit", "-m", message], cwd=path, env=env)
    proc = run(["rev-parse", "HEAD"], cwd=path)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def log(
    path: str,
    *,
    since: dt.date | None = None,
    until: dt.date | None = None,
    limit: int = 2000,
) -> list[Commit]:
    """Commits in a repository, newest first.

    The date range is applied to the *author* date, in Python rather than with
    git's --since/--until. Git filters on the committer date, and backdated
    commits - the whole point of a backfill - have a committer date of "now",
    so git's own filter would drop them.
    """
    fmt = "%H%x1f%aI%x1f%s"
    proc = run(["log", f"--format={fmt}", f"--max-count={limit}"], cwd=path)
    commits: list[Commit] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        sha, iso, subject = parts
        try:
            day = dt.date.fromisoformat(iso[:10])
        except ValueError:
            continue
        if since and day < since:
            continue
        if until and day > until:
            continue
        commits.append(Commit(sha=sha, date=day, subject=subject))
    return commits


def active_days(path: str, since: dt.date | None = None) -> set[dt.date]:
    return {c.date for c in log(path, since=since)}


def commits_per_day(path: str, since: dt.date | None = None) -> dict[dt.date, int]:
    counts: dict[dt.date, int] = {}
    for commit in log(path, since=since):
        counts[commit.date] = counts.get(commit.date, 0) + 1
    return counts


def clone(url: str, dest: str, *, token: str | None = None, depth: int | None = None) -> str:
    """Clone `url` into `dest`. A token is injected for private repos."""
    if token:
        if url.startswith("https://"):
            url = url.replace("https://", f"https://x-access-token:{token}@", 1)
    args = ["clone"]
    if depth:
        args += ["--depth", str(depth)]
    args += [url, dest]
    run(args, check=True, timeout=1800)
    return dest


def pull(path: str) -> None:
    run(["pull", "--rebase", "--autostash", "origin", current_branch(path)], cwd=path)


def push_result(path: str, *, force: bool = False) -> tuple[bool, str]:
    branch = current_branch(path)
    args = ["push", "origin", branch]
    if force:
        args.insert(1, "--force-with-lease")
    proc = run(args, cwd=path)
    message = (proc.stderr or proc.stdout or "").strip().splitlines()
    return proc.returncode == 0, (message[-1] if message else "")


def push(path: str, *, force: bool = False) -> bool:
    ok, _message = push_result(path, force=force)
    return ok


def show_file(path: str, rel: str) -> str | None:
    proc = run(["show", f"HEAD:{rel}"], cwd=path)
    if proc.returncode != 0:
        return None
    return proc.stdout


def read_file(path: str, rel: str) -> str | None:
    full = os.path.join(path, rel)
    if not os.path.isfile(full):
        return None
    with open(full, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def write_file(path: str, rel: str, content: str) -> str:
    full = os.path.join(path, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return full
