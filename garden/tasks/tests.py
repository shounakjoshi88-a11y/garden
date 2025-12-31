"""Run the repository's own test suite and record the result."""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from .base import Artifact, TaskContext, register

TIMEOUT = 420


def _which(name: str) -> str | None:
    return shutil.which(name)


def _detect(ctx: TaskContext):
    """Return (label, argv) for the test runner this project uses."""
    root = ctx.repo_path
    pkg = os.path.join(root, "package.json")
    if os.path.isfile(pkg):
        try:
            import json

            data = json.load(open(pkg, encoding="utf-8"))
            scripts = data.get("scripts") or {}
            if "test" in scripts:
                return ("npm", ["npm", "test", "--silent"], "package.json")
        except Exception:
            pass
    if os.path.isfile(os.path.join(root, "go.mod")):
        return ("go", ["go", "test", "./..."], "go.mod")
    if os.path.isfile(os.path.join(root, "Cargo.toml")):
        return ("cargo", ["cargo", "test", "--quiet"], "Cargo.toml")
    if os.path.isfile(os.path.join(root, "pom.xml")):
        return ("maven", ["mvn", "-q", "test"], "pom.xml")
    for marker in ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"):
        if os.path.isfile(os.path.join(root, marker)):
            if _which("pytest") or shutil.which("python"):
                return ("pytest", ["python", "-m", "pytest", "-q"], marker)
    if os.path.isdir(os.path.join(root, "tests")) or os.path.isdir(os.path.join(root, "test")):
        return ("pytest", ["python", "-m", "pytest", "-q"], "tests/")
    return (None, None, None)


def _summarise(output: str) -> dict:
    passed = failed = skipped = 0
    m = re.search(r"(\d+)\s+passed", output)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", output)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+)\s+skipped", output)
    if m:
        skipped = int(m.group(1))
    if not (passed or failed or skipped):
        m = re.search(r"Tests:\s+(\d+)\s+passed,\s+(\d+)\s+failed", output)
        if m:
            passed, failed = int(m.group(1)), int(m.group(2))
    m = re.search(r"^ok\s+(\S+)", output, re.M)
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "packages_ok": len(re.findall(r"^ok\s+", output, re.M))}


@register
def tests_task():
    class TestsTask:
        name = "tests"
        description = "Run the project test suite and store the outcome."

        def run(self, ctx: TaskContext) -> list[Artifact]:
            label, argv, marker = _detect(ctx)
            if not argv:
                return []

            if shutil.which(argv[0]) is None and argv[0] not in ("python", "npm"):
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

            stats = _summarise(output)
            status = "PASS" if exit_code == 0 else "FAIL"
            body = "\n".join(
                [
                    f"# {ctx.repo_name} - test run {ctx.date.isoformat()}",
                    "",
                    f"- **Status**: {status}",
                    f"- **Runner**: `{label}` (detected via `{marker}`)",
                    f"- **Exit code**: {exit_code}",
                    f"- **Passed**: {stats['passed']}  **Failed**: {stats['failed']}  "
                    f"**Skipped**: {stats['skipped']}",
                    "",
                    "## Output",
                    "",
                    "```",
                    output[-8000:].strip() or "(no output)",
                    "```",
                    "",
                ]
            )
            rel = ctx.op_path("tests", f"{ctx.date.isoformat()}.md")
            verb = "pass" if status == "PASS" else "fail"
            return [Artifact(rel, body, f"chore(ops): record {ctx.repo_name} test {verb}",
                            "test run report")]

    return TestsTask()
