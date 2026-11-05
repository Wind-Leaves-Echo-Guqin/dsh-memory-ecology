"""version —— 版本号单源与对齐自检（3.4.0 R7 扩面）。

A2A §B5 的要求是「版本号多处对齐，机制化（一条命令校验），别再手改」。
本模块就是那条命令的实现：

    python -m memory_ecology version

对齐面（R7 扩面后；可选位 = 文件存在才查）：
  1. `src/memory_ecology/__init__.py: __version__`  ← **唯一权威**
  2. `pyproject.toml: project.version`
  3. `plugin/package.json: version`
  4. `VERSION.md` 顶部第一个 `**vX.Y.Z**` 条目
  5. `run_tests.py` 横幅 —— **防回潮**：允许动态读取 __version__（无硬编码），
     但若出现硬编码 `记忆生态 vX.Y.Z ·` 且不等于当前版本即红
  6. `integrations/gui-v3/package.json`（可选；发布树 layout 下会从 repo 父目录找）
  7. `CHANGELOG.md` 首条 `## vX.Y.Z`（发版叙事与版本号一致）

S47 修复：比对从 startswith 改为**精确相等**——`3.2.20` / `3.2.2_x` /
`3.2.2-rc.1` 对权威值 `3.2.2` 全部判 mismatch（3.2.20 曾被判 ok）。
README 的版本轴随 CHANGELOG 更新（人读面，不由本模块机械校验）。
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


def _candidates(repo: Path, rel: str) -> list[Path]:
    """可选位查找顺序：repo/ 优先，其次 repo 父目录（发布树 core/ layout）。"""
    return [repo / rel, repo.parent / rel]


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

    # R7 扩面 ①：run_tests 横幅防回潮（找不到硬编码 = 动态化，记 "dynamic"）
    out["run_tests.py"] = "dynamic"
    for cand in _candidates(repo, "run_tests.py"):
        if cand.is_file():
            m = re.search(r"记忆生态 v(\d+\.\d+\.\d+) ·", cand.read_text(encoding="utf-8",
                                                                    errors="replace"))
            out["run_tests.py"] = m.group(1) if m else "dynamic"
            break

    # R7 扩面 ②：gui-v3（可选，发布树才有）
    out["integrations/gui-v3/package.json"] = None
    for cand in _candidates(repo, "integrations/gui-v3/package.json"):
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                out["integrations/gui-v3/package.json"] = str(data.get("version") or "") or None
            except (ValueError, TypeError):
                out["integrations/gui-v3/package.json"] = None
            break

    # R7 扩面 ③：CHANGELOG 首条（可选）
    out["CHANGELOG.md"] = None
    for cand in _candidates(repo, "CHANGELOG.md"):
        if cand.is_file():
            m = re.search(r"(?m)^## v(\d+\.\d+\.\d+)", cand.read_text(encoding="utf-8",
                                                                          errors="replace"))
            out["CHANGELOG.md"] = m.group(1) if m else None
            break

    # R7 扩面 ④（2026-11-05 补）：gui-v3 的**用户可见**版本位。
    # 漏这处是实测教训：package.json 已是 3.4.0，但 index.html 的 `<title>` 与
    # 设置页「关于本机」还写着 v3.2.0，而自检报 ok:true —— 版本漂移就挂在最显眼
    # 的地方（浏览器标签页）。只查 `<title>`（稳定、无 i18n 干扰）。
    out["integrations/gui-v3/index.html"] = None
    for cand in _candidates(repo, "integrations/gui-v3/index.html"):
        if cand.is_file():
            m = re.search(r"<title>[^<]*?v(\d+\.\d+\.\d+)</title>",
                          cand.read_text(encoding="utf-8", errors="replace"))
            out["integrations/gui-v3/index.html"] = m.group(1) if m else None
            break

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
        "run_tests.py": data["run_tests.py"],
        "CHANGELOG.md": data["CHANGELOG.md"],
    }
    if data["integrations/gui-v3/package.json"] is not None:
        places["integrations/gui-v3/package.json"] = data["integrations/gui-v3/package.json"]
    if data["integrations/gui-v3/index.html"] is not None:
        places["integrations/gui-v3/index.html"] = data["integrations/gui-v3/index.html"]
    # VERSION.md 允许形如 "3.4.0" 后跟日期（collect 已截取），单独赋值
    if data["VERSION.md"]:
        places["VERSION.md"] = data["VERSION.md"]
    # S47：精确相等。run_tests.py 的 "dynamic"（无硬编码横幅）视为一致。
    mismatched = [k for k, v in places.items()
                  if v is not None and v != want and not (k == "run_tests.py" and v == "dynamic")]
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
