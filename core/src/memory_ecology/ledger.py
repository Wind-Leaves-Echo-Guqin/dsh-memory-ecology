"""ledger —— 账本（eco.db）单写入层：四门账 + 注入账 + 成本账。

设计取舍：
  - **一张表一列趋势，不做通用 KV**。因为 GUI 要按 `action` 聚合算"最后一次真实动作"，
    通用 KV 会让每个视图自己写一遍聚合（正是"双口径漂移"的来源）。
  - **`idle` 与真实动作分开统计**：旧项目的病就是 `quota_log` 246 行 idle 把 52 行
    extrude 淹了，导致"门在跑"看起来像"门在工作"。`effective()` 只数真实动作。
  - 每张门表都有 `gate` 概念：本实现用表名承载（gate_log/quota_log/distill_log/review_log），
    保持与既有库同名，便于迁移与 GUI 复用。

**只读打开**走 `paths.connect_ro`（mode=ro），GUI 与体检绝不升级为写锁。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config as _config, paths

GATES = {
    "gate1": {"table": "gate_log", "name": "门① 写入整合",
              "effective": ("ADD", "UPDATE", "SUPERSEDE", "MERGE", "EXTRUDE", "REFER"),
              "label": "ADD"},
    "gate2": {"table": "quota_log", "name": "门② 配额巩固",
              "effective": ("promote", "extrude", "extrude-manual", "rollback"),
              "label": "promote"},
    "gate3": {"table": "distill_log", "name": "门③ 画像蒸馏",
              "effective": ("promote", "demote", "merge"),
              "label": "promote"},
    "gate4": {"table": "review_log", "name": "门④ 复核降级",
              "effective": ("demote", "archive", "merge_candidate", "verify"),
              "label": "demote"},
}

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS gate_log(
        ts TEXT, fingerprint TEXT, action TEXT, target TEXT, note TEXT)""",
    """CREATE TABLE IF NOT EXISTS quota_log(
        ts TEXT, action TEXT, slug TEXT, l1_preview TEXT, reason TEXT)""",
    """CREATE TABLE IF NOT EXISTS distill_log(
        ts TEXT, action TEXT, target TEXT, note TEXT)""",
    """CREATE TABLE IF NOT EXISTS review_log(
        ts TEXT, action TEXT, slug TEXT, reason TEXT)""",
    """CREATE TABLE IF NOT EXISTS inject_event(
        ts TEXT, session TEXT, source TEXT, form TEXT, items INTEGER,
        chars INTEGER, ids TEXT, tokens INTEGER)""",
    """CREATE TABLE IF NOT EXISTS hit(
        ts TEXT, id TEXT, kind TEXT, query TEXT)""",
    """CREATE TABLE IF NOT EXISTS candidate(
        fp TEXT PRIMARY KEY, id TEXT, kind TEXT, scope TEXT,
        text TEXT, session TEXT, first_seen TEXT)""",
    """CREATE TABLE IF NOT EXISTS event(
        ts TEXT, kind TEXT, target TEXT, detail TEXT)""",
    """CREATE TABLE IF NOT EXISTS pack(
        id TEXT PRIMARY KEY, version TEXT, kind TEXT, mode TEXT,
        enabled INTEGER, hash TEXT, provenance TEXT, path TEXT,
        installed_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS cost(
        ts TEXT, session TEXT, tools_schema INTEGER, system_prompt INTEGER,
        catalog INTEGER, injected_msgs INTEGER, injected_chars INTEGER,
        eco_msgs INTEGER, eco_chars INTEGER)""",
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_hit_id ON hit(id)",
    "CREATE INDEX IF NOT EXISTS ix_hit_ts ON hit(ts)",
    "CREATE INDEX IF NOT EXISTS ix_inject_ts ON inject_event(ts)",
    "CREATE INDEX IF NOT EXISTS ix_event_ts ON event(ts)",
]


