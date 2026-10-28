"""version —— 版本号单源与三处对齐自检。

A2A §B5 的要求是「版本号三处对齐，这次要机制化（一条命令校验），别再手改三处」。
本模块就是那条命令的实现：

    python -m memory_ecology version

对齐面（缺一即红）：
  1. `src/memory_ecology/__init__.py: __version__`  ← **唯一权威**
  2. `pyproject.toml: project.version`
  3. `plugin/package.json: version`
  4. `VERSION.md` 顶部第一个 `**vX.Y.Z**` 条目
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import __version__


def _find_repo_root() -> Path:
    """从本文件往上找同时含 `pyproject.toml` 与 `src/memory_ecology` 的目录。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "memory_ecology").is_dir():
            return parent
    return here.parents[2]


def collect(repo: Path | None = None) -> dict:
    repo = Path(repo) if repo else _find_repo_root()
    out: dict[str, str | None] = {"code": __version__}

    py = repo / "pyproject.toml"
    out["pyproject"] = None
    if py.is_file():
        m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', py.read_text(encoding="utf-8",
                                                                       errors="replace"))
        out["pyproject"] = m.group(1) if m else None

    pkg = repo / "plugin" / "package.json"
    out["plugin"] = None
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            out["plugin"] = str(data.get("version") or "") or None
        except (ValueError, TypeError):
            out["plugin"] = None

    ver = repo / "VERSION.md"
    out["VERSION.md"] = None
    if ver.is_file():
        m = re.search(r"\*\*v(\d+\.\d+\.\d+[^*]*)\*\*",
                      ver.read_text(encoding="utf-8", errors="replace"))
        if m:
            out["VERSION.md"] = m.group(1).split("（")[0].strip()

    out["repo"] = str(repo)
    return out


def report(root=None) -> dict:
    """→ {ok, version, places{...}, mismatched[]}。`ok=False` 时调用方应阻断发布。"""
    data = collect()
    want = str(data["code"])
    places = {
        "src/memory_ecology/__init__.py": data["code"],
        "pyproject.toml": data["pyproject"],
        "plugin/package.json": data["plugin"],
        "VERSION.md": None,
    }
    mismatched = [k for k, v in places.items() if v is not None and not str(v).startswith(want)]
    # VERSION.md 允许形如 "3.2.0" 后跟日期，故单独比
    if data["VERSION.md"]:
        places["VERSION.md"] = data["VERSION.md"]
        if not data["VERSION.md"].startswith(want):
            mismatched.append("VERSION.md")
    missing = [k for k, v in places.items() if v is None]
    return {
        "ok": not mismatched and not missing,
        "version": want,
        "places": places,
        "mismatched": mismatched,
        "missing": missing,
        "repo": data["repo"],
        "root": str(root.path) if root is not None else "",
    }
