"""Task framework.

A task inspects a repository and produces artifacts. Each artifact becomes its
own commit, so one day of automation yields several contributions on the
contribution graph while the repository gains real, inspectable content.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..config import Config


@dataclass
class Artifact:
    """One file the task wants written, and the commit that should carry it."""

    rel_path: str          # path relative to the repo root
    content: str
    message: str           # commit subject
    summary: str = ""      # one-line description for the run log


@dataclass
class TaskContext:
    repo_path: str
    repo_name: str
    date: dt.date
    config: Config
    ops_dir: str = "ops"
    extras: dict = field(default_factory=dict)
    shallow: bool = False
    # Where the ops hub lives. Tasks that carry state across days read it
    # from here, because the artifact may land in the hub rather than in the
    # repository being inspected.
    state_path: str = ""

    def op_path(self, *parts: str) -> str:
        return "/".join([self.ops_dir, *parts])


class Task(Protocol):
    name: str
    description: str

    def run(self, ctx: TaskContext) -> list[Artifact]: ...


REGISTRY: dict[str, Callable[[], Task]] = {}


def register(fn=None, *, name: str | None = None):
    """Register a task. Usable bare (`@register`) or with a name."""

    def deco(factory: Callable[[], Task]) -> Callable[[], Task]:
        key = name or factory.__name__.removesuffix("_task")
        REGISTRY[key] = factory
        return factory

    if fn is None:
        return deco
    return deco(fn)


def get(name: str):
    if name not in REGISTRY:
        raise KeyError(f"unknown task '{name}' (have: {', '.join(sorted(REGISTRY))})")
    return REGISTRY[name]()


def available() -> list[str]:
    return sorted(REGISTRY)


def load_all() -> None:
    """Import the task modules so they self-register."""
    from . import (  # noqa: F401
        cards,
        changelog,
        coverage,
        deps,
        lint,
        metrics,   # also registers the `todos` task
        notes,
        tests,
    )
