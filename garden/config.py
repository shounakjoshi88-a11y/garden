"""Config loading for garden.

Config lives in a single JSON file so it can be committed into the ops repo and
read identically by a laptop run and a GitHub Actions run.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

DEFAULT_CONFIG_NAME = "garden.config.json"


@dataclass
class RepoTarget:
    """One repository garden watches.

    `path` is a local checkout, `clone` is an "owner/name" to fetch instead.
    A repo can have both: clone is used when the path is missing.
    """

    name: str
    path: str | None = None
    clone: str | None = None
    tasks: list[str] = field(default_factory=list)
    enabled: bool = True
    write_ops_here: bool = True  # commit ops/ artifacts into this repo itself

    def resolve(self) -> str | None:
        if self.path and os.path.isdir(self.path):
            return self.path
        return None


@dataclass
class Config:
    ops_repo_path: str = ""
    ops_repo_clone: str | None = None
    fleet: list[RepoTarget] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)
    max_commits_per_day: int = 12
    commit_hour: int = 12
    topics: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    notes_dir: str = "notes"
    # Git identity used for the commits garden makes. GitHub attributes a
    # contribution to the account whose *verified* email authored it, so this
    # has to be a real identity, not a bot.
    author_name: str = "Shounak Joshi"
    author_email: str = "shounakjoshi88@gmail.com"
    # Changes the shape of the activity pattern. Change it to reshuffle which
    # days are busy and which are quiet.
    rhythm_salt: str = "garden-v1"
    config_path: str = ""

    def fleet_tasks_for(self, repo: RepoTarget) -> list[str]:
        return repo.tasks or list(self.tasks)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def load(path: str | None = None) -> Config:
    """Load config from `path`, or from ./garden.config.json, or fall back to
    a sane default. Never raises on a missing file."""
    candidates: list[str] = []
    if path:
        candidates.append(path)
    candidates.append(os.path.join(os.getcwd(), DEFAULT_CONFIG_NAME))
    candidates.append(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), DEFAULT_CONFIG_NAME)
    )

    data: dict[str, Any] = {}
    used = ""
    for cand in candidates:
        if cand and os.path.isfile(cand):
            with open(cand, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            used = cand
            break

    base = os.path.dirname(os.path.abspath(used)) if used else os.getcwd()

    def resolve_rel(p: str | None) -> str:
        if not p:
            return ""
        if os.path.isabs(p) or p.startswith("~"):
            return os.path.expanduser(p)
        return os.path.normpath(os.path.join(base, p))

    ops = data.get("ops_repo") or {}
    fleet: list[RepoTarget] = []
    for entry in data.get("fleet") or []:
        if isinstance(entry, str):
            entry = {"name": entry, "clone": entry}
        fleet.append(
            RepoTarget(
                name=entry.get("name") or entry.get("clone") or "repo",
                path=resolve_rel(entry.get("path")) or None,
                clone=entry.get("clone"),
                tasks=list(entry.get("tasks") or []),
                enabled=_as_bool(entry.get("enabled"), True),
                write_ops_here=_as_bool(entry.get("write_ops_here"), True),
            )
        )

    return Config(
        ops_repo_path=resolve_rel(ops.get("path")),
        ops_repo_clone=ops.get("clone"),
        fleet=fleet,
        tasks=list(data.get("tasks") or ["metrics", "todos", "notes"]),
        max_commits_per_day=int(data.get("max_commits_per_day") or 12),
        commit_hour=int(data.get("commit_hour") or 12),
        topics=list(data.get("topics") or []),
        sources=[resolve_rel(s) for s in (data.get("sources") or [])],
        notes_dir=data.get("notes_dir") or "notes",
        author_name=data.get("author_name") or "",
        author_email=data.get("author_email") or "",
        rhythm_salt=data.get("rhythm_salt") or "garden-v1",
        config_path=used,
    )


def save(cfg: Config, path: str) -> None:
    payload = {
        "version": 1,
        "ops_repo": {"path": cfg.ops_repo_path, "clone": cfg.ops_repo_clone},
        "tasks": cfg.tasks,
        "max_commits_per_day": cfg.max_commits_per_day,
        "commit_hour": cfg.commit_hour,
        "topics": cfg.topics,
        "sources": cfg.sources,
        "notes_dir": cfg.notes_dir,
        "author_name": cfg.author_name,
        "author_email": cfg.author_email,
        "rhythm_salt": cfg.rhythm_salt,
        "fleet": [
            {
                "name": r.name,
                "path": r.path,
                "clone": r.clone,
                "tasks": r.tasks,
                "enabled": r.enabled,
                "write_ops_here": r.write_ops_here,
            }
            for r in cfg.fleet
        ],
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
