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


# ── 目录载荷 ─────────────────────────────────────────────────────────

def catalog_payload(items: list[dict], limit: int = CATALOG_DEFAULT,
                    scope: str | None = None, budget_chars: int = 0,
                    stats: dict | None = None) -> dict:
    """→ {count, total, used_chars, items[]}。

    排序：**命中次数降序 → 更新时间降序**。刻意不按类型分组：旧条目不该因为类型被挤掉。
    `status != active` 的条目不进目录（降级/归档的条目只能靠 search 取回）。
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
        if scope:
            cost = len(it["id"]) + 3 + len(it["kind"]) + 3 + len(it["line"]) + 3
        else:
            cost = len(it["id"]) + 3 + len(it["kind"]) + 3 + len(it["line"]) + 3
        if budget_chars > 0 and shown and used + cost > budget_chars:
            break
        shown.append({k: v for k, v in it.items() if k != "_sort"})
        used += cost
    return {"ok": True, "count": len(shown), "total": total,
            "used_chars": used, "budget_chars": budget_chars, "items": shown}


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
            budget_chars=budget_chars or c_budget, stats=stats)
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
