"""Run the project's linter and record the finding count.

Reporting only by default - autofix is opt-in via `garden day --fix` because a
bot silently rewriting source is a good way to break a working tree.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from .base import Artifact, TaskContext, register

TIMEOUT = 300


def _detect(ctx: TaskContext):
    root = ctx.repo_path
    pyproject = os.path.join(root, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            if "[tool.ruff]" in open(pyproject, encoding="utf-8").read() and shutil.which("ruff"):
                return ("ruff", ["ruff", "check", "."], "pyproject.toml")
        except Exception:
            pass
    if os.path.isfile(os.path.join(root, ".ruff.toml")) and shutil.which("ruff"):
        return ("ruff", ["ruff", "check", "."], ".ruff.toml")
    if os.path.isfile(os.path.join(root, ".flake8")) and shutil.which("flake8"):
        return ("flake8", ["flake8", "."], ".flake8")
    pkg = os.path.join(root, "package.json")
    if os.path.isfile(pkg):
        try:
            import json

            data = json.load(open(pkg, encoding="utf-8"))
            scripts = data.get("scripts") or {}
            for key in ("lint", "lint:check", "typecheck"):
                if key in scripts:
                    return ("npm", ["npm", "run", key, "--silent"], "package.json")
        except Exception:
            pass
    if os.path.isfile(os.path.join(root, "Cargo.toml")) and shutil.which("cargo"):
        return ("cargo", ["cargo", "clippy", "--quiet"], "Cargo.toml")
    if os.path.isfile(os.path.join(root, ".eslintrc.json")) or os.path.isdir(os.path.join(root, ".eslintrc")):
        if shutil.which("npx"):
            return ("eslint", ["npx", "--no-install", "eslint", "."], ".eslintrc")
    return (None, None, None)


@register
def lint_task():
    class LintTask:
        name = "lint"
        description = "Run the project's linter and record the finding count."

        def run(self, ctx: TaskContext) -> list[Artifact]:
            label, argv, marker = _detect(ctx)
            if not argv or shutil.which(argv[0]) is None:
                return []
            try:
                proc = subprocess.run(
                    argv, cwd=ctx.repo_path, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=TIMEOUT,
                )
                output = (proc.stdout or "") + (proc.stderr or "")
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                output = f"timeout after {TIMEOUT}s"
                exit_code = -1
            except Exception as exc:  # noqa: BLE001
                output = f"could not run: {exc}"
                exit_code = -1

            issues = len(re.findall(r"^\S+:\d+:\d+:", output, re.M))
            errors = len(re.findall(r"\berror\b", output, re.I))
            warnings = len(re.findall(r"\bwarning\b", output, re.I))
            body = "\n".join(
                [
                    f"# {ctx.repo_name} - lint report {ctx.date.isoformat()}",
                    "",
                    f"- **Tool**: `{label}` (detected via `{marker}`)",
                    f"- **Exit code**: {exit_code}",
                    f"- **Reported findings**: {issues}",
                    f"- **Errors**: {errors}  **Warnings**: {warnings}",
                    "",
                    "## Output",
                    "",
                    "```",
                    output[-8000:].strip() or "(no output)",
                    "```",
                    "",
                ]
            )
            rel = ctx.op_path("lint", f"{ctx.date.isoformat()}.md")
            return [Artifact(rel, body, f"chore(ops): record {ctx.repo_name} lint report",
                            "lint report")]

    return LintTask()
