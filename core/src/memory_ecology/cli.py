"""cli —— 唯一对外接口（JSON 进 JSON 出，退出码分档）。

**为什么只有一条 CLI**：
  · dsh 插件（L2）、GUI（人类观测舱）、Hermes（只读挂载方）**都调它**，谁都不许自己
    实现领域逻辑——这是 GUI 与插件口径不漂移的机制保证（C6），不是约定。
  · 每命令成功输出**单行 JSON**；失败输出 `{ok:false,error}`，绝不 print 半截。

退出码（契约）：
  0 成功
  2 用法错误（argparse 自己抛）
  3 只读降级（根未初始化 / schema 不符 / 不可写）
  4 内部错误（**带 error 字段**，不是静默）
  5 校验失败（pack / 圈配置非法——可预期但不该成功的输入）

命令一览见 `python -m memory_ecology --help`。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import (__version__, config, gates, ledger, packs, paths, proclock, retrieve,
               root as rootmod, store)


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _open(args) -> rootmod.Root:
    return rootmod.Root(paths.resolve_root(args.root)).open(create=False)


# ── 各命令 ───────────────────────────────────────────────────────────

def cmd_init(root, args) -> dict:
    if (root.path / ".eco" / "root.json").is_file():
        fresh = False
        root.ensure_dirs()
        root.open(create=False)
    else:
        fresh = True
        root = rootmod.init_root(root.path)
    conn = root.connect()
    try:
        if not fresh:
            # 幂等：重复 init 不再写账本（否则账本被噪音淹没）
            pass
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "root": str(root.path), "root_id": root.meta.get("root_id"),
            "schema_version": rootmod.SCHEMA_VERSION, "fresh": fresh,
            "readonly": root.readonly, "version": __version__,
            "python": sys.version.split()[0]}


def cmd_deposit(root, args) -> dict:
    if root.readonly:
        return root.readonly_payload()
    text = paths.norm_text(args.text)[:store.BODY_MAX]
    if not text:
        return {"ok": False, "error": "empty-text"}
    # **圈名塌缩拒绝**（安全测试探测 1）：携带字母/数字的 scope 被归一剥成
    # 空串 → 静默落进可见面最宽的 global，成功回执毫无警示。机密意图不能这样
    # 被"放宽"——fail-closed。纯标点塌缩（"."、".."、"—"）不在此列：那是
    # test_compat 钉住的安全阀（恶意名安全碰撞，不产生穿越）。
    raw_scope = str(getattr(args, "scope", "") or "").strip()
    scope = store.norm_scope(raw_scope)
    warn = ""
    if (raw_scope and raw_scope.lower() != "global" and scope in ("", "global")
            and any(ch.isalnum() for ch in raw_scope)):
        return {"ok": False, "error": "scope-collapsed-to-global", "exit": 5,
                "detail": ("圈名 %r 归一后为空（携带字母/数字的圈名被整段剥掉，"
                           "内容会静默落进可见面最宽的 global）。"
                           "请用 [a-z0-9._-] 组成的圈名。") % raw_scope[:40]}
    # 部分塌缩警示（漏洞扫描低#11）：混合名 `机密圈X` 归一成 `x`——内容照投，
    # 但回执里必须让投递方看见改名这一步。
    if raw_scope and scope and scope != "global" and scope != raw_scope:
        warn = "圈名 %r 被归一为 %r" % (raw_scope[:40], scope)
    ctype = store.norm_type(args.kind)
    if store.circle_readonly(root.path, scope):
        return {"ok": False, "readonly": True, "reason": "circle-readonly:%s" % scope}
    day = paths.today().replace("-", "")
    folder = root.pending if scope == "global" else (root.pending / scope)
    safe_sess = "".join(ch if ch.isalnum() or ch in "._-" else "_"
                        for ch in (args.session or "anon"))[:40] or "anon"
    f = folder / ("cand-%s-%s.md" % (day, safe_sess))
    line = "- [%s] %s\n" % (ctype, text)
    try:
        paths.append_line(f, line)
    except OSError as exc:
        return {"ok": False, "error": "append-failed", "detail": str(exc)[:200]}
    return {"ok": True, "file": f.name, "kind": ctype, "scope": scope,
            "chars": len(text), **({"warn": warn} if warn else {})}


def cmd_reconcile(root, args) -> dict:
    if root.readonly:
        return root.readonly_payload()
    # 门① 的第二个入口也必须持进程锁（质量门①审查 P2）：插件每 3 分钟调它，
    # 与另一进程的 `gate all` 并发写同一 root 的场景与 `gate` 完全同构。
    lock = proclock.acquire(root.path, "gate")
    if not lock.get("ok"):
        try:
            conn = root.connect()
            try:
                ledger.event(conn, "gate.lock-blocked", "reconcile",
                             "holder pid=%s ts=%s" % ((lock.get("holder") or {}).get("pid"),
                                                      (lock.get("holder") or {}).get("ts")))
                conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "locked": True, "gate": "1",
                "holder": lock.get("holder"),
                "reason": "another-gate-run-in-progress", "exit": 5}
    try:
        return gates.integrate(root, archive=not args.no_archive, dry_run=args.dry_run)
    finally:
        proclock.release(root.path, "gate")


def cmd_gate(root, args) -> dict:
    if root.readonly:
        return root.readonly_payload()
    which = args.gate
    if which not in ("1", "2", "3", "4", "all"):
        return {"ok": False, "error": "unknown-gate", "gate": which}
    # ---- 进程级锁（v17 对照 · 缺口 3）：门可能被 hermes cron、插件维护、
    # 人手 CLI 同时拉起。四门幂等，但并发写同一个 root 仍可能交错出脏状态。
    # 规则：<30 分钟持锁 → 立即退出报 locked（被挡的一次落账，health 能报）；
    # >30 分钟 → 判持有进程已死，清掉重跑。锁文件写不了 → fail-open 放行。
    lock = proclock.acquire(root.path, "gate")
    if not lock.get("ok"):
        holder = lock.get("holder") or {}
        try:
            conn = root.connect()
            try:
                ledger.event(conn, "gate.lock-blocked", "gate:%s" % which,
                             "holder pid=%s ts=%s age_min=%s"
                             % (holder.get("pid"), holder.get("ts"), lock.get("age_min")))
                conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - 落账失败不能盖过 locked 本身
            pass
        return {"ok": False, "locked": True, "gate": which, "holder": holder,
                "age_min": lock.get("age_min"),
                "reason": "another-gate-run-in-progress", "exit": 5}
    if lock.get("stale_cleared") or lock.get("degraded"):
        try:
            conn = root.connect()
            try:
                if lock.get("stale_cleared"):
                    ledger.event(conn, "gate.lock-stale-cleared", "gate:%s" % which,
                                 "前任 pid=%s ts=%s" % ((lock.get("holder") or {}).get("pid"),
                                                        (lock.get("holder") or {}).get("ts")))
                if lock.get("degraded"):
                    ledger.event(conn, "gate.lock-degraded", "gate:%s" % which,
                                 str(lock.get("degraded")))
                conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        if which == "1":
            return gates.integrate(root, archive=True, dry_run=args.dry_run)
        if which == "2":
            return gates.quota(root, dry_run=args.dry_run)
        if which == "3":
            return gates.distill(root, dry_run=args.dry_run)
        if which == "4":
            return gates.review(root, dry_run=args.dry_run)
        return gates.maintain(root, dry_run=args.dry_run)
    finally:
        proclock.release(root.path, "gate")


def cmd_bring_back(root, args) -> dict:
    """把休眠/归档的条目显式恢复成 active（回目录）。

    **为什么必须有 CLI 入口**：`gates.bring_back()` 一直存在，但没有任何命令
    能调到它——等于"文档说有恢复通道，用户实际没有"。门④ 一旦真正运行
    （H1 修复后），降级就是会发生的常态，没有恢复通道等于单向丢目录。
    """
    if root.readonly:
        return root.readonly_payload()
    return gates.bring_back(root, args.name, reason=args.reason or "")


def cmd_catalog(root, args) -> dict:
    budget, limit = store.circle_budget(root.path, args.scope or "global")
    payload = retrieve.catalog_payload(
        root.items(), limit=args.limit or limit, scope=args.scope,
        budget_chars=args.max_chars if args.max_chars is not None else budget,
        stats=retrieve.hit_stats(root.connect_ro()),
        l1=retrieve.l1_resident(root))
    payload["ok"] = True
    if args.log_chars is not None and not root.readonly:
        conn = root.connect()
        try:
            ledger.log(conn, "inject_event", {
                "ts": paths.now(), "session": args.session or "", "source": "eco-catalog",
                "form": "catalog", "items": payload["count"],
                "chars": args.log_chars, "tokens": args.log_chars // 2,
                "ids": ",".join(i["id"] for i in payload["items"])[:500]})
            conn.commit()
        finally:
            conn.close()
    return payload


def cmd_load(root, args) -> dict:
    it = root.item(args.id)
    if it is None:
        return {"ok": False, "error": "not-found", "id": args.id}
    body = it["body"]
    # **正文硬上限**（二轮安全测试：15.8MB 手植 detail 一次 load 打爆上下文——
    # BODY_MAX 只约束门①整合通道，load 直读必须自带上限）
    try:
        load_cap = int(config.get(root.path, "LOAD_MAX_CHARS", config.LOAD_MAX_CHARS))
    except (TypeError, ValueError):
        load_cap = config.LOAD_MAX_CHARS
    truncated = False
    if len(body) > load_cap:
        body = body[:load_cap] + "\n…（超上限截断，全文在 detail/%s.md）" % it["name"]
        truncated = True
    out = {"ok": True, "id": it["fm"].get("id") or it["name"],
           "kind": it.get("type"), "scope": it.get("scope"),
           "status": it.get("status"), "body": body, "chars": len(body),
           "truncated": truncated}
    if not root.readonly:
        conn = root.connect()
        try:
            conn.execute("INSERT INTO hit(ts,id,kind,query) VALUES (?,?,?,?)",
                         (paths.now(), out["id"], out["kind"], "load"))
            # 跨圈命中：routed 圈会话里 load 了另一圈的条目（设计文档 §6）
            it_scope = store.norm_scope(out.get("scope") or "global")
            routed = store.norm_scope(getattr(args, "routed", "") or "")
            if routed and it_scope and it_scope != "global" and it_scope != routed:
                ledger.circle_hit_log(conn, routed, it_scope, [out["id"]], "load")
            ledger.log(conn, "inject_event", {
                "ts": paths.now(), "session": args.session or "", "source": "eco-load",
                "form": "load", "items": 1, "chars": len(body),
                "tokens": len(body) // 2, "ids": out["id"]})
            conn.commit()
        finally:
            conn.close()
    return out


def cmd_search(root, args) -> dict:
    conn = root.connect_ro()
    stats = retrieve.hit_stats(conn) if conn is not None else {}
    if conn is not None:
        conn.close()
    # **L1 行也参与命中判定**（修复"L1 无人读"）：detail 命中先取一个大池子，
    # 与 L1 命中按分数合并排序后再截到 limit——否则池子先截断会让 L1 命中
    # 永远挤不进结果（或反过来）。
    pool = max(1, args.limit, 32)
    hits = retrieve.search(root.items(), args.query, limit=pool,
                           scope=args.scope, hit_stats=stats)
    l1_hits = retrieve.l1_search_hits(retrieve.l1_resident(root), args.query)
    if l1_hits:
        # **同文去重**（漏洞扫描低#9）：promote 条目同时存在于 detail 与
        # MEMORY.md，两路都命中会让同一段文本出现两次（还挤占注入预算）。
        # 保留 detail 侧（可 load、有 hit 记账），丢弃 L1 影子命中。
        detail_keys = {paths.norm_key(h.get("line") or "")[:30] for h in hits}
        l1_hits = [h for h in l1_hits
                   if paths.norm_key(h.get("line") or "")[:30] not in detail_keys]
        hits = sorted(hits + l1_hits, key=lambda x: -x["score"])
    # **截断无条件执行**（质量门①审查 P2）：L1 无命中时旧写法漏截，
    # `--limit 8` 会返回最多 32 条（pool 口径），注入账跟着虚增
    hits = hits[:max(1, args.limit)]
    if hits and not root.readonly:
        conn = root.connect()
        try:
            # L1 命中没有可 load 的 id，也不构成"使用证据"——不进 hit 记账
            # 跨圈命中（circle_hit）：routed 圈 A 的会话命中了另一圈 B 的条目
            # ——驯化/桥接的唯一数据源（设计文档 §6）。global 条目不算跨圈。
            routed = store.norm_scope(getattr(args, "routed", "") or "")
            cross: dict[str, list] = {}
            for h in hits:
                if h.get("source") == "L1":
                    continue
                conn.execute("INSERT INTO hit(ts,id,kind,query) VALUES (?,?,?,?)",
                             (paths.now(), h["id"], h["kind"], args.query[:120]))
                hsc = store.norm_scope(h.get("scope") or "global")
                if (routed and hsc and hsc != "global"
                        and hsc != store.norm_scope(routed)):
                    cross.setdefault(hsc, []).append(h["id"])
            for hsc, hit_ids in cross.items():
                ledger.circle_hit_log(conn, routed, hsc, hit_ids, "search")
            ledger.log(conn, "inject_event", {
                "ts": paths.now(), "session": args.session or "", "source": "eco-search",
                "form": "search", "items": len(hits),
                "chars": sum(len(h["line"]) for h in hits),
                "tokens": sum(len(h["line"]) for h in hits) // 2,
                "ids": ",".join(h["id"] for h in hits)[:500]})
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "query": args.query, "count": len(hits), "items": hits,
            "terms": retrieve.terms(args.query)[:8]}


def cmd_circles(root, args) -> dict:
    counts: dict[str, int] = {}
    for it in root.items():
        if it.get("status") != "active":
            continue
        sc = it.get("scope") or "global"
        counts[sc] = counts.get(sc, 0) + 1
    cfg = store.load_circles(root.path)
    limit_idx = {store.norm_scope(c.get("id")): c for c in cfg.get("circles", [])}
    conn = root.connect_ro()
    stats = retrieve.hit_stats(conn) if conn is not None else {}
    if conn is not None:
        conn.close()
    items = []
    for sc, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        budget, limit = store.circle_budget(root.path, sc)
        # **必须带真实 hit 统计**：空 stats 会让目录排序与 `catalog` 不同，
        # 于是这里算出的"目录占用"与插件真正注入的字符数对不上（实测差 58 字符）。
        payload = retrieve.catalog_payload(root.items(), limit=limit, scope=sc,
                                           budget_chars=budget, stats=stats)
        items.append({
            "id": sc, "count": n,
            "declared": sc in limit_idx,
            "budget_chars": budget, "catalog_limit": limit,
            "catalog_count": payload["count"], "catalog_chars": payload["used_chars"],
            "readonly": store.circle_readonly(root.path, sc),
            "sources": (limit_idx.get(sc, {}) or {}).get("sources", []),
            "match": (limit_idx.get(sc, {}) or {}).get("match", {}),
            "display_name": (limit_idx.get(sc, {}) or {}).get("display_name", ""),
        })
    # 声明了但还没有条目的圈也要出现（否则 GUI 看不见"空圈"）
    for cid, c in limit_idx.items():
        if cid not in counts:
            budget, limit = store.circle_budget(root.path, cid)
            items.append({"id": cid, "count": 0, "declared": True,
                          "budget_chars": budget, "catalog_limit": limit,
                          "catalog_count": 0, "catalog_chars": 0,
                          "readonly": bool(c.get("readonly", False)),
                          "sources": c.get("sources", []),
                          "match": c.get("match", {}),
                          "display_name": c.get("display_name", "")})
    return {"ok": True, "count": len(items), "items": items,
            "global_budget_chars": cfg.get("global_budget_chars"),
            # 读侧降级必须可见（二轮安全测试：手改坏配置的用户看不到截断/损坏
            # 警示，命令输出装作"一切正常"）
            **({"warn": cfg["warn"]} if cfg.get("warn") else {}),
            **({"error": cfg["error"]} if cfg.get("error") else {}),
            # 尾段总闸的唯一权威出口（设计文档 §8）：插件从这里读，
            # **不许在 JS 里自定预算**——否则与核心口径漂移（条数/字符数的老坑）。
            "tail_max_chars": config.get(root.path, "CIRCLE_TAIL_MAX_CHARS",
                                         config.CIRCLE_TAIL_MAX_CHARS)}


def cmd_route(root, args) -> dict:
    scope = store.route_circle(root.path, args.cwd) if args.cwd else "global"
    return {"ok": True, "cwd": args.cwd or "", "scope": scope}


def cmd_charter(root, args) -> dict:
    """主圈物理法则段（多生态圈 · 设计文档 §2/§8）。

    三层常驻内容里**只有物理法则进这个命令**：气候（USER.md 摘要）与基础物种
    （MEMORY.md/全局目录）走既有 L1 pre-step 与目录段——它们随使用演化，
    进前缀会让字节稳定铁律破产。charter 文本 = 配置规则列表的确定性拼装，
    **同 root 同配置下字节恒定**（前缀缓存友好的前提）；改规则 = 改 eco.toml
    `[charter] charter_rules = [...]`（toml 短名归一要求键名自带 `charter_`
    前缀——写 `[charter] rules` 会静默无效，正是"死配置"教训的形态），
    代价一次重 prefill，由配置变更者有意承担。
    """
    rules = config.get(root.path, "CHARTER_RULES", config.CHARTER_RULES)
    if not isinstance(rules, (list, tuple)):
        rules = [str(rules)]
    # 控制字符/内嵌换行清洗（漏洞扫描低#8）：charter 进 systemPrompt 前缀，
    # ESC/换行会破坏"一法则一行"与终端渲染——折叠成空格（纵深防御，
    # 配置是用户自己的，但输出格式是系统契约）。
    clean = [re.sub(r"[\x00-\x1f\x7f]+", " ", str(r)).strip() for r in rules]
    lines = [l for l in clean if l]
    text = "\n".join("· " + l for l in lines)
    # **总闸**（二轮安全测试：200KB 规则直通注入前缀 = 无界 prefill 膨胀）
    try:
        cap = int(config.get(root.path, "CHARTER_MAX_CHARS", config.CHARTER_MAX_CHARS))
    except (TypeError, ValueError):
        cap = config.CHARTER_MAX_CHARS
    if len(text) > cap:
        text = text[:cap] + "\n…（超 CHARTER_MAX_CHARS 截断）"
        lines = text.splitlines()
    return {"ok": True, "schema": 1, "source": "charter",
            "text": text, "chars": len(text),
            "rules": [l[2:] if l.startswith("· ") else l for l in lines
                      if l and not l.startswith("…")],
            "version": __version__}


def cmd_circle_attach(root, args) -> dict:
    """挂载圈（agent 主导激活的服务端，设计文档 §4）。

    **校验 fail-closed**：未声明的圈、`attachable: false` 的圈一律拒绝——
    attach 是跨圈可见性的授权动作，必须显式声明在案。成功 = 落账
    （`circle.attach`）+ 返回该圈目录载荷（该圈 + global，绝不含第三圈）。
    """
    if root.readonly:
        return root.readonly_payload()
    cid = store.norm_scope(getattr(args, "circle", ""))
    if not cid or cid == "global":
        return {"ok": False, "error": "invalid-circle", "circle": cid,
                "detail": "global 无需挂载（对所有圈可见）", "exit": 5}
    declared = {store.norm_scope(c.get("id")): c
                for c in store.load_circles(root.path).get("circles", [])}
    c = declared.get(cid)
    if c is None:
        return {"ok": False, "error": "undeclared-circle", "circle": cid,
                "detail": "未在 circles.json 声明的圈不可挂载（没声明就不猜）", "exit": 5}
    if c.get("attachable") is False:
        return {"ok": False, "error": "not-attachable", "circle": cid,
                "detail": "该圈声明了 attachable: false（只可写不可挂）", "exit": 5}
    routed = store.norm_scope(getattr(args, "routed", "") or "")
    conn = root.connect()
    try:
        ledger.event(conn, "circle.attach", cid,
                     "routed=%s session=%s" % (routed or "-",
                                               getattr(args, "session", "") or "-"))
        conn.commit()
    finally:
        conn.close()
    budget, limit = store.circle_budget(root.path, cid)
    conn = root.connect_ro()
    stats = retrieve.hit_stats(conn) if conn is not None else {}
    if conn is not None:
        conn.close()
    payload = retrieve.catalog_payload(root.items(), limit=limit, scope=cid,
                                       budget_chars=budget, stats=stats)
    return {"ok": True, "circle": cid,
            "declared": True, "attachable": True, "routed": routed,
            "readonly": store.circle_readonly(root.path, cid),
            "count": payload["count"], "total": payload["total"],
            "items": payload["items"],
            "text": retrieve.render_catalog_text(payload, limit=limit)}


def cmd_circle_detach(root, args) -> dict:
    """分离圈：落账（`circle.detach`）。分离不撤回历史注入——append-only，
    旧消息随 compaction 自然滚出（设计文档 §4）。"""
    if root.readonly:
        return root.readonly_payload()
    cid = store.norm_scope(getattr(args, "circle", ""))
    if not cid:
        return {"ok": False, "error": "invalid-circle", "circle": "", "exit": 5}
    conn = root.connect()
    try:
        ledger.event(conn, "circle.detach", cid,
                     "session=%s" % (getattr(args, "session", "") or "-"))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "circle": cid}


def cmd_circles_stats(root, args) -> dict:
    """跨圈命中聚合（驯化/桥接的数据源，设计文档 §6）。

    按 (routed, scope) 聚合 `circle_hit`：A 圈会话里命中了 B 圈条目多少次。
    `count ≥ CIRCLE_ATTACH_SUGGEST_AFTER` → `suggest=true`——宇宙泡的
    "泡间桥接"与未来的驯化建议都从这里取数。只读。
    """
    conn = root.connect_ro()
    try:
        threshold = int(config.get(root.path, "CIRCLE_ATTACH_SUGGEST_AFTER",
                                   config.CIRCLE_ATTACH_SUGGEST_AFTER))
    except (TypeError, ValueError):
        threshold = config.CIRCLE_ATTACH_SUGGEST_AFTER
    # 阈值毒性下限（二轮安全测试：配 0 → 全部行 suggest:true 刷社工信号）
    if threshold < 1:
        threshold = 1
    rows, rows_err = [], ""
    if conn is not None:
        try:
            # suggest 按 **distinct id** 计数（二轮安全测试：同一 id 反复检索
            # 能把 hits 刷到 1200 伪造"建议挂载"——去重后的"真看过几条"才有资格建议）
            raw = conn.execute(
                "SELECT routed, scope, count(*), count(DISTINCT id), max(ts) "
                "FROM circle_hit GROUP BY routed, scope ORDER BY 3 DESC").fetchall()
            for routed, scope, n, distinct, last_ts in raw:
                rows.append({"routed": str(routed or ""), "scope": str(scope or ""),
                             "hits": int(n or 0), "distinct": int(distinct or 0),
                             "last_ts": str(last_ts or ""),
                             "suggest": int(distinct or 0) >= threshold})
        except Exception as exc:  # noqa: BLE001 - 坏表不该让快照崩
            rows, rows_err = [], str(exc)[:120]
        finally:
            conn.close()
    else:
        rows_err = "no-ledger"
    total = len(rows)
    # **limit 语义修正**（漏洞扫描低#4）：负数 limit 走 Python 负切片、
    # count 不随截断变——count = 截断后行数，total = 聚合总行数。
    try:
        limit = max(0, int(getattr(args, "limit", 0) or 0) or 50)
    except (TypeError, ValueError):
        limit = 50
    rows = rows[:limit]
    return {"ok": True, "count": len(rows), "total": total, "rows": rows,
            "suggest_after": threshold,
            **({"warn": rows_err} if rows_err else {})}


def cmd_l1(root, args) -> dict:
    """L1 常驻段载荷 + 插件首轮注入文本（唯一出口，§ 语义不进 JS）。

    预算默认 `L1_INJECT_BUDGET_CHARS`（1500）：目录段是 600 字符口径，
    L1 是常驻层、配额由门② 管，注入预算单独给。超限按 § 段截断并标注
    （retrieve.render_l1_injection）。**只读**——L1 的写入只属于四道门。
    """
    l1 = retrieve.l1_resident(root)
    try:
        budget = int(args.budget) if getattr(args, "budget", 0) else \
            int(config.get(root.path, "L1_INJECT_BUDGET_CHARS",
                           config.L1_INJECT_BUDGET_CHARS))
    except (TypeError, ValueError):
        budget = config.L1_INJECT_BUDGET_CHARS
    text, truncated, omitted = retrieve.render_l1_injection(l1, max(60, budget))
    return {"ok": True, "budget_chars": max(60, budget),
            "inject_text": text, "truncated": truncated, "omitted_sections": omitted,
            "memory": l1["memory"], "user": l1["user"],
            "total_chars": l1["total_chars"]}


def cmd_materialize(root, args) -> dict:
    if root.readonly:
        return root.readonly_payload()
    return retrieve.materialize(root, limit=args.limit or 0,
                                budget_chars=args.max_chars or 0)


def cmd_stats(root, args) -> dict:
    items = root.items()
    by_status: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    for it in items:
        by_status[it.get("status", "?")] = by_status.get(it.get("status", "?"), 0) + 1
        by_scope[it.get("scope", "global")] = by_scope.get(it.get("scope", "global"), 0) + 1
    out = {"ok": True, "root": str(root.path), "readonly": root.readonly,
           "version": __version__, "detail": len(items),
           "pending": sum(1 for _ in store.iter_candidates(root.pending)),
           "by_status": by_status, "by_scope": by_scope}
    conn = root.connect_ro()
    if conn is not None:
        try:
            for t, key in (("gate_log", "gate1"), ("quota_log", "gate2"),
                           ("distill_log", "gate3"), ("review_log", "gate4")):
                out[key] = conn.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
            out["injects"] = conn.execute("SELECT count(*) FROM inject_event").fetchone()[0]
            out["hits"] = conn.execute("SELECT count(*) FROM hit").fetchone()[0]
            out["packs"] = conn.execute("SELECT count(*) FROM pack").fetchone()[0]
        except Exception:  # noqa: BLE001 - 只读口径，缺表不算错
            pass
        finally:
            conn.close()
    return out


def cmd_health(root, args) -> dict:
    """健康快照。**任何单项失败都不能让整个快照失败**（GUI 会白屏）。"""
    notes: list[str] = []
    conn = None
    recon = None
    lock_state: dict = {"held": False}
    last_blocked = None
    try:
        conn = root.connect_ro()
    except Exception as exc:  # noqa: BLE001 - 坏库不该让 health 崩
        notes.append("账本读不了：%s" % str(exc)[:120])
    try:
        try:
            gate = ledger.gate_status(conn, stale_days=config.effective_gate_stale_days())
        except Exception as exc:  # noqa: BLE001
            notes.append("四门判定失败：%s" % str(exc)[:120])
            gate = ledger.gate_status(None)
        try:
            inject = ledger.inject_summary(conn) if conn is not None else {}
        except Exception as exc:  # noqa: BLE001
            notes.append("注入账读取失败：%s" % str(exc)[:120])
            inject = {}
        try:
            cost = ledger.cost_summary(conn) if conn is not None else {"rows": []}
        except Exception as exc:  # noqa: BLE001
            notes.append("成本账读取失败：%s" % str(exc)[:120])
            cost = {"rows": []}
        # **L1 消费端对账**（v17 对照 · 第 10 步）：账本声称 promote 进 L1 的
        # 条目必须"还在 L1 或有挤出账"。L1 无人读修好之后，这是 L1 唯一剩下的
        # 失真通道——写了、账上有、读者读不到，没有这道核对谁也不会发现。
        try:
            recon = gates.l1_reconcile(root, conn) if conn is not None else None
        except Exception as exc:  # noqa: BLE001
            notes.append("L1 对账失败：%s" % str(exc)[:120])
            recon = None
        # **门锁可见性**：当前持锁（并发会被挡）+ 上次被锁挡的记录（24h 内才报，
        # 否则一次历史阻塞永久挂在高频快照里）。
        try:
            lock_state = proclock.status(root.path, "gate")
            last_blocked = (ledger.last_event(conn, "gate.lock-blocked")
                            if conn is not None else None)
        except Exception as exc:  # noqa: BLE001
            notes.append("锁状态读取失败：%s" % str(exc)[:120])
            lock_state, last_blocked = {"held": False}, None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    try:
        items = root.items()
    except Exception as exc:  # noqa: BLE001
        notes.append("detail 读取失败：%s" % str(exc)[:120])
        items = []
    # **H2：损坏的 detail 必须可见**。它们已被排除在目录/检索/门外（不再以
    # 「scope=global」身份泄漏），但"看不见"不等于"没发生"——不报出来就永远修不好。
    try:
        damaged = store.scan_damaged(root.detail)
    except Exception as exc:  # noqa: BLE001
        notes.append("损坏扫描失败：%s" % str(exc)[:120])
        damaged = []
    if damaged:
        notes.append("%d 条 detail 损坏（已隔离，不进目录/检索）：%s"
                     % (len(damaged), "、".join(d["id"] for d in damaged[:5])))
    status = "ok"
    for g in gate["gates"]:
        if g["dead"]:
            status = "warn"
            notes.append("%s 空转：%s" % (g["name"], g["reason"]))
    if damaged:
        status = "warn"
    if recon is not None and not recon.get("ok"):
        # 对账没跑成（坏库/查询失败）必须可见，但 warn 与否交给既有坏库告警——
        # 这里只落 note，不重复报警。
        notes.append("L1 对账没跑成：%s" % recon.get("why"))
    elif recon is not None and recon.get("missing"):
        status = "warn"
        notes.append("L1 对账：%d 条 promote 声称进 L1，但既不在 MEMORY.md 也无挤出账"
                     "（手改/丢失？重跑门②可回填）：%s"
                     % (len(recon["missing"]),
                        "、".join(m["id"] for m in recon["missing"][:5])))
    if lock_state.get("held"):
        notes.append("四门锁当前被持有（pid=%s，%s 分钟）——此期间的并发门运行会被挡"
                     % (lock_state.get("pid"), lock_state.get("age_min")))
    if last_blocked:
        age = paths.days_between(last_blocked.get("ts") or "")
        if age is not None and age <= 1.0:
            notes.append("上次四门被锁挡：%s（%s）"
                         % (last_blocked.get("ts"),
                            last_blocked.get("target") or last_blocked.get("detail")))
    try:
        mat = retrieve.read_materialized(root, "global")
    except Exception:  # noqa: BLE001
        mat = None
    if mat is None and items:
        notes.append("物化视图缺失或过期（.eco/catalog/global.json）——插件会注入不到目录")
        status = "warn" if status == "ok" else status
    return {"ok": True, "root": str(root.path), "version": __version__,
            "readonly": root.readonly, "status": status, "notes": notes,
            "gates": gate, "inject": inject, "cost": cost,
            "detail": len(items),
            # **增量字段，不改既有结构**：GUI 侧照旧读 detail/status/notes，
            # 需要细看时再读 damaged[]。H2 的"隔离"在这里是**可观测的**。
            "damaged": damaged,
            "materialized": mat is not None,
            "l1_reconcile": recon,
            "gate_lock": {**lock_state, "last_blocked": last_blocked},
            "schema": rootmod.SCHEMA_VERSION}


def cmd_version(root, args) -> dict:
    """版本自检：三处版本号必须一致（A2A §B5 从纪律变成检查）。"""
    from . import version as vmod
    return vmod.report(root)


def cmd_circles_write(root, args) -> dict:
    """圈配置写入口（GUI G4 唯一通道）。

    **读不到就给错误，不要 fail-open 成空配置**：
    旧写法 `json.loads(read_text(file) or "{}")` 在文件不存在/不可读时得到 `{}`，
    于是"改一个不存在的文件"会**把全部圈配置静默清空**（有 .bak 可回滚，但调用方
    收到的信号是成功——这类"成功其实是清空"的 API 迟早会吃掉人的配置）。
    显式空对象也不接受：空配置不是合法意图，改成 `--json '{}'` 才会（那至少是明确的）。
    """
    if root.readonly:
        return root.readonly_payload()
    if args.file:
        fp = Path(args.file)
        if not fp.is_file():
            return {"ok": False, "error": "file-unreadable",
                    "detail": "文件不存在：%s" % fp, "exit": 5}
        raw = paths.read_text(fp)
        if not raw.strip():
            return {"ok": False, "error": "file-empty",
                    "detail": "文件为空：%s" % fp, "exit": 5}
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            # RecursionError：深嵌套爆栈也归 invalid rc=5（二轮安全测试 R6 小疵：
            # 落成 internal rc=4 会误导成"程序坏了"——这是可预期的坏输入）
            return {"ok": False, "error": "bad-json", "detail": str(exc)[:200], "exit": 5}
    else:
        if not (args.json or "").strip():
            return {"ok": False, "error": "no-input",
                    "detail": "必须给 --file 或 --json（不写就是不写，别猜）", "exit": 5}
        try:
            data = json.loads(args.json)
        except (ValueError, RecursionError) as exc:
            return {"ok": False, "error": "bad-json", "detail": str(exc)[:200], "exit": 5}
    if not isinstance(data, dict):
        return {"ok": False, "error": "invalid", "detail": "顶层必须是对象", "exit": 5}
    errs = store.validate_circles(data)
    if errs:
        return {"ok": False, "error": "invalid", "errors": errs, "exit": 5}
    p = store.save_circles(root.path, data)
    conn = root.connect()
    try:
        ledger.event(conn, "circles.write", str(p), "n=%d" % len(data.get("circles", [])))
        conn.commit()
    finally:
        conn.close()
    retrieve.materialize(root)
    return {"ok": True, "path": str(p), "circles": len(data.get("circles", []))}


def cmd_pack(root, args) -> dict:
    if args.action == "list":
        return packs.list_packs(root, verify=not args.no_verify)
    if root.readonly:
        return root.readonly_payload()
    if args.action == "install":
        if not args.dir:
            return {"ok": False, "error": "install 需要 --dir"}
        res = packs.install(root, args.dir, mode=args.mode, enable=not args.disabled)
        return res if res.get("ok") else dict(res, exit=5)
    if args.action in ("enable", "disable"):
        if not args.id:
            return {"ok": False, "error": "%s 需要 --id" % args.action}
        return packs.enable(root, args.id, args.action == "enable")
    if args.action == "uninstall":
        if not args.id:
            return {"ok": False, "error": "uninstall 需要 --id"}
        return packs.uninstall(root, args.id)
    if args.action == "mounted":
        return {"ok": True, "entries": packs.mounted_entries(root, args.scope)}
    return {"ok": False, "error": "unknown-pack-action"}


def cmd_prune(root, args) -> dict:
    if root.readonly:
        return root.readonly_payload()
    conn = root.connect()
    try:
        n = ledger.prune_hit(conn, keep_days=config.HIT_KEEP_DAYS)
        n2 = ledger.prune_circle_hit(conn, keep_days=int(
            config.get(root.path, "CIRCLE_HIT_KEEP_DAYS", config.CIRCLE_HIT_KEEP_DAYS)))
        moved = 0
        for target in (root.memory_md, root.user_md):
            moved += len(paths.prune_backups(target, config.BACKUP_KEEP))
        ledger.event(conn, "prune", "hit", "deleted=%d circle_hit=%d moved_backups=%d"
                     % (n, n2, moved))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "hit_rows_deleted": n, "circle_hit_rows_deleted": n2,
            "backups_archived": moved}


# ── 参数表 ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eco", description="记忆生态核心 CLI（v%s）" % __version__)
    p.add_argument("--root", default=None,
                   help="数据根（默认：MEMORY_ECOLOGY_ROOT > ~/.dsh/memory-eco-root > 布局派生）")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="建根（幂等）")

    d = sub.add_parser("deposit", help="投一条候选")
    d.add_argument("--session", default="anon")
    d.add_argument("--text", default=None)
    d.add_argument("--kind", default="semantic")
    d.add_argument("--scope", default="global")

    r = sub.add_parser("reconcile", help="门①：候选 → detail")
    r.add_argument("--no-archive", action="store_true")
    r.add_argument("--dry-run", action="store_true")

    g = sub.add_parser("gate", help="跑某一门（1|2|3|4|all）")
    g.add_argument("gate")
    g.add_argument("--dry-run", action="store_true")

    c = sub.add_parser("catalog", help="目录载荷")
    c.add_argument("--limit", type=int, default=0)
    c.add_argument("--scope", default=None)
    c.add_argument("--max-chars", type=int, default=None, dest="max_chars")
    c.add_argument("--log-chars", type=int, default=None, dest="log_chars")
    c.add_argument("--session", default="")

    ld = sub.add_parser("load", help="读正文")
    ld.add_argument("id")
    ld.add_argument("--session", default="")
    ld.add_argument("--routed", default="", help="会话的 routed 圈（跨圈命中账用）")

    s = sub.add_parser("search", help="词面检索")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=config.SEARCH_LIMIT)
    s.add_argument("--scope", default=None)
    s.add_argument("--session", default="")
    s.add_argument("--routed", default="", help="会话的 routed 圈（跨圈命中账用）")

    sub.add_parser("circles", help="列圈")
    rt = sub.add_parser("route", help="cwd → 圈")
    rt.add_argument("--cwd", default="")

    l1p = sub.add_parser("l1", help="L1 常驻段（MEMORY.md/USER.md）载荷与注入文本")
    l1p.add_argument("--budget", type=int, default=0,
                     help="注入文本字符预算（默认 L1_INJECT_BUDGET_CHARS=1500）")

    sub.add_parser("charter", help="主圈物理法则段（前缀字节稳定层）")

    ca = sub.add_parser("circle-attach", help="挂载圈（校验 fail-closed + 落账 + 返回圈目录）")
    ca.add_argument("--circle", required=True)
    ca.add_argument("--routed", default="", help="当前会话的 routed 圈（落账用）")
    ca.add_argument("--session", default="")

    cd = sub.add_parser("circle-detach", help="分离圈（落账）")
    cd.add_argument("--circle", required=True)
    cd.add_argument("--session", default="")

    cs = sub.add_parser("circles-stats", help="跨圈命中聚合（驯化/桥接数据源）")
    cs.add_argument("--limit", type=int, default=0)

    m = sub.add_parser("materialize", help="物化目录到 .eco/catalog/")
    m.add_argument("--limit", type=int, default=0)
    m.add_argument("--max-chars", type=int, default=0, dest="max_chars")

    sub.add_parser("stats", help="统计")
    sub.add_parser("health", help="健康快照（GUI/G2 用）")
    sub.add_parser("version", help="版本三处对齐自检")
    sub.add_parser("prune", help="账本/备份归档（不做物理删除记忆）")

    bb = sub.add_parser("bring-back", help="把休眠/归档的条目恢复成 active（回目录）")
    bb.add_argument("name", help="条目 id 或文件名")
    bb.add_argument("--reason", default="", help="恢复原因（写进复核账）")

    cw = sub.add_parser("circles-write", help="写圈配置（GUI G4 通道）")
    cw.add_argument("--file", default=None, help="从文件读 JSON")
    cw.add_argument("--json", default=None, help="内联 JSON")

    pk = sub.add_parser("pack", help="记忆包（mount/integrate）")
    pk.add_argument("action", choices=("list", "install", "enable", "disable",
                                       "uninstall", "mounted"))
    pk.add_argument("--dir", default=None)
    pk.add_argument("--id", default=None)
    pk.add_argument("--mode", default="mount", choices=("mount", "integrate", "both"))
    pk.add_argument("--disabled", action="store_true")
    pk.add_argument("--scope", default=None)
    pk.add_argument("--no-verify", action="store_true", dest="no_verify")
    return p


_DISPATCH = {
    "init": cmd_init,
    "deposit": cmd_deposit,
    "reconcile": cmd_reconcile,
    "gate": cmd_gate,
    "catalog": cmd_catalog,
    "load": cmd_load,
    "search": cmd_search,
    "circles": cmd_circles,
    "route": cmd_route,
    "l1": cmd_l1,
    "charter": cmd_charter,
    "circle-attach": cmd_circle_attach,
    "circle-detach": cmd_circle_detach,
    "circles-stats": cmd_circles_stats,
    "materialize": cmd_materialize,
    "stats": cmd_stats,
    "health": cmd_health,
    "version": cmd_version,
    "prune": cmd_prune,
    "bring-back": cmd_bring_back,
    "circles-write": cmd_circles_write,
    "pack": cmd_pack,
}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - pythonw 下 stdout 为 None
        pass
    args = build_parser().parse_args(argv)
    root = _open(args)
    if args.cmd in ("deposit",) and getattr(args, "text", None) is None:
        # 支持从 stdin 读（避免超长正文进命令行）
        args.text = sys.stdin.read()
    try:
        out = _DISPATCH[args.cmd](root, args)
    except OSError as exc:
        out = {"ok": False, "error": "io-error", "detail": str(exc)[:300]}
    except ValueError as exc:
        out = {"ok": False, "error": "invalid", "detail": str(exc)[:300]}
    except Exception as exc:  # noqa: BLE001 - 明确失败，不静默
        import traceback
        out = {"ok": False, "error": "internal", "detail": "%s: %s" % (type(exc).__name__, exc),
               "trace": traceback.format_exc()[-800:]}
    _emit(out)
    if out.get("ok"):
        return 0
    if out.get("readonly"):
        return 3
    if out.get("exit"):
        return int(out["exit"])
    if out.get("error") in ("unknown-gate", "unknown-pack-action"):
        return 2
    return 4


if __name__ == "__main__":
    sys.exit(main())
