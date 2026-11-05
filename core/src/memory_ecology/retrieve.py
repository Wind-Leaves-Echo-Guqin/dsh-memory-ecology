"""retrieve —— 词面检索、目录载荷、物化视图（stdlib only）。

三条从旧项目继承的硬结论（别再重新发明）：
  1. **目录进系统提示词，不进对话消息**。实测上下文 append-only，每条追加都会加长
     之后每一次请求的前缀；做成 `systemPrompt.section` 只付一次且走缓存。
  2. **预算按字符不按条数**。条数是假闸门（20 条 200 字的目录和 20 条 20 字的一样"合规"）。
  3. **物化成文件是正确性要求，不是优化**。dsh 在 agent 创建后 ~10ms 装配系统提示词，
     子进程取数要 80ms，任何"异步取数后回填"必然输。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config as _config, paths, store

CATALOG_DEFAULT = _config.CATALOG_LIMIT
CATALOG_LINE_MAX = 90
SEARCH_DEFAULT = _config.SEARCH_LIMIT
CATALOG_BUDGET = _config.CATALOG_BUDGET_CHARS


# ── 词面检索 ─────────────────────────────────────────────────────────

def terms(query: str) -> list[str]:
    """查询 → 检索词。

    拉丁词整词；中文**整串 + 2-gram**（中文常用词恰是 2 字）。这是纯 stdlib 能做到的
    最好折中——再加就需要分词词典或 embedding（P4 才做，别在本版偷渡）。
    """
    q = paths.norm_text(query).lower()
    out: list[str] = []
    for w in re.findall(r"[a-z0-9_+.#-]{2,}", q):
        out.append(w)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", q):
        out.append(run)
        for i in range(len(run) - 1):
            out.append(run[i:i + 2])
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def score(body: str, ts: list[str]) -> float:
    """词频 × 词长权重 ÷ √正文长度（长短条目公平）。"""
    low = body.lower()
    s = 0.0
    for t in ts:
        c = low.count(t)
        if not c:
            continue
        s += (1.0 + 0.35 * max(0, len(t) - 2)) * c
    if s <= 0:
        return 0.0
    return s / (max(12, len(low)) ** 0.5)


def search(items: list[dict], query: str, limit: int = SEARCH_DEFAULT,
           scope: str | None = None, hit_stats: dict | None = None) -> list[dict]:
    """在 items 上做检索。**只读**——记账由调用方决定（`cmd_search` 才写 hit）。"""
    ts = terms(query)
    if not ts:
        return []
    hit_stats = hit_stats or {}
    out = []
    for it in items:
        if it.get("status") in ("archived",) or it.get("fm", {}).get("fate") == "superseded":
            continue
        if scope and it.get("scope") not in (scope, "global"):
            continue
        s = score(it["body"], ts)
        if s <= 0:
            continue
        boost = 1.0 + 0.04 * min(20, int(hit_stats.get(it["name"], (0, ""))[0]))
        out.append({
            "id": it["fm"].get("id") or it["name"],
            "kind": it.get("type") or "semantic",
            "scope": it.get("scope", "global"),
            "score": round(s * boost, 4),
            "line": paths.norm_text(it["body"])[:CATALOG_LINE_MAX],
            "chars": len(it["body"]),
        })
    out.sort(key=lambda x: -x["score"])
    return out[:max(1, limit)]


def hit_stats(conn) -> dict:
    """id → (命中次数, 最近命中时间)。目录排序与淘汰判据的原料。"""
    if conn is None:
        return {}
    import sqlite3
    try:
        return {r[0]: (int(r[1]), r[2] or "")
                for r in conn.execute("SELECT id, count(*), max(ts) FROM hit GROUP BY id")}
    except sqlite3.Error:
        return {}


# ── L1 常驻段（第三轮审查：L1 写入了但 catalog/search 全都不读它）──────
#
# L1（memories/MEMORY.md / USER.md）是门② promote 的落点。修复前它是
# "单向漏斗"：写进去之后 catalog 只吃 detail、search 只扫 detail、插件只
# 注入物化目录——L1 写了等于没写，门② 三重空转。本节把 L1 变成
# **可读的一等公民**。
#
# 圈（scope）语义：L1 属于 **global 圈**（MEMORY.md 在根 memories/ 下，不在
# 圈子目录）。与 global 条目同口径——global 条目在任何圈的目录里都可见，
# L1 常驻段同理出现在每个圈的 catalog 载荷里。

def _l1_section_payload(sections: list[str]) -> list[dict]:
    """§ 分段 → [{text, hash}]。hash 供插件做"变更才注入"的增量判断。"""
    return [{"text": s, "hash": paths.fingerprint("l1sec", paths.norm_key(s))[:16]}
            for s in sections]


def l1_resident(root) -> dict:
    """读 L1（MEMORY.md / USER.md）→ 常驻段载荷。

    全仓唯一 L1 入口是 `root.memory_md` / `root.user_md`（root.py），
    解析口径唯一来自 `store.parse_l1`。**只读**，文件缺失按空处理（L1 尚未
    建立是常态，不是错误）。
    """
    mem_raw = paths.read_text(root.memory_md)
    mem_sections, _ = store.parse_l1(mem_raw)
    user_raw = paths.read_text(root.user_md)
    user_sections, _ = store.parse_l1(user_raw)
    mem_chars = store.chars_of(mem_sections)
    user_chars = store.chars_of(user_sections)
    return {
        "source": "L1",
        "scope": "global",
        "memory": {
            "exists": bool(mem_raw.strip()),
            "file": str(root.memory_md),
            "entries": len(mem_sections),
            "chars": mem_chars,
            "lines": [paths.norm_text(s)[:CATALOG_LINE_MAX] for s in mem_sections],
            "sections": _l1_section_payload(mem_sections),
        },
        "user": {
            "exists": bool(user_raw.strip()),
            "file": str(root.user_md),
            "entries": len(user_sections),
            "chars": user_chars,
            # 一行摘要：USER.md 是画像，逐条太长；拼前 3 条各 40 字符
            "summary": "；".join(paths.norm_text(s)[:40] for s in user_sections[:3]),
            "sections": _l1_section_payload(user_sections),
        },
        "total_chars": mem_chars + user_chars,
    }


def l1_search_hits(l1: dict, query: str) -> list[dict]:
    """L1 段的词面命中（来源=L1）。

    L1 是常驻层：命中**不参与 hit 记账**（没有可 load 的 id，也不该影响
    门② 的"使用证据"统计——它已经在每轮上下文里了，命中与否不产生新信息）。
    """
    ts = terms(query)
    if not ts:
        return []
    out = []
    for part, kind in (("memory", "resident"), ("user", "profile")):
        for sec in (l1.get(part, {}) or {}).get("sections", []):
            s = score(sec["text"], ts)
            if s <= 0:
                continue
            out.append({
                "id": "L1", "kind": kind, "source": "L1",
                "scope": "global", "score": round(s, 4),
                "line": paths.norm_text(sec["text"])[:CATALOG_LINE_MAX],
                "chars": len(sec["text"]),
            })
    out.sort(key=lambda x: -x["score"])
    return out


def render_l1_injection(l1: dict, budget: int) -> tuple[str, bool, int]:
    """L1 载荷 → 插件首轮注入文本（MEMORY.md 全文 + USER.md 摘要）。

    预算按字符（与目录段 600 字符同一哲学）；超限**按 § 段从最旧端丢弃**
    并显式标注（L1 是 append-only，尾部是最新条目，保新弃旧）。
    → (文本, 是否截断, 丢弃段数)。空 L1 → ("", False, 0)。
    """
    mem_secs = [s["text"] for s in (l1.get("memory", {}) or {}).get("sections", [])]
    user_secs = [s["text"] for s in (l1.get("user", {}) or {}).get("sections", [])]
    if not mem_secs and not user_secs:
        return "", False, 0

    def build(kept: list[str]) -> str:
        parts = []
        if kept:
            parts.append("L1 常驻记忆（memories/MEMORY.md，%d/%d 段）："
                         % (len(kept), len(mem_secs)))
            parts.append("\n§\n".join(kept))
        if user_secs:
            parts.append("用户画像（memories/USER.md 摘要，%d 条）：" % len(user_secs))
            parts.append("；".join(paths.norm_text(s)[:40] for s in user_secs))
        return "\n".join(parts)

    text = build(mem_secs)
    if len(text) <= budget:
        return text, False, 0
    kept = list(mem_secs)
    omitted = 0
    # **截断标注行本身也要算进预算**（质量门①审查 P2）：旧写法先按 budget
    # 收缩、再追加标注行——最终文本系统性超出预算几十字符。
    note = "\n（超预算截断：省略最早 %d 段，全文在 memories/MEMORY.md）"
    note_budget = max(60, budget - (len(note % 0) + 8))
    while kept and len(build(kept)) > note_budget:
        kept.pop(0)   # 丢最旧（L1 尾部是最新）
        omitted += 1
    text = build(kept)
    if len(text) > note_budget:   # 极端：光 USER 摘要也超 → 硬截断兜底
        text = text[:note_budget] + "…"
    if omitted:
        text += note % omitted
    if len(text) > budget:   # 最终保险丝：无论如何不超预算
        text = text[:budget]
    return text, True, omitted


# ── 目录载荷 ─────────────────────────────────────────────────────────

def catalog_payload(items: list[dict], limit: int = CATALOG_DEFAULT,
                    scope: str | None = None, budget_chars: int = 0,
                    stats: dict | None = None, l1: dict | None = None) -> dict:
    """→ {count, total, used_chars, items[], l1?}。

    排序：**命中次数降序 → 更新时间降序**。刻意不按类型分组：旧条目不该因为类型被挤掉。
    `status != active` 的条目不进目录（降级/归档的条目只能靠 search 取回）。

    `l1`（`l1_resident()` 的输出）作为**L1 常驻段**附加在载荷上：它有自己的
    配额（门② 管），**不占目录的字符预算、不进 items 排序**——L1 是常驻层，
    目录是按预算截断的滚动层，两者不是一个池子。
    """
    stats = stats or {}
    rows = []
    for it in items:
        if it.get("status") != "active":
            continue
        if it.get("fm", {}).get("fate") in ("superseded", "archived"):
            continue
        if scope and it.get("scope") not in (scope, "global", ""):
            continue
        mid = it["fm"].get("id") or it["name"]
        one = paths.norm_text(it["body"])
        if len(one) > CATALOG_LINE_MAX:
            one = one[:CATALOG_LINE_MAX - 1] + "…"
        rows.append({
            "id": mid,
            "kind": it.get("type") or "semantic",
            "scope": it.get("scope", "global"),
            "line": one,
            "chars": len(it["body"]),
            "updated": it["fm"].get("created") or it["fm"].get("first_seen") or "",
            "hits": int(stats.get(mid, (0, ""))[0]),
            # 排序键：负 hits + 时间正序（旧的在前）会被 `-x["hits"]` 抵消，
            # 所以这里直接给出复合键，避免下游各自拼排序
            "_sort": (-int(stats.get(mid, (0, ""))[0]),
                      _neg_time(it["fm"].get("created") or it["fm"].get("first_seen") or "")),
        })
    rows.sort(key=lambda x: x["_sort"])
    total = len(rows)
    shown, used = [], 0
    for it in rows[:max(1, limit)]:
        # 行成本 = id + kind + 摘要 + 三段分隔（二轮审查 P2：原来的 if/else
        # 两分支逐字符相同，是死代码）
        cost = len(it["id"]) + 3 + len(it["kind"]) + 3 + len(it["line"]) + 3
        if budget_chars > 0 and shown and used + cost > budget_chars:
            break
        shown.append({k: v for k, v in it.items() if k != "_sort"})
        used += cost
    return {"ok": True, "count": len(shown), "total": total,
            "used_chars": used, "budget_chars": budget_chars, "items": shown,
            # **L1 常驻段**（修复"L1 无人读"）：有内容才附，空根不带噪音。
            # 空段也要能区分"没传 l1"与"传了但为空"——用 exists 字段表达。
            **({"l1": l1} if l1 is not None and
               (l1.get("memory", {}).get("exists") or l1.get("user", {}).get("exists"))
               else {})}


def _neg_time(value: str) -> float:
    """把时间串转成"越新越靠前"的排序键（旧值取负）。解析失败当作最旧。"""
    dt = paths.parse_ts(value)
    if dt is None:
        return 0.0
    return -dt.timestamp()


# ── 物化视图 ─────────────────────────────────────────────────────────

CATALOG_SCHEMA = 2   # v3.2.0：载荷加了 budget_chars / schema；旧文件按 schema 判过期


def materialize(root, limit: int = CATALOG_DEFAULT, budget_chars: int = 0,
                scopes: list[str] | None = None) -> dict:
    """把每个圈的目录写成 `.eco/catalog/<scope>.json` + `index.json`。

    每个圈用自己的预算（`circles.json` 的 budget_chars / catalog_limit）。
    `scopes=None` → 全部 detail 里出现过的圈 + global。
    """
    items = root.items()
    conn = root.connect_ro()
    stats = hit_stats(conn) if conn is not None else {}
    if conn is not None:
        conn.close()
    # L1 常驻段：每个圈的物化载荷都带上（global 圈语义——global 条目在哪个
    # 圈都可见，L1 同理）。读一次，逐圈复用同一份。
    l1 = l1_resident(root)

    if scopes is None:
        scopes = ["global"]
        for it in items:
            sc = it.get("scope") or "global"
            if sc != "global" and sc not in scopes:
                scopes.append(sc)
    # 配置里声明的圈也要物化（哪怕暂时没有条目——GUI 要显示"空圈"而不是"圈不存在"）
    for c in store.load_circles(root.path).get("circles", []):
        cid = store.norm_scope(c.get("id"))
        if cid not in scopes:
            scopes.append(cid)

    out_dir = root.catalog_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for sc in scopes:
        c_budget, c_limit = store.circle_budget(root.path, sc)
        # 显式传入的预算优先（调用方/测试），否则用圈自己的预算
        payload = catalog_payload(
            items, limit=limit or c_limit, scope=sc,
            budget_chars=budget_chars or c_budget, stats=stats, l1=l1)
        payload["schema"] = CATALOG_SCHEMA
        payload["scope"] = sc
        payload["generated_at"] = paths.now()
        paths.write_atomic(out_dir / (sc + ".json"),
                           json.dumps(payload, ensure_ascii=False))
        written.append(sc)
    paths.write_atomic(out_dir / "index.json", json.dumps(
        {"schema": CATALOG_SCHEMA, "scopes": written, "generated_at": paths.now()},
        ensure_ascii=False))
    return {"ok": True, "scopes": written, "dir": str(out_dir),
            "detail_total": len(items)}


def read_materialized(root, scope: str) -> dict | None:
    """同步读一份物化目录。**损坏/过期一律返回 None**（fail-open：宁可少注入一段，
    也不能把半截 JSON 塞进系统提示词）。"""
    p = root.catalog_dir / (scope + ".json")
    if not p.is_file():
        return None
    try:
        data = json.loads(paths.read_text(p) or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    if data.get("schema") != CATALOG_SCHEMA:
        return None
    return data


def render_catalog_text(payload: dict | None, limit: int = CATALOG_DEFAULT) -> str:
    """目录载荷 → 注入文本（插件与 GUI 共用同一渲染口径，杜绝漂移）。

    格式刻意保持稳定：`- <id> │ <kind> │ <摘要>`。任何格式改动都会让前缀缓存失效。
    """
    if not payload:
        return ""
    items = payload.get("items") or []
    if not items:
        return ""
    shown = items[:limit]
    lines = []
    for it in shown:
        kind = str(it.get("kind") or "semantic")[:8].ljust(8, " ")
        lines.append("- %s │ %s │ %s" % (it.get("id") or "", kind,
                                         paths.norm_text(it.get("line") or "")))
    total = int(payload.get("total") or len(items))
    if total > len(shown):
        head = "记忆目录（共 %d 条，显示最近 %d 条；正文用 memory_load(id) 取）" % (
            total, len(shown))
    else:
        head = "记忆目录（共 %d 条；正文用 memory_load(id) 取）" % total
    return "\n".join([head] + lines)
