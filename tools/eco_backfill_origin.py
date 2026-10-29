#!/usr/bin/env python3
"""a1 溯源回填（v3.0.0a1，审计 P0-3 收口件）。

背景（AUDIT-2026-10-23 P0-3）：detail 存量 170 篇中——
  · 51 篇 origin 为挤出占位符（extrude-from-L1 / manual-extrude-user）：
    门② 挤出直写、绕过门①，gate_log 无任何记录（「账实背离」）；
  · 119 篇 origin 为空：门① 正常 ADD，但候选行契约里没有会话关联，
    session 级溯源信息在捕获时就没落下来（v3.0.0a1 起 sidecar 补上）。

本工具做两件事（全部可 dry-run 报告先行）：
  A. EXTRUDE 补账   —— 有挤出占位符、但 gate_log 无 EXTRUDE 行的条目，
     按 frontmatter transaction_time 补一行 action=EXTRUDE（fingerprint
     = md5("EXTRUDE|slug")，INSERT OR IGNORE 幂等，与前向修复同构）。
  B. 门① 溯源回填  —— origin 为空的条目，按 stem 关联 gate_log 的
     ADD/UPDATE 行（target==stem），把 `gate_log:<action>@<ts>` 写进
     evidence 字段（**不伪造** origin_session_id——会话号确实不可知，
     空串即诚实；这是有据可查的账本级溯源，不是猜出来的会话号）。

明确不做（偏离计划的记录，2026-10-24）：
  · 不把 origin 批量改写成 unknown-legacy——该标记无消费方，空串本就是
    「未知」的诚实表达，不值得 170 篇文件级 churn（selfcheck A4 也不依赖它）。
  · 不回填 evidence 到 quarantine/superseded 条目（只处理 detail/ 下的 active 面）。

用法:
  python tools/eco_backfill_origin.py                 # 只读报告
  python tools/eco_backfill_origin.py --apply         # 执行补账（先备份）
  python tools/eco_backfill_origin.py --json          # 机器读
选项: --root <数据根>（默认 hermes_root()）；--db <eco.db>（默认 <root>/eco.db）

退出码: 0 完成（含「有待补项但未 --apply」）/ 1 --apply 执行出错 / 2 用法/路径错误
设计约束: 与 eco_selfcheck 同款——只读为默认；SQLite 一律 `?mode=ro`（防 WAL
副作用污染数据根），仅 --apply 时以读写打开一次。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as e:  # 与 run_tests 同口径：reconfigure 失败必须留痕
    print(f"warning: could not reconfigure stdio to UTF-8: {e!r}", file=sys.stderr)

from lib.config import hermes_root          # noqa: E402
from lib import pii                          # noqa: E402
from lib.memstore import parse_frontmatter, dump_frontmatter  # noqa: E402

EXTRUDE_ORIGIN_RE = re.compile(r"extrude", re.IGNORECASE)
BACKUP_TAG = "backfill"


def read_fm(path: Path) -> tuple[dict, str]:
    return parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))


def open_ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def scan(detail_dir: Path, db: Path) -> dict:
    """只读扫描：分类 detail 条目并对照 gate_log，返回待办清单与统计。"""
    extrude_todo: list[dict] = []      # 有挤出占位符 + 无 EXTRUDE 行
    gate1_traced: list[dict] = []      # origin 空 + 有 ADD/UPDATE 行可关联
    gate1_untraceable: list[dict] = [] # origin 空 + 无任何账
    stats = {"total": 0, "unparsable": 0}

    con = open_ro(db)
    try:
        extrude_rows = {r[0] for r in con.execute(
            "SELECT target FROM gate_log WHERE action='EXTRUDE'")}
        add_rows: dict[str, tuple[str, str]] = {}
        for ts, action, target in con.execute(
                "SELECT ts, action, target FROM gate_log "
                "WHERE action IN ('ADD','UPDATE') ORDER BY ts ASC"):
            if target and target not in add_rows:
                add_rows[target] = (ts, action)
    finally:
        con.close()

    for f in sorted(detail_dir.glob("*.md")):
        stats["total"] += 1
        try:
            fm, body = read_fm(f)
        except OSError as e:
            stats["unparsable"] += 1
            print(f"⚠️ 读取失败 {f.name}: {e}", file=sys.stderr)
            continue
        origin = str(fm.get("origin_session_id", "") or "").strip()
        stem = f.stem
        if EXTRUDE_ORIGIN_RE.search(origin):
            if stem not in extrude_rows:
                extrude_todo.append({
                    "slug": stem, "origin": origin,
                    "ts": str(fm.get("transaction_time", "") or "").strip(),
                    "body_len": len(body),
                })
        elif not origin:
            hit = add_rows.get(stem)
            if hit and not str(fm.get("evidence", "") or "").strip():
                gate1_traced.append({"slug": stem, "ledger_ts": hit[0], "ledger_action": hit[1]})
            else:
                gate1_untraceable.append({"slug": stem,
                                          "reason": "无账" if not hit else "evidence已有值（已是证据链条目，不覆盖）"})

    return {
        "stats": stats,
        "extrude_total": len(extrude_rows) + len(extrude_todo),
        "extrude_todo": extrude_todo,
        "gate1_traced": gate1_traced,
        "gate1_untraceable": gate1_untraceable,
    }


def apply_extrude_rows(db: Path, todo: list[dict]) -> int:
    """补 EXTRUDE 行（幂等：fingerprint=md5(EXTRUDE|slug)，INSERT OR IGNORE）。"""
    if not todo:
        return 0
    conn = sqlite3.connect(str(db), timeout=10)
    n = 0
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS gate_log("
                     "ts TEXT, fingerprint TEXT PRIMARY KEY, action TEXT, "
                     "target TEXT, note TEXT)")
        for item in todo:
            ts = item.get("ts") or datetime.datetime.now().isoformat(timespec="seconds")
            fp = hashlib.md5(f"EXTRUDE|{item['slug']}".encode("utf-8")).hexdigest()
            cur = conn.execute(
                "INSERT OR IGNORE INTO gate_log(ts, fingerprint, action, target, note) "
                "VALUES(?,?,?,?,?)",
                (ts, fp, "EXTRUDE", item["slug"],
                 pii.mask(f"存量补账 a1：origin={item['origin']}（门②挤出直写历史无账）")))
            n += cur.rowcount if cur.rowcount > 0 else 0
        conn.commit()
    except Exception:
        conn.commit()  # 已写入部分先落账再抛（与 eco_quota.execute 同纪律）
        raise
    finally:
        conn.close()
    return n


def apply_evidence_backfill(detail_dir: Path, traced: list[dict]) -> int:
    """回填 evidence=gate_log:<action>@<ts>（先备份，frontmatter-only，正文不动）。"""
    if not traced:
        return 0
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = detail_dir / f".backfill-bak-{stamp}"
    bak.mkdir(exist_ok=True)
    n = 0
    for item in traced:
        path = detail_dir / f"{item['slug']}.md"
        fm, body = read_fm(path)
        fm["evidence"] = f"gate_log:{item['ledger_action']}@{item['ledger_ts']}"
        (bak / path.name).write_bytes(path.read_bytes())
        # newline="\n"：仓库 LF 纪律（write_text 默认会把 \n 翻成 os.linesep/CRLF）
        path.write_text(dump_frontmatter(fm, body), encoding="utf-8", newline="\n")
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="a1 溯源回填（EXTRUDE 补账 + 门① evidence 关联）")
    ap.add_argument("--apply", action="store_true", help="执行补账（默认只读报告）")
    ap.add_argument("--json", action="store_true", help="机器读输出")
    ap.add_argument("--root", default="", help="数据根（默认 hermes_root()）")
    ap.add_argument("--db", default="", help="eco.db 路径（默认 <root>/eco.db）")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else hermes_root()
    db = Path(args.db) if args.db else root / "eco.db"
    detail_dir = root / "memories" / "detail"
    if not detail_dir.is_dir():
        print(f"❌ detail 目录不存在: {detail_dir}", file=sys.stderr)
        return 2
    if not db.is_file():
        print(f"❌ eco.db 不存在: {db}", file=sys.stderr)
        return 2

    report = scan(detail_dir, db)
    report["applied"] = None

    if args.apply:
        try:
            n_e = apply_extrude_rows(db, report["extrude_todo"])
            n_v = apply_evidence_backfill(detail_dir, report["gate1_traced"])
        except Exception as e:
            print(f"❌ 补账执行失败: {e}", file=sys.stderr)
            report["applied"] = {"error": str(e)}
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=1))
            return 1
        report["applied"] = {"extrude_rows": n_e, "evidence_backfilled": n_v}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        s = report["stats"]
        print(f"== a1 溯源回填报告（{'APPLY' if args.apply else '只读'}）==")
        print(f"detail {s['total']} 篇（解析失败 {s['unparsable']}）")
        print(f"A. 挤出条目共 {report['extrude_total']} 篇："
              f"待补 EXTRUDE 账 {len(report['extrude_todo'])} 篇")
        for it in report["extrude_todo"][:10]:
            print(f"   - {it['slug']} (origin={it['origin']}, ts={it['ts'] or '无'})")
        if len(report["extrude_todo"]) > 10:
            print(f"   … 共 {len(report['extrude_todo'])} 篇")
        print(f"B. origin 为空 {len(report['gate1_traced']) + len(report['gate1_untraceable'])} 篇："
              f"可关联账本 {len(report['gate1_traced'])} 篇 / 其余 {len(report['gate1_untraceable'])} 篇"
              f"（无账或已有证据链；空串即诚实，不伪造会话号）")
        if report["applied"] is not None:
            print(f"已执行: EXTRUDE 行 {report['applied']['extrude_rows']} 行 / "
                  f"evidence 回填 {report['applied']['evidence_backfilled']} 篇"
                  f"（备份: detail/.backfill-bak-*）")
        elif len(report["extrude_todo"]) or report["gate1_traced"]:
            print("ℹ️ 带 --apply 执行补账（先看本报告确认）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