def _migrate(conn) -> None:
    """给已有库补列（**向前兼容**：老库不能因为加了一列就打不开）。

    只做 `ALTER TABLE ADD COLUMN`（可加不可删、不动既有数据）；失败即忽略——
    补列失败最多是"新字段读不到"，不该让整个账本变成不可用。
    """
    want = {
        "cost": [("eco_msgs", "INTEGER"), ("eco_chars", "INTEGER")],
        "inject_event": [("tokens", "INTEGER")],
    }
    for table, cols in want.items():
        try:
            have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        except sqlite3.Error:
            continue
        if not have:
            continue
        for name, typ in cols:
            if name in have:
                continue
            try:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, typ))
            except sqlite3.Error:
                pass


def open_rw(db: Path) -> sqlite3.Connection:
    """**唯一写入口**。建表 + 索引幂等 + 向前迁移。"""
    conn = paths.connect(db)
    for stmt in _SCHEMA:
        conn.execute(stmt)
    for stmt in _INDEXES:
        conn.execute(stmt)
    _migrate(conn)
    conn.commit()
    return conn


def open_ro(db: Path):
    """只读打开（GUI/体检/自检）。库不存在返回 None，绝不创建。"""
    conn = paths.connect_ro(db)
    if conn is None:
        return None
    try:
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        pass
    return conn


def log(conn, table: str, row: dict) -> None:
    """按表名写一行（列名即 dict 键）。缺列用空串补齐——**永不因为字段演进丢行**。"""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]
    keys = [k for k in cols if k in row]
    if not keys:
        keys = cols[:1]
        row = {keys[0]: ""}
    sql = "INSERT INTO %s(%s) VALUES (%s)" % (
        table, ",".join(keys), ",".join("?" * len(keys)))
    conn.execute(sql, [row[k] for k in keys])


def gate_log(conn, action: str, target: str, note: str = "") -> None:
    log(conn, "gate_log", {
        "ts": paths.now(),
        "fingerprint": paths.fingerprint(action, target)[:40],
        "action": action, "target": target, "note": note[:500],
    })


def quota_log(conn, action: str, slug: str = "", preview: str = "", reason: str = "") -> None:
    log(conn, "quota_log", {
        "ts": paths.now(), "action": action, "slug": slug,
        "l1_preview": preview[:80], "reason": reason[:400],
    })


def distill_log(conn, action: str, target: str = "", note: str = "") -> None:
    log(conn, "distill_log", {"ts": paths.now(), "action": action,
                              "target": target, "note": note[:400]})


def review_log(conn, action: str, slug: str = "", reason: str = "") -> None:
    log(conn, "review_log", {"ts": paths.now(), "action": action,
                             "slug": slug, "reason": reason[:400]})


def event(conn, kind: str, target: str = "", detail: str = "") -> None:
    log(conn, "event", {"ts": paths.now(), "kind": kind,
                        "target": target, "detail": detail[:500]})


# ── 读侧：四门实况（GUI G2 的唯一数据源）─────────────────────────────

