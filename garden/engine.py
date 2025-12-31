"""The daily engine.

One run = one day of activity. It picks real checks, runs them against your
repositories, writes the resulting artifacts and commits each one separately.

Several small real commits on the same day is what turns a faint square on the
contribution graph into a solid one - GitHub counts commits, not lines changed.
The engine enforces a daily commit budget so the history stays readable.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field

from . import gitutil, rhythm
from .config import Config, RepoTarget
from .tasks import Artifact, TaskContext, get as get_task

DEFAULT_TASKS = ["metrics", "todos", "notes"]

# Tasks that describe the day rather than a particular repository. They run
# once, against the ops repo, instead of once per monitored repository.
HUB_SCOPED = {"cards"}


@dataclass
class CommitRecord:
    repo: str
    message: str
    sha: str | None
    summary: str
    path: str


@dataclass
class DayResult:
    date: dt.date
    commits: list[CommitRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.commits)


def ensure_repo(path: str, *, initialise: bool = True, author_name: str = "", author_email: str = "") -> str:
    """Make sure `path` is a git repository, creating one if allowed."""
    if gitutil.is_repo(path):
        _apply_identity(path, author_name, author_email)
        return path
    if not initialise:
        raise RuntimeError(f"not a git repository: {path}")
    os.makedirs(path, exist_ok=True)
    gitutil.run(["init", "-b", "main"], cwd=path, check=True)
    _apply_identity(path, author_name, author_email, fallback=True)
    readme = os.path.join(path, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# garden\n\nDaily engineering records for the Shounak Joshi profile.\n")
    gitutil.add(path, ["README.md"])
    gitutil.commit(path, "chore: initialise garden ops repository")
    return path


def _apply_identity(path: str, author_name: str, author_email: str, fallback: bool = False) -> None:
    """Set the git identity used for the commits garden makes.

    GitHub attributes a contribution to the account whose verified email
    authored the commit, so this must be the real identity - a bot identity
    silently produces commits that never show up on the contribution graph.
    """
    if author_name:
        gitutil.run(["config", "user.name", author_name], cwd=path)
    if author_email:
        gitutil.run(["config", "user.email", author_email], cwd=path)
    if fallback and not (author_name and author_email):
        gitutil.run(["config", "user.name", "garden-bot"], cwd=path)
        gitutil.run(
            ["config", "user.email", "garden-bot@users.noreply.github.com"], cwd=path
        )


def resolve_fleet(
    cfg: Config, workdir: str, token: str | None
) -> list[tuple[RepoTarget, str]]:
    """Resolve every fleet entry to a local path, cloning when needed."""
    workdir = os.environ.get("GARDEN_WORKDIR") or workdir
    resolved: list[tuple[RepoTarget, str]] = []
    for target in cfg.fleet:
        if not target.enabled:
            continue
        path = target.resolve()
        if not path and target.clone:
            dest = os.path.join(workdir, target.clone.replace("/", "__"))
            if not os.path.isdir(os.path.join(dest, ".git")):
                url = f"https://github.com/{target.clone}.git"
                try:
                    gitutil.clone(url, dest, token=token, depth=200)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! could not clone {target.clone}: {exc}")
                    continue
            path = dest
        if not path:
            continue
        if not gitutil.is_repo(path):
            print(f"  ! {target.name}: {path} is not a git repository - skipping")
            continue
        resolved.append((target, path))
    return resolved


def _task_sequence(tasks_per_repo: list[list[str]]) -> list[tuple[int, str]]:
    """Round-robin the tasks so every repository contributes early."""
    sequence: list[tuple[int, str]] = []
    depth = 0
    while True:
        added = False
        for index, tasks in enumerate(tasks_per_repo):
            if depth < len(tasks):
                sequence.append((index, tasks[depth]))
                added = True
        if not added:
            break
        depth += 1
    return sequence


def fleet_writes_enabled() -> bool:
    """Can garden commit into the monitored repositories, or only the hub?

    The built-in workflow token cannot write to other repositories, so writing
    there would create commits that never reach GitHub and never count. In
    that case the hub carries the load on its own - which is the behaviour
    that always works.

    GARDEN_PAT is the personal access token secret, and is empty when the
    secret is not configured. GARDEN_TOKEN is the token actually used for the
    clone and the push, which on CI falls back to the built-in one.
    """
    if os.environ.get("GARDEN_PAT"):
        return True
    return not os.environ.get("CI")


def run_day(
    cfg: Config,
    when: dt.date | None = None,
    *,
    repos: list[str] | None = None,
    push: bool = False,
    dry_run: bool = False,
    cap: int | None = None,
    token: str | None = None,
    workdir: str | None = None,
    hub_only: bool = False,
) -> DayResult:
    day = when or dt.date.today()
    salt = cfg.rhythm_salt or rhythm.DEFAULT_SALT
    result = DayResult(date=day)

    hub_path = cfg.ops_repo_path
    if not hub_path:
        result.errors.append("no ops_repo.path configured")
        return result

    fleet = resolve_fleet(
        cfg, workdir or os.path.join(os.path.dirname(hub_path), "_fleet"), token
    )
    if repos:
        wanted = {r.lower() for r in repos}
        fleet = [
            (t, p)
            for (t, p) in fleet
            if t.name.lower() in wanted or (t.clone or "").lower() in wanted
        ]

    if cap is None:
        # The rhythm decides what the day should look like. If the day already
        # carries its target, there is nothing left to add.
        target = rhythm.target_for(day, salt)
        if target <= 0:
            result.skipped.append(f"{day.isoformat()} is a quiet day in the rhythm")
            return result
        existing = 0
        if gitutil.is_repo(hub_path):
            existing = gitutil.commits_per_day(hub_path, since=day).get(day, 0)
        cap = target - existing
        if cap <= 0:
            result.skipped.append(
                f"{day.isoformat()} already has {existing} commit(s), target {target}"
            )
            return result

    budget = max(1, cap) - 1  # one slot reserved for the day summary
    clock = rhythm.times_for(day, cap, salt)
    slot = {"index": 0}

    def stamp() -> tuple[int, int]:
        if not clock:
            return 12, 0
        pair = clock[slot["index"] % len(clock)]
        slot["index"] += 1
        return pair

    if not dry_run:
        ensure_repo(hub_path, author_name=cfg.author_name, author_email=cfg.author_email)
        for _target, path in fleet:
            _apply_identity(path, cfg.author_name, cfg.author_email)

    tasks_per_repo: list[list[str]] = []
    hub_task_names: list[str] = []
    for index, (target, _path) in enumerate(fleet):
        names = list(cfg.fleet_tasks_for(target))
        # Rotate which check each repository starts on, so over a week every
        # check gets its turn instead of metrics eating the whole budget.
        if names:
            offset = (day.toordinal() + index) % len(names)
            names = names[offset:] + names[:offset]
        tasks_per_repo.append([n for n in names if n not in HUB_SCOPED])
        for name in names:
            if name in HUB_SCOPED and name not in hub_task_names:
                hub_task_names.append(name)

    write_fleet = fleet_writes_enabled()
    if not write_fleet and fleet:
        print("  ~ fleet writes disabled (no GARDEN_PAT secret on CI) - "
              "the ops hub carries the whole day")

    sequence = _task_sequence(tasks_per_repo)
    executed: list[tuple[str, str, list[Artifact]]] = []

    for index, task_name in sequence:
        if budget <= 0:
            break
        target, path = fleet[index]
        ctx = TaskContext(
            repo_path=path,
            repo_name=target.name,
            date=day,
            config=cfg,
            ops_dir="ops",
            shallow=gitutil.is_shallow(path),
            state_path=hub_path,
        )
        try:
            artifacts = get_task(task_name).run(ctx)
        except KeyError as exc:
            result.errors.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{target.name}/{task_name}: {exc}")
            continue
        if not artifacts:
            continue

        used: list[Artifact] = []
        for artifact in artifacts:
            if budget <= 0:
                break
            if target.write_ops_here and not hub_only and write_fleet:
                _commit_artifact(path, artifact, day, result, repo=target.name,
                                 dry_run=dry_run, at=stamp())
                budget -= 1
                used.append(artifact)
            if budget <= 0:
                break
            tail = (
                artifact.rel_path.split("ops/", 1)[-1]
                if "ops/" in artifact.rel_path
                else artifact.rel_path
            )
            mirrored = Artifact(
                rel_path=f"ops/{target.name}/{tail}",
                content=artifact.content,
                message=artifact.message,
                summary=artifact.summary,
            )
            _commit_artifact(hub_path, mirrored, day, result, repo="hub",
                             dry_run=dry_run, at=stamp())
            budget -= 1
        executed.append((target.name, task_name, used))

    # Hub-scoped checks (knowledge cards) run against the ops repo itself.
    for task_name in hub_task_names:
        if budget <= 0:
            break
        ctx = TaskContext(
            repo_path=hub_path, repo_name="garden", date=day, config=cfg, ops_dir="ops",
            state_path=hub_path,
        )
        try:
            artifacts = get_task(task_name).run(ctx)
        except KeyError as exc:
            result.errors.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"hub/{task_name}: {exc}")
            continue
        for artifact in artifacts:
            if budget <= 0:
                break
            _commit_artifact(hub_path, artifact, day, result, repo="hub",
                             dry_run=dry_run, at=stamp())
            budget -= 1
        executed.append(("garden", task_name, artifacts))

    # The hub always records what happened today.
    summary_lines = [
        f"# garden run - {day.isoformat()}",
        "",
        f"- Repositories monitored: {len(fleet)}",
        f"- Checks executed: {len(executed)}",
        f"- Commits created: {result.count}",
        "",
        "| Repository | Check | Artifact |",
        "| --- | --- | --- |",
    ]
    for repo_name, task_name, artifacts in executed:
        for artifact in artifacts:
            summary_lines.append(f"| {repo_name} | {task_name} | `{artifact.rel_path}` |")
    if not executed:
        summary_lines.append("| _none_ | | |")
    summary_lines.append("")

    hub_summary = Artifact(
        rel_path=f"ops/days/{day.isoformat()}.md",
        content="\n".join(summary_lines),
        message=f"chore(ops): record garden run for {day.isoformat()}",
        summary="day summary",
    )
    _commit_artifact(hub_path, hub_summary, day, result, repo="hub",
                     dry_run=dry_run, at=stamp())

    # One push per repository at the end, not one per commit.
    if push and not dry_run:
        touched = {hub_path}
        touched.update(path for _t, path in fleet)
        for path in sorted(touched):
            if not (gitutil.is_repo(path) and gitutil.remote_url(path)):
                continue
            gitutil.pull(path)
            ok, message = gitutil.push_result(path)
            if not ok:
                # Not fatal: the hub push already landed, and a fleet
                # repository without write access is a configuration gap,
                # not a failed run.
                result.warnings.append(f"push failed for {path}: {message}")

    return result


def _commit_artifact(
    repo_path: str,
    artifact: Artifact,
    day: dt.date,
    result: DayResult,
    *,
    repo: str,
    dry_run: bool,
    at: tuple[int, int] = (12, 0),
) -> None:
    if dry_run:
        result.commits.append(
            CommitRecord(repo, artifact.message, None, artifact.summary, artifact.rel_path)
        )
        return
    try:
        gitutil.write_file(repo_path, artifact.rel_path, artifact.content)
        gitutil.add(repo_path, [artifact.rel_path])
        if not gitutil.has_staged_changes(repo_path):
            return
        sha = gitutil.commit(repo_path, artifact.message, when=day, hour=at[0], minute=at[1])
        if not sha:
            result.errors.append(
                f"{repo}: nothing committed for {artifact.rel_path} "
                f"(is {repo_path} a git repository?)"
            )
            return
        result.commits.append(
            CommitRecord(repo, artifact.message, sha, artifact.summary, artifact.rel_path)
        )
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"{repo}: {artifact.rel_path}: {exc}")


def init_ops_repo(
    hub_path: str,
    *,
    clone: str | None = None,
    token: str | None = None,
    author_name: str = "",
    author_email: str = "",
) -> str:
    ensure_repo(hub_path, author_name=author_name, author_email=author_email)
    if clone and not gitutil.remote_url(hub_path):
        gitutil.run(["remote", "add", "origin", f"https://github.com/{clone}.git"], cwd=hub_path)
    for folder in ("ops/days", "ops/metrics", "ops/notes", "ops/cards"):
        os.makedirs(os.path.join(hub_path, folder), exist_ok=True)
    return hub_path


def git_config_set(path: str, name: str, value: str) -> None:
    gitutil.run(["config", name, value], cwd=path)
