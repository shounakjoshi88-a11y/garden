"""Classify changed paths into logical commit buckets.

The goal is that a day of real work turns into several *readable* commits
instead of one blob, because GitHub counts contributions per commit, not per
line changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Ordered: first match wins.
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("test", re.compile(r"(^|/)(tests?|spec|__tests__|e2e)(/|$)|(^|/)(test_[^/]+|[^/]*_test|[^/]*\.test|[^/]*\.spec)\.[^/]+$")),
    ("ci", re.compile(r"(^|/)\.github/|(^|/)(Dockerfile|docker-compose[^/]*)|(^|/)(\.gitlab-ci\.yml|Jenkinsfile|Makefile)$")),
    ("deps", re.compile(r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|package(-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Cargo\.(toml|lock)|go\.(mod|sum)|Gemfile[^/]*)$")),
    ("docs", re.compile(r"(^|/)(docs?|documentation)(/|$)|\.(md|rst|adoc|txt)$|(^|/)README(\.[^/]*)?$|(^|/)CHANGELOG(\.[^/]*)?$|(^|/)LICENSE(\.[^/]*)?$")),
    ("config", re.compile(r"(^|/)\.(env|editorconfig|prettierrc[^/]*|eslintrc[^/]*|eslintignore|gitignore|gitattributes)$|(^|/)(tsconfig[^/]*\.json|jsconfig\.json)$")),
    ("assets", re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|mp4|mov|woff2?|ttf|eot|mp3|wav)$")),
]

# Scope of a commit is the first meaningful path segment.
IGNORE_SCOPES = {"src", "lib", "app", "packages", "source", "internal", "cmd", "pkg"}

# Signals pulled from the diff body to pick a conventional-commit type.
TYPE_SIGNALS: list[tuple[str, re.Pattern[str]]] = [
    ("fix", re.compile(r"\b(fix|bug|crash|regression|broken|typo|revert|hotfix|patch)\b", re.I)),
    ("feat", re.compile(r"\b(add|added|adding|new|implement|feature|support for|introduce)\b", re.I)),
    ("perf", re.compile(r"\b(perf|performance|optimi[sz]e|faster|speed up|memory)\b", re.I)),
    ("refactor", re.compile(r"\b(refactor|clean ?up|rename|restructure|simplify|extract)\b", re.I)),
    ("test", re.compile(r"\b(test|tests|spec|coverage|assert)\b", re.I)),
    ("docs", re.compile(r"\b(document|docs|readme|changelog|comment|typo fix)\b", re.I)),
]


@dataclass
class Bucket:
    kind: str          # conventional commit type: feat/fix/docs/...
    files: list[str]

    @property
    def scope(self) -> str:
        for f in self.files:
            parts = [p for p in f.replace("\\", "/").split("/") if p]
            if not parts:
                continue
            head = parts[0]
            candidate = parts[1] if (head in IGNORE_SCOPES and len(parts) > 1) else head
            candidate = candidate.rsplit(".", 1)[0] if "." in candidate else candidate
            candidate = candidate.strip(".").strip()
            if candidate and not candidate.startswith("."):
                return candidate
        return ""


def classify_by_path(path: str) -> str:
    normalised = path.replace("\\", "/")
    for kind, pattern in RULES:
        if pattern.search(normalised):
            return kind
    return "chore"


def classify_by_content(diff: str) -> str:
    for kind, pattern in TYPE_SIGNALS:
        if pattern.search(diff):
            return kind
    return "chore"


def split_files(files: list[str], max_per_commit: int = 8) -> list[list[str]]:
    """Break a large file group into chunks that stay reviewable.

    Files are grouped by their parent directory first so a commit tends to hold
    one coherent area, then split into chunks of at most `max_per_commit`.
    """
    by_dir: dict[str, list[str]] = {}
    for f in files:
        normalised = f.replace("\\", "/")
        parent = normalised.rsplit("/", 1)[0] if "/" in normalised else "."
        by_dir.setdefault(parent, []).append(normalised)

    chunks: list[list[str]] = []
    for parent in sorted(by_dir):
        group = sorted(by_dir[parent])
        for i in range(0, len(group), max_per_commit):
            chunks.append(group[i : i + max_per_commit])
    return chunks


def group_changes(
    files: list[str],
    diff_for: dict[str, str] | None = None,
    max_per_commit: int = 8,
) -> list[Bucket]:
    """Group changed files into logical buckets, one commit each."""
    diff_for = diff_for or {}
    buckets: dict[str, list[str]] = {}
    for f in files:
        normalised = f.replace("\\", "/")
        kind = classify_by_path(normalised)
        if kind == "chore":
            kind = classify_by_content(diff_for.get(normalised, ""))
        buckets.setdefault(kind, []).append(normalised)

    result: list[Bucket] = []
    for kind in sorted(buckets):
        for chunk in split_files(buckets[kind], max_per_commit):
            result.append(Bucket(kind=kind, files=chunk))
    return result


# --- commit message generation -------------------------------------------------

SYMBOL_PATTERNS = [
    re.compile(r"^\+\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)", re.M),
    re.compile(r"^\+\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\+\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\+\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\+\s*func\s+([A-Za-z_][\w$]*)", re.M),
    re.compile(r"^\+\s*type\s+([A-Za-z_][\w$]*)", re.M),
]


def extract_symbols(diff: str, limit: int = 3) -> list[str]:
    found: list[str] = []
    for pattern in SYMBOL_PATTERNS:
        for match in pattern.findall(diff):
            if match not in found and not match.startswith("_"):
                found.append(match)
    return found[:limit]


def summarise(files: list[str], diff: str = "") -> str:
    """Build a short human-readable summary for a commit subject."""
    symbols = extract_symbols(diff)
    if symbols:
        return "add " + ", ".join(symbols)
    if not files:
        return "update"
    names = [f.replace("\\", "/").rsplit("/", 1)[-1] for f in files]
    if len(files) == 1:
        return f"update {names[0]}"
    if len(files) <= 3:
        return "update " + ", ".join(names)
    dirs = {f.replace("\\", "/").rsplit("/", 1)[0] for f in files}
    if len(dirs) == 1:
        parent = dirs.pop()
        return f"update {len(files)} files in {parent}"
    return f"update {len(files)} files"