def gate_status(conn, stale_days: float | None = None) -> dict:
    """每门：累计有效动作 / 最后一次真实动作 / 距今天数 / 是否空转。

    **判据写在代码里，不写进文档**（C6）：`effective == 0` 或 `age > stale_days`
    → `dead=True`。GUI 只负责把它画成红色。
    """
    if stale_days is None:
        stale_days = _config.effective_gate_stale_days()
    out = {"gates": [], "stale_days": stale_days, "dead_count": 0}
    if conn is None:
        for key, spec in GATES.items():
            out["gates"].append({
                "gate": key, "name": spec["name"], "total": 0, "effective": 0,
                "expected_count": 0, "last_action": "", "last_ts": "", "age_days": None,
                "dead": True, "kind": "dead",
                "reason": "账本不可读（库不存在或只读打开失败）",
                "expect": spec["label"],
            })
            out["dead_count"] += 1
        return out
    for key, spec in GATES.items():
        table = spec["table"]
        try:
            total = conn.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
        except sqlite3.Error:
            total = 0
        marks = ",".join("?" * len(spec["effective"]))
        eff, last_ts, last_action = 0, "", ""
        try:
            row = conn.execute(
                "SELECT count(*), max(ts) FROM %s WHERE action IN (%s)" % (table, marks),
                list(spec["effective"])).fetchone()
            eff = int(row[0] or 0)
            last_ts = row[1] or ""
            if last_ts:
                row2 = conn.execute(
                    "SELECT action FROM %s WHERE action IN (%s) ORDER BY ts DESC LIMIT 1"
                    % (table, marks), list(spec["effective"])).fetchone()
                last_action = (row2[0] if row2 else "") or ""
        except sqlite3.Error:
            pass
        age = paths.days_between(last_ts) if last_ts else None
        # **期望动作的计数**：门② 的真正判据不是"有没有动作"，而是"有没有成功提升过条目"。
        # 旧库的真实形态是 52 次挤出 / 0 次提升 —— L1 是个净流出系统；只看 effective>0
        # 会把这种"一直在挤出、从来没提升"的病态判成健康。
        label = spec["label"]
        try:
            expect_n = int(conn.execute(
                "SELECT count(*) FROM %s WHERE action=?" % table, (label,)).fetchone()[0] or 0)
        except sqlite3.Error:
            expect_n = 0
        dead = eff == 0 or age is None or age > stale_days or expect_n == 0
        # **两级红灯**（A2A G2 的硬要求）：两种红的处置完全不同，不能混成一句话。
        #   idle-spin = 账本有行但全是 idle/自检 → **进程活着、没产出**（查判据/输入）
        #   dead      = 一行都没有 → **根本没跑**（查调度/进程）
        kind = "ok"
        reason = ""
        if eff == 0:
            kind = "idle-spin" if total > 0 else "dead"
            reason = ("进程在跑但从未产出有效动作（账本 %d 行全是心跳/自检）" % total
                      if total > 0 else "账本里一行都没有——这一门从未运行过")
        elif expect_n == 0:
            # 有动作但**从来没有过期望的那一种**（门②的 promote / 门③的 promote）
            kind = "starved"
            reason = ("有动作（%d 条）但从未成功 %s —— 动作类型分布不对称，"
                      "多半是判据在真实数据下不可满足" % (eff, label))
        elif age is None:
            kind = "dead"
            reason = "最后一次动作时间不可解析"
        elif age > stale_days:
            kind = "stale"
            reason = "最后有效动作距今 %.1f 天（阈值 %.0f）" % (age, stale_days)
        out["gates"].append({
            "gate": key, "name": spec["name"], "total": total, "effective": eff,
            "expected_count": expect_n, "last_action": last_action, "last_ts": last_ts,
            "age_days": None if age is None else round(age, 2),
            "dead": bool(dead), "kind": kind, "reason": reason,
            "expect": label,
        })
        if dead:
            out["dead_count"] += 1
    return out


def action_counts(conn, table: str) -> dict:
    """action → 计数（旧项目的 `idle` 淹没问题在 GUI 侧用 effective 过滤解决）。"""
    if conn is None:
        return {}
    try:
        return {r[0]: int(r[1]) for r in
                conn.execute("SELECT action, count(*) FROM %s GROUP BY action" % table)}
    except sqlite3.Error:
        return {}


# ── 读侧：注入观测（G3）──────────────────────────────────────────────

