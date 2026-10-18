#!/usr/bin/env python3
"""check_entry_discipline.py — 入口纪律门禁（v2.5.0 新增）。

背景
----
2026-10-18 的用户模拟测试发现：26 个 CLI 脚本各自实现编码/数据根/加锁/建目录，
四道门"四项指标没有一项是齐的"，直接导致 7 个用户可见的 Blocker：

  · 22 个脚本没有 stdout 编码兜底 → 中文 Windows 下 print emoji 崩溃，
    且 write_gate 崩在收尾位置，造成"数据已落盘却 exit 1"的假失败
  · eco_review 自算 __file__.parent.parent → 无视 MEMORY_ECOLOGY_ROOT，
    同一生态被拆成两个数据根
  · eco_quota 锁文件 os.open 不建父目录 → 自定义数据根必崩

这些都不是架构问题，是**入口纪律不一致**。人工记得不可靠，所以做成门禁。

检查项
------
  D1 编码：CLI 脚本必须在 main() 里调用 bootstrap()（或自带 reconfigure）
  D2 数据根：禁止 `Path(__file__)...parent.parent` 自算 root
  D3 建目录：写文件前必须确保父目录存在（裸 open/write 需有同函数内 mkdir/ensure）
  D4 加锁：用 acquire_lock 的必须成对 release（或在 finally 里）

用法
----
    python tools/check_entry_discipline.py            # 检查（默认目录 src/memory_ecology）
    python tools/check_entry_discipline.py --fix-hint # 附修法提示
    python tools/check_entry_discipline.py --json     # 机器可读

退出码：0 = 全过；1 = 有违规（可直接用于 CI）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ── 规则 ──────────────────────────────────────────────────────────────────

#: 不是 CLI 入口、无需检查的文件（库、包初始化、纯数据脚本）
SKIP = {
    "__init__.py",
    "cli_base.py",      # 纪律的实现者本身
}

#: 明确的例外（有理由）
EXEMPT_ROOT = {
    # 门禁自己就是"按目录找脚本"的工具，它的 __file__ 推导不涉及数据根
    "check_entry_discipline.py",
    # 这是安装期一次性脚本，自己就是"找安装位置"的实现
    "fetch_embed_models.py",
}

SELF_ROOT_RE = re.compile(r"Path\(\s*__file__\s*\)[^\n]*parent\.parent")
RECONF_RE = re.compile(r"\.reconfigure\s*\(")
BOOTSTRAP_RE = re.compile(r"\bbootstrap\s*\(|import\s+bootstrap\s+as|\b_bootstrap\s*\(")


def _is_cli(src: str) -> bool:
    """有 argparse 或 main() 就算 CLI 入口。"""
    return "argparse" in src or re.search(r"^def main\s*\(", src, re.M) is not None


def check_file(path: Path, hint: bool = False) -> list[dict]:
    issues: list[dict] = []
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [{"file": path.name, "rule": "READ", "msg": str(e)}]

    if path.name in SKIP or not _is_cli(src):
        return issues

    # D1 编码
    if not (RECONF_RE.search(src) or BOOTSTRAP_RE.search(src)):
        issues.append({
            "file": path.name, "rule": "D1",
            "msg": "缺 stdout 编码兜底（中文 Windows 下 print emoji 会崩）",
            "hint": "main() 第一行加 cli_base.bootstrap()" if hint else "",
        })

    # D2 自算数据根（先剥注释/docstring，否则"修复说明里提到旧写法"会误报）
    code_only = "\n".join(
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    )
    code_only = re.sub(r'"""(?:.|\n)*?"""', "", code_only)
    code_only = re.sub(r"'''(?:.|\n)*?'''", "", code_only)
    if path.name not in EXEMPT_ROOT and SELF_ROOT_RE.search(code_only):
        issues.append({
            "file": path.name, "rule": "D2",
            "msg": "用 Path(__file__)...parent.parent 自算数据根，会无视 MEMORY_ECOLOGY_ROOT",
            "hint": "改用 cli_base.resolve_root()" if hint else "",
        })

    # D3 写文件不建目录
    # 只在"写的是长期目标（非临时文件）"且"全文件无 mkdir/ensure_"时报。
    # 写临时文件（.tmp / mkstemp）不报：其父目录必然是目标目录，已存在，
    # 且写入方通常后面紧跟 replace —— 这不是缺口（实测 3 处全是这种形态）。
    tmp_write = re.search(r'\.tmp["\')]|mkstemp|with_suffix\(\s*["\']\.md\.tmp', src)
    long_write = re.search(r"\.write_text\s*\(", src)
    if long_write and not tmp_write and "mkdir" not in src and "ensure_" not in src:
        issues.append({
            "file": path.name, "rule": "D3",
            "msg": "有 write_text 但全文件无 mkdir/ensure_，目标目录不存在时会崩",
            "hint": "用 cli_base.ensure_parent() 或 atomic_write" if hint else "",
        })

    # D4 加锁成对
    if "acquire_lock" in src and not ("release_lock" in src or "unlink" in src):
        issues.append({
            "file": path.name, "rule": "D4",
            "msg": "acquire_lock 但没 release_lock —— 锁会永久残留",
            "hint": "用 cli_base.locked() 上下文管理器" if hint else "",
        })

    return issues


def main() -> int:
    # v2.5.0（OCR B11）：门禁自己必须先做编码兜底 —— 否则中文 Windows（GBK）下
    # 打印 emoji 会抛 UnicodeEncodeError 并以 exit=1 结束，CI 会读成"有违规"。
    for _s in (sys.stdout, sys.stderr):
        _r = getattr(_s, "reconfigure", None)
        if _r is not None:
            try:
                _r(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    ap = argparse.ArgumentParser(description="入口纪律门禁")
    ap.add_argument("--dir", default=None, help="要检查的目录（默认 <脚本>/../src/memory_ecology）")
    ap.add_argument("--fix-hint", action="store_true", help="附修法提示")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    # v2.5.0：默认目录要兼容两种布局——
    #   发布树：<repo>/src/memory_ecology/check_entry_discipline.py  → 检查自己所在目录
    #   开发树：<repo>/scripts/check_entry_discipline.py             → 也是自己所在目录
    # 原实现固定拼 parent.parent/"src"/"memory_ecology"，在发布树里会多算一层 src。
    if args.dir:
        target = Path(args.dir)
    else:
        here = Path(__file__).resolve().parent
        target = here if (here / "write_gate.py").is_file() else (here.parent / "src" / "memory_ecology")
    if not target.is_dir():
        print(f"错误: 目录不存在 {target}", file=sys.stderr)
        return 1

    all_issues: list[dict] = []
    checked = 0
    for p in sorted(target.glob("*.py")):
        if p.name.startswith("test_"):
            continue
        checked += 1
        all_issues.extend(check_file(p, hint=args.fix_hint))

    if args.json:
        print(json.dumps({"checked": checked, "issues": all_issues},
                         ensure_ascii=False, indent=2))
    else:
        print(f"检查 {checked} 个脚本（{target}）")
        if not all_issues:
            print("[OK] 入口纪律全部通过")
        else:
            for it in all_issues:
                print(f"  [FAIL] [{it['rule']}] {it['file']}: {it['msg']}")
                if it.get("hint"):
                    print(f"       → {it['hint']}")
            print(f"\n共 {len(all_issues)} 个问题")
    return 0 if not all_issues else 1


if __name__ == "__main__":
    sys.exit(main())
