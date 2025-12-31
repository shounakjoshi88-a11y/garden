"""Repository metrics: size, language mix, and a daily history row.

This is the workhorse task. It always produces a diff (the day changes), so it
guarantees the ops repository receives a daily commit with meaningful content
rather than an empty marker.
"""

from __future__ import annotations

import os

from .. import gitutil
from .base import Artifact, TaskContext, register

LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".jsx": "JavaScript", ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin", ".c": "C", ".h": "C/C++ header", ".cpp": "C++",
    ".hpp": "C++", ".cs": "C#", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
    ".php": "PHP", ".swift": "Swift", ".m": "Objective-C", ".scala": "Scala",
    ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell", ".sql": "SQL",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".sass": "Sass",
    ".vue": "Vue", ".svelte": "Svelte", ".dart": "Dart", ".r": "R",
    ".ipynb": "Notebook", ".json": "JSON", ".yml": "YAML", ".yaml": "YAML",
    ".md": "Markdown", ".txt": "Text", ".xml": "XML", ".toml": "TOML",
    ".gradle": "Gradle", ".proto": "Protobuf", ".lua": "Lua", ".gd": "GDScript",
}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build",
    ".next", ".nuxt", "target", "vendor", ".idea", ".vscode", ".gradle", ".cache",
    "site-packages", ".mypy_cache", ".pytest_cache", ".ruff_cache", "coverage",
    ".tox", ".eggs", "htmlcov", ".next-cache", "out",
}

TODO_MARKERS = ("TODO", "FIXME", "HACK", "XXX", "BUG:", "OPTIMIZE")
TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".c", ".h", ".cpp",
            ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".scala", ".sh", ".sql",
            ".html", ".css", ".scss", ".vue", ".svelte", ".dart", ".yml", ".yaml",
            ".md", ".toml", ".json", ".txt", ".gradle", ".lua"}


def _walk(root: str, limit_bytes: int = 400_000):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git")]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > limit_bytes:
                    continue
            except OSError:
                continue
            yield path


def _scan(root: str) -> tuple[dict[str, int], int, int, list[tuple[str, str, int]]]:
    per_lang: dict[str, int] = {}
    files = 0
    lines = 0
    todos: list[tuple[str, str, int]] = []
    for path in _walk(root):
        ext = os.path.splitext(path)[1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".mp4", ".woff", ".ttf"}:
            continue
        files += 1
        lang = LANG_BY_EXT.get(ext, "Other")
        count = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    count += 1
                    if ext in TEXT_EXT:
                        upper = line.upper()
                        for marker in TODO_MARKERS:
                            if marker in upper:
                                rel = os.path.relpath(path, root).replace("\\", "/")
                                todos.append((rel, line.strip()[:110], lineno))
                                break
        except OSError:
            pass
        lines += count
        per_lang[lang] = per_lang.get(lang, 0) + count
    return per_lang, files, lines, todos


def _fmt_table(rows: list[tuple[str, int]]) -> str:
    if not rows:
        return "_No source files found._"
    width = max(len(r[0]) for r in rows)
    out = ["| Language | Lines |", "| --- | ---: |"]
    for lang, count in rows:
        out.append(f"| {lang:<{width}} | {count:,} |")
    return "\n".join(out)


@register
def metrics_task():
    class MetricsTask:
        name = "metrics"
        description = "Track repository size, language mix and a daily history row."

        def run(self, ctx: TaskContext) -> list[Artifact]:
            per_lang, files, lines, todos = _scan(ctx.repo_path)
            commits = len(gitutil.log(ctx.repo_path, limit=1000))
            commit_note = " (shallow clone - partial count)" if ctx.shallow else ""
            top = sorted(per_lang.items(), key=lambda kv: -kv[1])[:10]

            md_rel = ctx.op_path("metrics", f"{ctx.date.isoformat()}.md")
            md_content = "\n".join(
                [
                    f"# {ctx.repo_name} - metrics {ctx.date.isoformat()}",
                    "",
                    "| Metric | Value |",
                    "| --- | ---: |",
                    f"| Tracked files | {files:,} |",
                    f"| Lines of code | {lines:,} |",
                    f"| Open TODOs | {len(todos):,} |",
                    f"| Commits in history | {commits:,}{commit_note} |",
                    "",
                    "## Language mix",
                    "",
                    _fmt_table(top),
                    "",
                ]
            )
            return [
                Artifact(md_rel, md_content, f"chore(ops): record {ctx.repo_name} metrics snapshot",
                         "daily metrics snapshot"),
            ]

    return MetricsTask()


@register
def todos_task():
    class TodosTask:
        name = "todos"
        description = "Inventory TODO/FIXME markers with file and line numbers."

        def run(self, ctx: TaskContext) -> list[Artifact]:
            _per_lang, _files, _lines, todos = _scan(ctx.repo_path)
            rel = ctx.op_path("todos", f"{ctx.date.isoformat()}.md")
            if not todos:
                body = f"# {ctx.repo_name} - TODO inventory {ctx.date.isoformat()}\n\n_Clean. No open TODO markers._\n"
            else:
                lines = [
                    f"# {ctx.repo_name} - TODO inventory {ctx.date.isoformat()}",
                    "",
                    f"{len(todos)} open marker(s) across the tree.",
                    "",
                    "| File | Line | Marker |",
                    "| --- | ---: | --- |",
                ]
                for path, text, lineno in sorted(todos)[:300]:
                    clean = text.replace("|", "\\|")
                    lines.append(f"| `{path}` | {lineno} | {clean} |")
                body = "\n".join(lines) + "\n"
            return [Artifact(rel, body, f"chore(ops): refresh {ctx.repo_name} TODO inventory",
                            "TODO inventory")]

    return TodosTask()