def inject_summary(conn, limit_days: float = 14.0) -> dict:
    """按 source 聚合注入：条数/字符/token；再算 dead_rate 与 reload_rate。

    两个反馈量的定义（写死在代码里，避免各处口径漂移）：
      - `dead_rate`  = 注入过的 id 里，之后从未被 `hit` 或 `load` 命中的比例
      - `reload_rate`= 注入过的 id 里，之后**又被 load/search 拉回**的比例
    """
    out = {"sources": [], "total_events": 0, "dead_rate": None, "reload_rate": None,
           "window_days": limit_days, "recent": []}
    if conn is None:
        return out
    try:
        rows = conn.execute(
            "SELECT source, count(*), sum(chars), sum(items), sum(coalesce(tokens,0)) "
            "FROM inject_event GROUP BY source ORDER BY 2 DESC").fetchall()
    except sqlite3.Error:
        return out
    for r in rows:
        out["sources"].append({
            "source": r[0] or "unknown", "events": int(r[1] or 0),
            "chars": int(r[2] or 0), "items": int(r[3] or 0),
            "tokens": int(r[4] or 0),
            "avg_chars": round((r[2] or 0) / max(1, r[1] or 1), 1),
        })
        out["total_events"] += int(r[1] or 0)
    ids, hits = set(), set()
    try:
        for (blob,) in conn.execute("SELECT ids FROM inject_event WHERE ids IS NOT NULL"):
            for i in str(blob or "").split(","):
                i = i.strip()
                if i:
                    ids.add(i)
        for (i,) in conn.execute("SELECT DISTINCT id FROM hit"):
            hits.add(i)
    except sqlite3.Error:
        pass
    if ids:
        out["dead_rate"] = round(len(ids - hits) / len(ids), 4)
        out["reload_rate"] = round(len(ids & hits) / len(ids), 4)
    try:
        for r in conn.execute(
                "SELECT ts, source, form, items, chars FROM inject_event "
                "ORDER BY ts DESC LIMIT 30"):
            out["recent"].append({"ts": r[0], "source": r[1], "form": r[2],
                                  "items": r[3], "chars": r[4]})
    except sqlite3.Error:
        pass
    return out


def cost_summary(conn, limit: int = 10) -> dict:
    """最近 N 次成本快照（G6 与 tools/measure.py 共读同一批行）。

    同时给出**全量**与**本插件（eco_）**两套数：
      · 全量 = 这个会话里所有注入源（time-context / skill-catalog / subagent …）
      · eco_ = 只有本插件自己的注入
    KPI（≤3 条 / ≤1,200 字符）**只对 eco_ 成立**——把所有来源都算进 KPI 会让
    "超标"变成常态误报，告警一响就没人看了。
    """
    out = {"rows": [], "eco_legacy_rows": 0}
    if conn is None:
        return out
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cost)")]
    has_split = "eco_msgs" in cols and "eco_chars" in cols
    if not has_split:
        out["eco_legacy_rows"] = 1
    try:
        for r in conn.execute(
                "SELECT ts, session, tools_schema, system_prompt, catalog, "
                "injected_msgs, injected_chars"
                + (", eco_msgs, eco_chars" if has_split else ", NULL, NULL")
                + " FROM cost ORDER BY ts DESC LIMIT ?", (limit,)):
            out["rows"].append(dict(zip(
                ("ts", "session", "tools_schema", "system_prompt", "catalog",
                 "injected_msgs", "injected_chars", "eco_msgs", "eco_chars"), tuple(r))))
    except sqlite3.Error:
        pass
    return out


def prune_hit(conn, keep_days: int = 180) -> int:
    """`hit` 表归档：删掉超过 keep_days 的行。

    **这不是"物理删除记忆"**（红线 1 只禁删记忆条目）；hit 是遥测，无界增长会让
    目录排序查询变慢（旧项目已知边界 7）。返回删除行数，调用方必须记账。
    """
    if conn is None or keep_days <= 0:
        return 0
    try:
        cur = conn.execute(
            "DELETE FROM hit WHERE julianday('now') - julianday(substr(ts,1,19)) > ?",
            (keep_days,))
        conn.commit()
        return int(cur.rowcount or 0)
    except sqlite3.Error:
        return 0
