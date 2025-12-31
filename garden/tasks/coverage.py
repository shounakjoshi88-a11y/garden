"""Coverage summary when the project already produces one."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

from .base import Artifact, TaskContext, register


def _from_coverage_xml(path: str) -> dict | None:
    try:
        tree = ET.parse(path)
    except Exception:
        return None
    root = tree.getroot()
    line_rate = root.get("line-rate")
    branch_rate = root.get("branch-rate")
    lines_covered = root.get("lines-covered")
    lines_valid = root.get("lines-valid")
    return {
        "line_rate": float(line_rate) if line_rate else None,
        "branch_rate": float(branch_rate) if branch_rate else None,
        "lines_covered": int(lines_covered) if lines_covered else None,
        "lines_valid": int(lines_valid) if lines_valid else None,
    }


def _from_coverage_file(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", text)
    if not m:
        m = re.search(r"Total.*?(\d+)%", text, re.S)
    if not m:
        return None
    return {"line_rate": float(m.group(1)) / 100.0}


@register
def coverage_task():
    class CoverageTask:
        name = "coverage"
        description = "Summarise the coverage report if the project already emits one."

        def run(self, ctx: TaskContext) -> list[Artifact]:
            root = ctx.repo_path
            data = None
            source = ""
            for candidate in ("coverage.xml", "htmlcov/coverage.xml", "coverage/coverage.xml"):
                full = os.path.join(root, candidate)
                if os.path.isfile(full):
                    data = _from_coverage_xml(full)
                    source = candidate
                    if data:
                        break
            if not data:
                for candidate in (".coverage", "coverage.txt", "coverage.out"):
                    full = os.path.join(root, candidate)
                    if os.path.isfile(full):
                        data = _from_coverage_file(full)
                        source = candidate
                        if data:
                            break
            if not data or not data.get("line_rate"):
                return []

            pct = data["line_rate"] * 100
            body = "\n".join(
                [
                    f"# {ctx.repo_name} - coverage {ctx.date.isoformat()}",
                    "",
                    f"- **Line coverage**: {pct:.1f}%",
                    (f"- **Branch coverage**: {data['branch_rate'] * 100:.1f}%"
                     if data.get("branch_rate") is not None else "- **Branch coverage**: n/a"),
                    (f"- **Covered / valid lines**: {data['lines_covered']:,} / {data['lines_valid']:,}"
                     if data.get("lines_valid") else ""),
                    f"- **Source**: `{source}`",
                    "",
                ]
            )
            rel = ctx.op_path("coverage", f"{ctx.date.isoformat()}.md")
            return [Artifact(rel, body, f"chore(ops): record {ctx.repo_name} coverage snapshot",
                            "coverage snapshot")]

    return CoverageTask()
