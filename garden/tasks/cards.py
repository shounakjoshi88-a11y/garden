"""Knowledge cards.

Point `sources` at a folder of markdown notes and garden publishes one per day
into the ops repository. This is the task that turns the graph green with
something you actually wanted to have written down.
"""

from __future__ import annotations

import json
import os
import re

from .base import Artifact, TaskContext, register

CURSOR = "ops/cards/.cursor.json"


def _collect(sources: list[str]) -> list[str]:
    files: list[str] = []
    for src in sources:
        if not src or not os.path.isdir(src):
            continue
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in sorted(filenames):
                if name.lower().endswith((".md", ".markdown")):
                    files.append(os.path.join(dirpath, name))
    return sorted(files)


@register
def cards_task():
    class CardsTask:
        name = "cards"
        description = "Publish one knowledge card per day from a folder of notes."

        def run(self, ctx: TaskContext) -> list[Artifact]:
            files = _collect(ctx.config.sources)
            if not files:
                return []

            raw = gitutil.read_file(ctx.repo_path, CURSOR)
            index = 0
            if raw:
                try:
                    index = int(json.loads(raw).get("index", 0))
                except Exception:
                    index = 0
            if index >= len(files):
                return []

            source = files[index]
            body = open(source, encoding="utf-8", errors="replace").read()
            title = re.search(r"^#\s+(.+)$", body, re.M)
            title_text = title.group(1).strip() if title else os.path.basename(source)

            date_str = ctx.date.isoformat()
            rel = ctx.op_path("cards", f"{date_str}.md")
            content = "\n".join(
                [
                    f"# {title_text}",
                    "",
                    f"_Card {index + 1} of {len(files)} - published {date_str}_",
                    "",
                    body.strip(),
                    "",
                    f"<!-- source: {os.path.basename(source)} -->",
                    "",
                ]
            )
            cursor_content = json.dumps({"index": index + 1, "total": len(files)}, indent=2) + "\n"
            return [
                Artifact(rel, content, f"docs(cards): publish card - {title_text[:60]}", "knowledge card"),
                Artifact(CURSOR, cursor_content, f"chore(ops): advance card cursor to {index + 1}",
                         "card cursor"),
            ]

    return CardsTask()
