#!/usr/bin/env python3
"""gate_log_rebuild.py — 从 eco.db 重建可读的 gate_log 账本。

背景
----
2026-10-22 的审计发现：`memories/gate_log/` 有 74 个 md 文件，其中 **31 个只有 30 字节**
（只剩标题）。原因是 v2.5.2 修复前的缺陷——当天最后一轮若没有候选，
`atomic_write` 会用空报告覆盖整天的 md。

而 `eco.db` 的 `gate_log` 表保留了完整历史（116 行 / 26 天）。
所以**可以补回**。

设计原则
--------
1. **只读 db，不写 db**（gate_log 表是权威源，md 只是人读视图）
2. **默认 dry-run**：先看会改什么，加 --apply 才真写
3. **不覆盖有内容的文件**：只处理"极小/空壳"或用户显式 --force 的情况
4. **幂等**：重复跑结果一致（重建是确定性映射，不是追加）
5. **可回滚**：--apply 前把原文件备份到 `<logdir>/_rebuild_backup_<时间戳>/`

用法
----
    python gate_log_rebuild.py                    # 只看报告，不写
    python gate_log_rebuild.py --apply            # 真写（含备份）
    python gate_log_rebuild.py --apply --force    # 连有内容的文件也重写
    python gate_log_rebuild.py --min-bytes 60     # 自定义"空壳"阈值
"""
from __future__ import annotations

import argparse
import datetime
import io
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


HERMES = Path(r"<USER_HOME>\AppData\Local\hermes")
DB = HERMES / "eco.db"
LOGDIR = HERMES / "memories" / "gate_log"

ACTION_ORDER = {"CONFLICT": 0, "ADD": 1, "UPDATE": 2, "NOOP": 3, "idle": 4, "SKIP": 5}


def load_rows(db: Path) -> dict[str, list[tuple]]:
    """按日期分组读出 db 里的账本行。只读。"""
    if not db.is_file():
        raise SystemExit(f"找不到 {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute("SELECT ts, action, target, note FROM gate_log ORDER BY ts")
        by_date: dict[str, list[tuple]] = {}
        for ts, action, target, note in cur.fetchall():
            day = str(ts)[:10]
            by_date.setdefault(day, []).append((ts, action, target, note))
        return by_date
    finally:
        con.close()


def render(day: str, rows: list[tuple]) -> str:
    """把某天的 db 行渲染成 md（与 write_gate 的格式兼容）。"""
    lines = [f"# 写入整合门 {day}（由 eco.db 重建）", ""]
    # 按动作优先级 + 时间排序，让 CONFLICT 靠前（人事后回看最关心失效）
    ordered = sorted(rows, key=lambda r: (ACTION_ORDER.get(str(r[1]), 9), str(r[0])))
    for ts, action, target, note in ordered:
        t = str(ts)[11:19] if len(str(ts)) >= 19 else ""
        bits = [f"- {str(action):8} {t}"]
        if target:
            bits.append(f"→ {target}")
        if note:
            bits.append(f"[{note}]")
        lines.append(" ".join(bits))
    return "\n".join(lines) + "\n"


def main() -> int:
    from lib.cli_base import bootstrap as _bootstrap   # v2.5.0：编码兜底
    _bootstrap()
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写（默认只报告）")
    ap.add_argument("--force", action="store_true", help="连有内容的文件也重写")
    ap.add_argument("--min-bytes", type=int, default=60, help="小于此字节视为空壳（默认 60）")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--logdir", default=str(LOGDIR))
    args = ap.parse_args()

    db = Path(args.db)
    logdir = Path(args.logdir)
    if not logdir.is_dir():
        raise SystemExit(f"找不到日志目录 {logdir}")

    by_date = load_rows(db)
    print("=" * 76)
    print("gate_log 账本重建")
    print("=" * 76)
    print(f"  db      : {db}")
    print(f"  logdir  : {logdir}")
    print(f"  db 覆盖 : {len(by_date)} 天 / {sum(len(v) for v in by_date.values())} 行")
    print(f"  模式    : {'APPLY（会写盘）' if args.apply else 'DRY-RUN（只报告）'}")
    print()

    existing_daily = {p.stem: p for p in logdir.glob("gate-????-??-??.md")}
    plan: list[tuple[str, Path, str, str]] = []   # (kind, path, day, reason)

    for day, rows in sorted(by_date.items()):
        p = existing_daily.get(f"gate-{day}")
        new_text = render(day, rows)
        if p is None:
            plan.append(("create", logdir / f"gate-{day}.md", day, "db 有记录但无 md"))
            continue
        size = p.stat().st_size
        old = p.read_text(encoding="utf-8", errors="replace")
        old_n = len([l for l in old.splitlines() if l.strip().startswith("- ")])
        new_n = len(rows)
        if size < args.min_bytes:
            plan.append(("rebuild", p, day, f"空壳 {size}B → {new_n} 条（db）"))
        elif args.force and old_n < new_n:
            plan.append(("rebuild", p, day, f"md {old_n} 条 < db {new_n} 条"))
        elif old_n < new_n:
            plan.append(("skip-partial", p, day, f"md {old_n} 条 < db {new_n} 条（需 --force）"))
        else:
            plan.append(("ok", p, day, f"md {old_n} 条 ≥ db {new_n} 条"))

    kinds = {}
    for k, *_ in plan:
        kinds[k] = kinds.get(k, 0) + 1
    print(f"  计划：{kinds}")
    print()

    todo = [x for x in plan if x[0] in ("create", "rebuild")]
    for k, p, day, why in plan:
        mark = {"create": "＋新建", "rebuild": "✎重建", "skip-partial": "·跳过", "ok": "✓无需"}[k]
        print(f"  {mark}  {day}  {why}")

    # db 里没有但 md 里有的（历史更早的日期）——不删，只提示
    db_days = set(by_date)
    md_only = [d for d in existing_daily if d.replace("gate-", "") not in db_days]
    if md_only:
        print()
        print(f"  ⚠️ md 有但 db 无对应日期的文件 {len(md_only)} 个（不处理，仅提示）：")
        for d in sorted(md_only)[:8]:
            print(f"      {d}.md")

    if not args.apply:
        print()
        print(f"  DRY-RUN：将改动 {len(todo)} 个文件。加 --apply 执行。")
        return 0

    if not todo:
        print()
        print("  无需改动。")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bakdir = logdir / f"_rebuild_backup_{stamp}"
    bakdir.mkdir(parents=True, exist_ok=True)
    print()
    print(f"  备份目录: {bakdir}")
    written = 0
    for k, p, day, why in todo:
        if p.exists():
            shutil.copy2(p, bakdir / p.name)
        text = render(day, by_date[day])
        # 原子写（复用项目自己的实现，保证与生产一致）
        try:
            sys.path.insert(0, r"<USER_HOME>\memory-ecology-compat\src\memory_ecology")
            from lib.fs import atomic_write
            atomic_write(p, text)
        except Exception:
            fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".rebuild_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            os.replace(tmp, str(p))
        written += 1
        print(f"    ✎ {p.name}  ← {why}")
    print()
    print(f"  已写 {written} 个文件；原文件备份在 {bakdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
