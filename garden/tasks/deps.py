"""Dependency inventory with a best-effort outdated check.

The check is advisory: it never edits a manifest, it only reports what it can
see. Network failures degrade to a plain inventory instead of breaking the run.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .base import Artifact, TaskContext, register

TIMEOUT = 12


def _pypi_latest(pkg: str) -> str | None:
    try:
        url = f"https://pypi.org/pypi/{pkg}/json"
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("info", {}).get("version")
    except Exception:
        return None


def _npm_latest(pkg: str) -> str | None:
    name = pkg.split("@")[0] if not pkg.startswith("@") else pkg
    try:
        url = f"https://registry.npmjs.org/{name}/latest"
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("version")
    except Exception:
        return None


def _python_deps(root: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name in ("requirements.txt", "requirements-dev.txt", "pyproject.toml"):
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        if name == "pyproject.toml":
            block = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
            if not block:
                continue
            for entry in re.findall(r"[\"']([^\"']+)[\"']", block.group(1)):
                out.append((entry.split(">=")[0].split("==")[0].split(">")[0].strip(), entry))
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                name_only = re.split(r"[><=!~\[]", line)[0].strip()
                if name_only:
                    out.append((name_only, line))
    return out


def _npm_deps(root: str) -> list[tuple[str, str]]:
    path = os.path.join(root, "package.json")
    if not os.path.isfile(path):
        return []
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    deps = {}
    deps.update(data.get("dependencies") or {})
    deps.update(data.get("devDependencies") or {})
    return [(k, v) for k, v in sorted(deps.items())]


@register
def deps_task():
    class DepsTask:
        name = "deps"
        description = "Inventory declared dependencies and flag outdated ones."

        def run(self, ctx: TaskContext) -> list[Artifact]:
            rows: list[tuple[str, str, str, str]] = []
            for pkg, spec in _python_deps(ctx.repo_path):
                latest = _pypi_latest(pkg)
                rows.append(("PyPI", pkg, spec, latest or "unknown"))
            for pkg, spec in _npm_deps(ctx.repo_path):
                latest = _npm_latest(pkg)
                rows.append(("npm", pkg, spec, latest or "unknown"))

            rel = ctx.op_path("deps", f"{ctx.date.isoformat()}.md")
            if not rows:
                return []

            def _is_outdated(spec: str, latest: str) -> bool:
                if latest in ("unknown", ""):
                    return False
                m = re.search(r"(\d+(?:\.\d+)+)", spec)
                if not m:
                    return False
                current = m.group(1)
                return [int(x) for x in current.split(".")[:3]] < [
                    int(x) for x in re.findall(r"\d+", latest)[:3]
                ] if re.findall(r"\d+", latest) else False

            outdated = [r for r in rows if _is_outdated(r[2], r[3])]
            lines = [
                f"# {ctx.repo_name} - dependency check {ctx.date.isoformat()}",
                "",
                f"{len(rows)} declared dependency/dependencies, {len(outdated)} behind latest.",
                "",
                "| Registry | Package | Declared | Latest |",
                "| --- | --- | --- | --- |",
            ]
            for registry, pkg, spec, latest in rows:
                mark = " ⚠️" if _is_outdated(spec, latest) else ""
                lines.append(f"| {registry} | `{pkg}` | `{spec}` | `{latest}`{mark} |")
            body = "\n".join(lines) + "\n"
            return [Artifact(rel, body, f"chore(ops): record {ctx.repo_name} dependency audit",
                            "dependency audit")]

    return DepsTask()
