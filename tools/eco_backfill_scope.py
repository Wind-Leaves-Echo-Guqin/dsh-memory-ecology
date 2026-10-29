#!/usr/bin/env python3
"""v3.0.1 scope 存量回填（PLAN-v3.0.1 §1.4；骨架复用 a1 的 eco_backfill_origin）。

scope = 候选来源文件名（gate_log.fingerprint = md5("<文件名>|<原句>") 反推的硬证据，
a1 实测 127/127 ADD 可归因）。挤出直写条目（origin 含 extrude）scope 诚实空串。

用法:
  python tools/eco_backfill_scope.py                 # 只读报告
  python tools/eco_backfill_scope.py --apply         # 回填（先备份，幂等）
选项: --root <数据根>（默认 hermes_root()，对真实数据操作必须显式传生产根）

退出码: 0 完成 / 1 apply 出错 / 2 路径错误。SQLite 只读段一律 ?mode=ro。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as e:
    print(f"warning: could not reconfigure stdio to UTF-8: {e!r}", file=sys.stderr)

from lib.config import hermes_root          # noqa: E402
from lib.memstore import parse_frontmatter, dump_frontmatter  # noqa: E402


def build_fingerprints(pending: Path) -> dict:
    """指纹 → 候选文件名（消费后改名的历史变体全部纳入；与 a1 同款）。"""
    fps = {}
    for f in pending.iterdir():
        if f.suffix != ".md" or f.name == "README.md":
            continue
        names = {f.name}
        if f.name.endswith(".done.md"):
            names.add(f.name[:-8])
        elif f.name.endswith(".done"):
            names.add(f.name[:-5])
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = ln.strip()
            if raw.startswith("- ["):
                for n in names:
                    fps[hashlib.md5(f"{n}|{raw}".encode()).hexdigest()] = n
    return fps


def read_fm(path: Path):
    return parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="v3.0.1 scope 存量回填（来源文件名指纹反推）")
    ap.add_argument("--apply", action="store_true", help="执行回填（默认只读报告）")
    ap.add_argument("--json", action="store_true", help="机器读输出")
    ap.add_argument("--root", default="", help="数据根（默认 hermes_root()）")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else hermes_root()
    detail = root / "memories" / "detail"
    pending = root / "memories" / "pending"
    db = root / "eco.db"
    if not detail.is_dir() or not db.is_file():
        print(f"❌ 数据根不可用: {root}", file=sys.stderr)
        return 2

    fps = build_fingerprints(pending) if pending.is_dir() else {}
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        add_rows = dict(con.execute(
            "SELECT fingerprint, target FROM gate_log WHERE action IN ('ADD','UPDATE')"))
    finally:
        con.close()

    todo: list[dict] = []
    stats = {"total": 0, "has_scope": 0, "extrude_skip": 0, "no_fingerprint": 0}
    for f in sorted(detail.glob("*.md")):
        stats["total"] += 1
        fm, _ = read_fm(f)
        origin = str(fm.get("origin_session_id", "") or "").strip()
        if "scope" in fm and str(fm.get("scope", "") or "").strip():
            stats["has_scope"] += 1
            continue
        if "extrude" in origin.lower():
            stats["extrude_skip"] += 1   # 挤出直写：scope="" 是诚实值，不伪造
            continue
        slug = f.stem
        # 反向指纹：该 slug 的 ADD/UPDATE 行 → 反查候选文件名
        src = None
        for fp, target in add_rows.items():
            if target == slug and fp in fps:
                src = fps[fp]
                break
        if src:
            todo.append({"slug": slug, "scope": src})
        else:
            stats["no_fingerprint"] += 1

    applied = None
    if args.apply and todo:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = detail / f".scope-bak-{stamp}"
        bak.mkdir(exist_ok=True)
        try:
            for item in todo:
                path = detail / f"{item['slug']}.md"
                fm, body = read_fm(path)
                fm["scope"] = item["scope"]
                (bak / path.name).write_bytes(path.read_bytes())
                # newline="\n"：仓库 LF 纪律
                path.write_text(dump_frontmatter(fm, body), encoding="utf-8", newline="\n")
            applied = len(todo)
        except Exception as e:
            print(f"❌ 回填执行失败: {e}", file=sys.stderr)
            if args.json:
                print(json.dumps({"stats": stats, "todo_n": len(todo), "error": str(e)},
                                 ensure_ascii=False))
            return 1

    report = {"stats": stats, "todo_n": len(todo), "applied": applied,
              "sample": todo[:10]}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(f"== scope 回填报告（{'APPLY' if args.apply else '只读'}）==")
        print(f"detail {stats['total']} 篇：已有 scope {stats['has_scope']} / "
              f"挤出跳过 {stats['extrude_skip']} / 指纹不可反推 {stats['no_fingerprint']} / "
              f"待回填 {len(todo)}")
        for it in report["sample"]:
            print(f"   - {it['slug'][:50]} → {it['scope']}")
        if applied is not None:
            print(f"已回填 {applied} 篇（备份 detail/.scope-bak-*）")
        elif todo:
            print("ℹ️ 带 --apply 执行（先看本报告确认）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
