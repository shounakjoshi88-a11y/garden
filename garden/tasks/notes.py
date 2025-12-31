"""Daily engineering note.

If you wrote `notes/YYYY-MM-DD.md` in the repo, that file is used verbatim -
that is the version worth having. Otherwise a draft is generated from the day's
commits so the slot is never empty, with the parts only you can write left
blank on purpose.
"""

from __future__ import annotations

import os

from .. import gitutil
from .base import Artifact, TaskContext, register

PROMPTS = [
    "What did today actually change, and why?",
    "What broke, and what is the root cause?",
    "What did you learn that you did not know yesterday?",
    "What is the next smallest useful step?",
    "What would you do differently if you started today again?",
    "Which decision today will look obvious in a month?",
    "What is still fuzzy about the code you touched?",
]


@register
def notes_task():
    class NotesTask:
        name = "notes"
        description = "Keep a daily engineering note, authored or drafted from the day's commits."

        def run(self, ctx: TaskContext) -> list[Artifact]:
            date_str = ctx.date.isoformat()
            roots = [ctx.repo_path]
            if ctx.state_path and ctx.state_path not in roots:
                roots.append(ctx.state_path)

            authored = None
            for root in roots:
                for candidate in (
                    f"{ctx.config.notes_dir}/{date_str}.md",
                    f"{root}/{ctx.config.notes_dir}/{date_str}.md",
                ):
                    if os.path.isabs(candidate):
                        if os.path.isfile(candidate):
                            authored = open(candidate, encoding="utf-8", errors="replace").read()
                            break
                    else:
                        full = os.path.join(root, candidate)
                        if os.path.isfile(full):
                            authored = open(full, encoding="utf-8", errors="replace").read()
                            break
                if authored is not None:
                    break

            rel = ctx.op_path("notes", f"{date_str}.md")
            if authored is not None:
                body = f"# {ctx.repo_name} - {date_str}\n\n{authored.strip()}\n"
                message = f"docs(ops): add {ctx.repo_name} note for {date_str}"
                return [Artifact(rel, body, message, "authored daily note")]

            commits = [c for c in gitutil.log(ctx.repo_path, limit=60) if c.date == ctx.date]
            prompt = PROMPTS[ctx.date.toordinal() % len(PROMPTS)]
            topic = ""
            topics = ctx.config.topics
            if topics:
                topic = topics[ctx.date.toordinal() % len(topics)]

            lines = [
                f"# {ctx.repo_name} - {date_str}",
                "",
                "> Drafted by garden. Edit `notes/{date_str}.md` in the repo to author this "
                "entry yourself - an authored note always wins over the draft.",
                "",
                "## Commits today",
                "",
            ]
            if commits:
                for c in commits:
                    lines.append(f"- `{c.sha[:7]}` {c.subject}")
            else:
                lines.append("_No commits recorded on this date._")
            lines += [
                "",
                "## Focus",
                "",
                f"- {prompt}",
            ]
            if topic:
                lines.append(f"- Topic of the day: **{topic}**")
            lines += [
                "",
                "## Notes",
                "",
                "<!-- write it here -->",
                "",
            ]
            body = "\n".join(lines)
            return [Artifact(rel, body, f"docs(ops): draft {ctx.repo_name} note for {date_str}",
                            "daily note draft")]

    return NotesTask()
