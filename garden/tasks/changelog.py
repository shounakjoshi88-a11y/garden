"""Changelog generated from conventional commits."""

from __future__ import annotations

import re

from .. import gitutil
from .base import Artifact, TaskContext, register

SECTIONS = [
    ("feat", "Added"),
    ("fix", "Fixed"),
    ("perf", "Performance"),
    ("refactor", "Changed"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("build", "Build"),
    ("ci", "CI"),
    ("chore", "Chores"),
]

CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<subject>.+)$")


def _last_tag(path: str) -> str | None:
    proc = gitutil.run(["describe", "--tags", "--abbrev=0"], cwd=path)
    tag = proc.stdout.strip()
    return tag if proc.returncode == 0 and tag else None


@register
def changelog_task():
    class ChangelogTask:
        name = "changelog"
        description = "Generate a changelog from conventional commits."

        def run(self, ctx: TaskContext) -> list[Artifact]:
            root = ctx.repo_path
            tag = _last_tag(root)
            rev = f"{tag}..HEAD" if tag else "HEAD"
            commits = gitutil.log(root, limit=300)
            # keep only commits after the tag, when there is one
            if tag:
                tagged = gitutil.run(["rev-list", "-1", tag], cwd=root).stdout.strip()
                if tagged:
                    commits = [c for c in commits if _is_ancestor(root, tagged, c.sha)]

            grouped: dict[str, list[str]] = {}
            for c in commits:
                match = CONVENTIONAL.match(c.subject)
                if match:
                    kind = match.group("type")
                    scope = match.group("scope")
                    subject = match.group("subject")
                else:
                    kind = "chore"
                    scope = ""
                    subject = c.subject
                scope_part = f"**{scope}**: " if scope else ""
                grouped.setdefault(kind, []).append(f"- {scope_part}{subject} ({c.date.isoformat()})")

            if not grouped:
                return []

            lines = [
                f"# Changelog - {ctx.repo_name}",
                "",
                f"_Generated {ctx.date.isoformat()} from {len(commits)} commit(s)"
                + (f" since `{tag}`." if tag else "."),
                "",
            ]
            for kind, title in SECTIONS:
                items = grouped.get(kind)
                if not items:
                    continue
                lines.append(f"## {title}")
                lines.append("")
                lines.extend(sorted(set(items)))
                lines.append("")
            for kind in sorted(set(grouped) - {k for k, _ in SECTIONS}):
                lines.append(f"## {kind.title()}")
                lines.append("")
                lines.extend(sorted(set(grouped[kind])))
                lines.append("")

            rel = f"{ctx.ops_dir}/CHANGELOG.md"
            return [Artifact(rel, "\n".join(lines), f"docs(ops): regenerate {ctx.repo_name} changelog",
                            "changelog")]

    return ChangelogTask()


def _is_ancestor(root: str, ancestor: str, descendant: str) -> bool:
    proc = gitutil.run(["merge-base", "--is-ancestor", ancestor, descendant], cwd=root)
    return proc.returncode == 0
