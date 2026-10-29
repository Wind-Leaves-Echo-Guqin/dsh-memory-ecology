#!/usr/bin/env python3
"""eco_trace — 溯源回原文（v3.1.0「记忆可回溯」件；调研依据：记忆日历 memory_expand / 谛听角标）。

把 a1 落下的溯源数据变成可用的回溯能力：detail 条目 → 来源会话 → 原文窗口。

解析链（与写入侧严格对应）：
  1. detail frontmatter：origin_session_id（v3.0.0a1 起由 sidecar 填，逗号分隔多会话）
  2. evidence：gate_log:<action>@<ts>（a1 存量回填）→ 用 ts 定位该会话内的时间窗
  3. state.db（只读 ?mode=ro）：sessions（cwd/model/title/时间）+ messages（原文窗口）

输出：人读（默认）/ --json（GUI 直接消费：{"ok", "slug", "origin", "sessions":[...],
"window":[...]}）。不可溯（origin 空 / extrude / 会话已清）如实报告，绝不猜。

用法:
  python tools/eco_trace.py <slug> [--window N] [--json]
  python tools/eco_trace.py <slug> --full        # 原文窗口打印全文
选项: --root <数据根>（默认 hermes_root()；对真实数据必须显式传生产根）
退出码: 0 有溯源输出 / 1 无可溯源信息 / 2 用法错误
"""
from __future__ import annotations

import argparse
import datetime
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
except Exception as e:
    print(f"warning: could not reconfigure stdio to UTF-8: {e!r}", file=sys.stderr)

from lib.config import hermes_root          # noqa: E402
from lib.memstore import parse_frontmatter  # noqa: E402

EV_RE = re.compile(r"gate_log:(\w+)@(.+)")


def read_fm(detail_dir: Path, slug: str):
    p = detail_dir / f"{slug}.md"
    if not p.is_file():
        return None
    return parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))


def parse_ts(raw: str):
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw.strip())
    except ValueError:
        return None


def fetch_sessions(db: Path, ids: list[str]) -> list[dict]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        out = []
        for sid in ids:
            row = con.execute(
                "SELECT id, cwd, model, title, started_at, last_activity_at, message_count "
                "FROM sessions WHERE id=?", (sid,)).fetchone()
            if row:
                out.append(dict(zip(
                    ("id", "cwd", "model", "title", "started_at", "last_activity_at",
                     "message_count"), row)))
        return out
    finally:
        con.close()


def fetch_window(db: Path, session_id: str, around: datetime.datetime | None,
                 radius: int) -> list[dict]:
    """around 附近 ±radius 条（无锚点则取会话头 radius*2 条）。

    messages.timestamp 生产 schema 是 REAL unix 秒——数值比较（OCR 修正：原先
    `datetime(timestamp)` 会被 SQLite 当 julian day 解析，锚点恒错位）。
    """
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        if around is not None:
            row = con.execute(
                "SELECT id FROM messages WHERE session_id=? AND timestamp >= ? "
                "ORDER BY timestamp ASC, id ASC LIMIT 1",
                (session_id, around.timestamp())).fetchone()
        else:
            row = None
        if row:
            anchor = row[0]
            rows = con.execute(
                "SELECT id, role, content, timestamp FROM messages WHERE session_id=? AND id BETWEEN ? AND ? "
                "ORDER BY id ASC",
                (session_id, anchor - radius, anchor + radius)).fetchall()
        else:
            rows = con.execute(
                "SELECT id, role, content, timestamp FROM messages WHERE session_id=? "
                "ORDER BY id ASC LIMIT ?",
                (session_id, radius * 2)).fetchall()
        return [dict(zip(("id", "role", "content", "timestamp"), r)) for r in rows]
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="溯源回原文：detail → 来源会话 → 原文窗口")
    ap.add_argument("slug", help="detail 条目 slug（文件名主干）")
    ap.add_argument("--window", type=int, default=6, help="锚点两侧各取 N 条（默认 6）")
    ap.add_argument("--full", action="store_true", help="窗口消息打全文（默认截断 120 字）")
    ap.add_argument("--json", action="store_true", help="GUI 消费的机器读输出")
    ap.add_argument("--root", default="", help="数据根（默认 hermes_root()）")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else hermes_root()
    detail_dir = root / "memories" / "detail"
    db = root / "state.db"
    got = read_fm(detail_dir, args.slug)
    if got is None:
        print(f"❌ detail 未找到: {args.slug}", file=sys.stderr)
        return 2
    fm, body = got
    origin = str(fm.get("origin_session_id", "") or "").strip()
    evidence = str(fm.get("evidence", "") or "").strip()
    tx = parse_ts(str(fm.get("transaction_time", "") or ""))

    out = {"ok": False, "slug": args.slug, "origin": origin, "evidence": evidence,
           "sessions": [], "window": [], "reason": ""}
    ev_m = EV_RE.match(evidence)
    anchor = parse_ts(ev_m.group(2)) if ev_m else tx

    if not origin:
        out["reason"] = ("origin 为空（历史条目，捕获时未落会话溯源；"
                         "a1/v3.0.0a1 起新条目自动携带）")
    elif "extrude" in origin.lower():
        out["reason"] = "挤出条目（源自 L1 挤出直写，无会话级原文可回）"
    elif not db.is_file():
        out["reason"] = f"state.db 不存在: {db}"
    else:
        sids = [s.strip() for s in origin.split(",") if s.strip() and not s.startswith("|more")]
        sids = [s for s in sids if not s.startswith("more:")]
        out["sessions"] = fetch_sessions(db, sids)
        out["ok"] = bool(out["sessions"])
        if out["sessions"]:
            out["window"] = fetch_window(db, out["sessions"][0]["id"], anchor, args.window)
        elif sids:
            out["reason"] = f"会话已不在 state.db（历史清理）：{sids}"
    if not out["ok"] and not out["reason"]:
        out["reason"] = "无可用溯源信息"

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(f"== eco_trace: {args.slug}")
        print(f"origin: {origin or '(空)'}")
        print(f"evidence: {evidence or '(空)'}")
        for s in out["sessions"]:
            print(f"会话 {s['id']}")
            print(f"  cwd={s['cwd'] or '?'} model={s['model'] or '?'} "
                  f"msgs={s['message_count']}")
            if s.get("title"):
                print(f"  title: {str(s['title'])[:60]}")
        if out["window"]:
            print(f"-- 原文窗口（{len(out['window'])} 条，锚点=入库时间附近）--")
            for msg in out["window"]:
                text = str(msg["content"] or "").replace("\n", " ")
                if not args.full and len(text) > 120:
                    text = text[:120] + "…"
                print(f"  [{msg['id']}][{msg['role']}] {text}")
        else:
            print(f"-- 不可回溯：{out['reason']} --")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
