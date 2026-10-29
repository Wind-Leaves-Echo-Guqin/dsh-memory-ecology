"""gates —— 四道门（记忆治理流水线）。**本模块就是 v3.2.0 的核心交付**。

为什么旧项目的三个门空转（这一次全部查清了，不再靠猜）：

  门② `promote` 恒为 0 —— 两个前置条件在生产数据下**结构性不可满足**：
      ① 快路径要求 `occurrences>=3 且 session_count>=2`，但 detail 的真实分布是
         **228/229 条 occurrences=1、1 条 =2**（本次实测）。这两个字段只有「同一事实
         被再次写入且命中相似合并」才会增长，而相似合并本身依赖词面层命中 —— 环。
      ② 慢路径（稳路径）要求 L2 条目在 `<root>/memories/MEMORY.md`（L1 文本）里
         **有同源条目**才可提升；旧实现的 L1 是「只被挤出、从不被写入」的单向漏斗，
         所以慢路径同样空转。
      → 本实现把「稳路径」重定义为**与 L1 文本无关**：`status=active + 驻留 ≥14 天
        + 被检索命中 ≥2 次（或 occurrences/session 达标）`。有使用证据即可提升，
        这是"记忆被用过才算数"的正面判据，而不是"必须先在 L1 里"。

  门③ `promote` 恒为 0 —— 64 个候选**全部** `status: observing`、`created` 全是
      10-05..10-25（最新距今 2 天），而 `OBSERVE_DAYS=30`，所以**没有一条通过观察期**。
      这不是 bug，是"30 天观察期 + 只跑了 20 天"。真正的问题是**没有任何旁路**：
      一条被 3 个独立会话反复印证的事实也要干等 30 天。
      → 本实现保留观察期，但加**交叉印证旁路**（`corroborate>=2` 个不同来源 ⇒ 可提前
        晋升），并把"为什么没晋升"写成 `blocked_by` 字段落账（GUI G2 直接显示）。

  门④ 两个月 2 行 —— 它在 cron 里是"每月 1 号"，而 `last_verified` 从不刷新，
      一旦超期就永久超期；更糟的是**最活跃的记忆反被归档**（它没被"验证"过）。
      → 本实现：① 频率改事件驱动（每次会话/维护都跑）；② `last_verified` 由
        「被检索命中 / 被加载」自动刷新（有使用即视为已验证）；③ 窗口内被用过
        的条目免降级（旧实现有这条但只认 `last_hit`，且写在本文件外）。

  **永不物理删除**：门④ 只把 `status` 改成 `archived`（文件留在原处，进目录时被过滤）。
      `quarantine/` 只做**移动**，从不 unlink。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, ledger, paths, similarity, store

# ── 门① 写入整合 ─────────────────────────────────────────────────────

def integrate(root, archive: bool = True, dry_run: bool = False,
              limit: int = 500, session: str = "") -> dict:
    """候选 → detail。指纹去重 → 同源判定 → 类型归一 → 新旧取代（superseded + 隔离区）。

    幂等：同一候选跑两次结果一致（指纹是主键，`candidate` 表 + 磁盘 fp 双查）。
    **永不物理删除**：被取代的旧条目只改 frontmatter（status/fate/superseded_by），
    文件原地保留，`quarantine` 只收"候选与既有条目冲突且无法判定谁新"的副本。
    """
    cfg = config.load(root.path)
    merge_th = float(cfg.get("MERGE_THRESHOLD", config.MERGE_THRESHOLD))
    body_max = int(cfg.get("BODY_MAX", config.BODY_MAX))

    items = root.items()
    known: set[str] = set()
    by_fp = {}
    for it in items:
        # **fallback 指纹必须与 candidate_fp 同口径（含 scope）**：
        # 老库（迁移来的）detail 里没有 fp 字段，若这里漏了 scope，则同样的内容
        # 再投一次会走"相似合并"而不是"指纹去重"——语义不同，且 occurrences 会被
        # 无谓自增（实测：有 fp 时 dups=1 merged=0，无 fp 时 new=0 dups=0 merged=1）。
        sc = it.get("scope") or "global"
        fp = it["fm"].get("fp") or store.candidate_fp(sc, it["body"], body_max)[0]
        known.add(fp)
        by_fp[fp] = it
    conn = root.connect()
    try:
        for (fp,) in conn.execute("SELECT fp FROM candidate"):
            known.add(fp)

        scanned = new = dup = merged = superseded = 0
        ids, blocked = [], []
        per_file: dict[str, int] = {}

        for f, lineno, ctype, text, scope, sess in store.iter_candidates(root.pending):
            if scanned >= limit:
                blocked.append({"reason": "limit", "at": "%s:%d" % (f.name, lineno)})
                break
            scanned += 1
            # **指纹/正文截断的唯一口径**：store.candidate_fp（含 scope + body_max）
            fp, body = store.candidate_fp(scope, text, body_max)
            if fp in known or fp in by_fp:
                dup += 1
                # **重复不等于"什么都不用做"**：完全相同的句子被另一个会话再写一次，
                # 是"这件事又被印证了"的直接证据（session_count / occurrences 的唯一
                # 真实来源）。旧实现直接 continue，于是：
                #   · session_count 永远是 1 → 门② 的 A 判据（sess>=2）结构性不可达；
                #   · 门③ 的交叉印证（corroborate）没有数据来源。
                # 注意：这不是"重复入库"，detail 里仍然只有一条。
                if not dry_run and fp in by_fp:
                    it = by_fp[fp]
                    if it.get("status") == "active":
                        fm = dict(it["fm"])
                        fm["occurrences"] = str(_int(fm.get("occurrences")) + 1)
                        fm["session_count"] = str(_bump_sessions(fm, sess or session))
                        fm["last_seen"] = paths.today()
                        fm["last_verified"] = paths.today()
                        root.write_item(it["name"], fm, it["body"])
                        it["fm"] = fm
                        ledger.gate_log(conn, "REFER", it["name"],
                                        "重复印证 session=%s" % (sess or session or "anon"))
                continue

            # 同源判定**只在同圈内**做：同一个词在两个项目里可能指不同东西（A2A §6-A1 坑）
            same, best, best_score = None, None, 0.0
            for it in items:
                if (it.get("scope") or "global") != scope:
                    continue
                if it.get("status") != "active":
                    continue
                s = similarity.ratio(body, it["body"])
                if s > best_score:
                    best, best_score = it, s
            if best is not None and best_score >= merge_th:
                same = best

            if same is not None:
                merged += 1
                # **H3：相似合并也算"这个候选被消费了"**。
                # 旧实现在 `continue` 前什么都不记，于是：
                #   · `unconsumed_in_file` 永远认为该候选还在 → pending 卡住不空；
                #   · 下一次 reconcile 看到同一句话仍是"未知指纹" → 再 +1 occurrences。
                # 结果是不新增任何候选也能无限灌水，而门② 拿 occurrences
                # 当提升判据 → `hits=0` 的条目照样进 L1（实测 2→5 并 promote）。
                # 指纹必须在这里进 `known`——它是"这个候选已落账"的唯一凭据。
                known.add(fp)
                if not dry_run:
                    fm = dict(same["fm"])
                    occ = _int(fm.get("occurrences")) + 1
                    fm["occurrences"] = str(occ)
                    fm["session_count"] = str(_bump_sessions(fm, sess or session))
                    fm["last_seen"] = paths.today()
                    fm["last_verified"] = paths.today()
                    fm["status"] = "active"
                    root.write_item(same["name"], fm, same["body"])
                    same["fm"] = fm
                    ledger.gate_log(conn, "UPDATE", same["name"],
                                    "同源合并 score=%.3f from=%s session=%s"
                                    % (best_score, f.name, sess or session or "anon"))
                continue

            mid_out = "mem-%s-%s" % (paths.today().replace("-", ""), fp[:8])
            meta = {
                "id": mid_out, "type": ctype, "status": "active", "fate": "retained",
                "scope": scope, "created": paths.now(), "first_seen": paths.today(),
                "last_seen": paths.today(), "last_verified": paths.today(),
                "occurrences": "1", "session_count": "1",
                "sessions": _session_hash("", sess or session),
                "fp": fp, "source": f.name,
                "superseded_by": "", "superseded_reason": "", "superseded_at": "",
            }
            if not dry_run:
                root.write_item(mid_out, meta, body)
                conn.execute(
                    "INSERT OR IGNORE INTO candidate VALUES (?,?,?,?,?,?,?)",
                    (fp, mid_out, ctype, scope, body, sess or session or f.name, paths.now()))
                ledger.gate_log(conn, "ADD", mid_out,
                                "type=%s scope=%s file=%s" % (ctype, scope, f.name))
            known.add(fp)
            ids.append(mid_out)
            new += 1
            # **本轮新增的条目也要进 by_fp**：否则同一批候选里"同一句话出现两次"
            # 只会命中 `known`（走 dup 分支但 by_fp 里没有它 → 什么也不记）。
            by_fp[fp] = {"name": mid_out, "fm": meta, "body": body, "status": "active",
                         "type": ctype, "scope": scope}
            per_file[f.relative_to(root.pending).as_posix()] = \
                per_file.get(f.relative_to(root.pending).as_posix(), 0) + 1

        # 显式取代（外部调用 `supersede` 落账；此处只统计供报告）
        superseded = int(conn.execute(
            "SELECT count(*) FROM gate_log WHERE action='SUPERSEDE'").fetchone()[0] or 0)

        if archive and not dry_run:
            # 只消费"里面的每一条都能在 detail/candidate 里找到指纹"的文件。
            # `only` 用**相对路径**（不是文件名）：同名不同圈的两个候选文件必须分别判，
            # 否则只整合了一个却把另一个也改名 → 那个圈的记忆永久丢失（实测复现）。
            live = [p for p in root.pending.rglob("*.md")
                    if "archive" not in p.relative_to(root.pending).parts
                    and "quarantine" not in p.relative_to(root.pending).parts
                    and store.is_pending_active(p.name)]
            consumable = {p.relative_to(root.pending).as_posix() for p in live
                          if store.unconsumed_in_file(p, known, body_max,
                                                      pending_root=root.pending) == 0}
            n = store.mark_consumed(root.pending, only=consumable) if consumable else 0
            # **H3 防回退**：已消费文件里的候选指纹，不能再留在 `known` 里参与判重。
            # 否则同一句话只要被投递第二次（改标点就够），就会再次以 "dup" 身份
            # 去 `+1 occurrences`，于是"不新增任何候选、连跑 reconcile"也能无限灌水
            # （实测 2→3→4→5），而门② 拿 occurrences 当提升判据 → hits=0 也进 L1。
            # 它们既已在 detail 里（走 known 会重复计数），也不该再进本轮统计，
            # 所以直接剔除：等价于"这个文件已经彻底处理完，翻篇"。
            if consumable:
                for rel in consumable:
                    the_file = root.pending / rel
                    for text in store.candidate_lines(the_file):
                        fp2, _ = store.candidate_fp(
                            store.scope_of_file(the_file, root.pending), text, body_max)
                        known.discard(fp2)
        else:
            n = 0
        if not dry_run:
            conn.commit()
        return {"ok": True, "scanned": scanned, "new": new, "dups": dup,
                "merged": merged, "superseded_total": superseded,
                "consumed_files": n, "ids": ids[:50],
                "blocked": blocked[:10], "dry_run": dry_run}
    finally:
        conn.close()


def supersede(root, old_id: str, new_id: str, reason: str = "") -> dict:
    """把 `old_id` 标记为被 `new_id` 取代（**不删文件**，只改 frontmatter + 落账）。

    回滚：`restore_superseded()` 把 fate 改回 retained / status 改回 active。
    """
    if str(old_id) == str(new_id):
        return {"ok": False, "error": "self-reference",
                "detail": "不能让一条记忆取代它自己（会把 status 改成 archived 且 superseded_by 指向自己）"}
    old = root.item(old_id)
    if old is None:
        return {"ok": False, "error": "not-found", "id": old_id}
    new = root.item(new_id)
    if new is None:
        return {"ok": False, "error": "not-found", "id": new_id}
    fm = dict(old["fm"])
    fm.update({"fate": "superseded", "status": "archived",
               "superseded_by": str(new["fm"].get("id") or new["name"]),
               "superseded_reason": (reason or "manual")[:80],
               "superseded_at": paths.today()})
    root.write_item(old["name"], fm, old["body"])
    conn = root.connect()
    try:
        ledger.gate_log(conn, "SUPERSEDE", old["name"],
                        "by=%s reason=%s" % (new["fm"].get("id") or new["name"], reason[:60]))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "superseded": old["name"], "by": new["name"]}


def restore_superseded(root, name: str) -> dict:
    """回滚取代（C4：反向操作本身也要可测）。

    **只对"被取代"的条目有效**：对一条被门④ `archived`（遗忘曲线归档）的条目调用
    restore，会把门④的归档决定抹掉——那是另一个门的状态迁移，不是"取代"的回滚
    （实测：对 archived 条目 restore 会直接复活成 active，门④的历史痕迹消失）。
    真要复活归档条目，用 `bring_back()`，它会留下明确的账。
    """
    it = root.item(name)
    if it is None:
        return {"ok": False, "error": "not-found", "name": name}
    fate = (it["fm"].get("fate") or "").strip().lower()
    if fate != "superseded":
        return {"ok": False, "error": "not-superseded", "name": name, "fate": fate,
                "detail": "这条不是被取代状态（fate=%s）；复活归档条目请用 bring_back"
                          % (fate or "(空)")}
    fm = dict(it["fm"])
    fm.update({"fate": "retained", "status": "active",
               "superseded_by": "", "superseded_reason": "", "superseded_at": ""})
    root.write_item(it["name"], fm, it["body"])
    conn = root.connect()
    try:
        ledger.gate_log(conn, "RESTORE", it["name"], "回滚取代")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "restored": it["name"]}


def bring_back(root, name: str, reason: str = "") -> dict:
    """把归档/休眠的条目**显式**恢复成 active（留一笔账，不伪装成"没被归档过"）。"""
    it = root.item(name)
    if it is None:
        return {"ok": False, "error": "not-found", "name": name}
    fm = dict(it["fm"])
    was = fm.get("status")
    fm.update({"status": "active", "fate": "retained",
               "last_verified": paths.today(), "bring_back_at": paths.today(),
               "bring_back_reason": (reason or "manual")[:80]})
    root.write_item(it["name"], fm, it["body"])
    conn = root.connect()
    try:
        ledger.review_log(conn, "bring_back", it["name"],
                          "was=%s reason=%s" % (was, reason[:60]))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "restored": it["name"], "was": was}


def quarantine_copy(root, name: str, reason: str) -> dict:
    """把条目**复制**到隔离区（不是移动、不是删除）——用于"判不清谁新"的冲突。"""
    it = root.item(name)
    if it is None:
        return {"ok": False, "error": "not-found", "name": name}
    root.quarantine.mkdir(parents=True, exist_ok=True)
    target = root.quarantine / ("%s.%s.md" % (name, paths.stamp()))
    paths.write_atomic(target, store.dump_frontmatter(
        dict(it["fm"], quarantine_reason=reason[:120], quarantined_at=paths.now()),
        it["body"]))
    conn = root.connect()
    try:
        ledger.gate_log(conn, "QUARANTINE", name, reason[:200])
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "quarantined": str(target)}


# ── 门② 配额巩固 ─────────────────────────────────────────────────────

def quota(root, dry_run: bool = False) -> dict:
    """L1 视图由核心计算：**提升 L2→L1** 与 **超限挤出 L1→L2**。

    关键修复（相对旧实现）：提升判据不再要求"L1 里已有同源条目"——那让慢路径永远空转。
    新判据（任一成立即可，且 status=active 且非冷却期）：
      A. occurrences>=3 且 session_count>=2（旧快路径，保留）
      B. 驻留 premium_days>=14 且 被检索命中>=2（**新**：有使用证据就够）
      C. occurrences>=2 且 驻留>=14（**新**：被重申过且沉淀够久）
    防震荡（照抄旧实现的三条，实测有效）：
      1) 挤出时优先排非高价值条目；2) 挤出落 L1 的条目有冷却期；3) 本轮刚提升的
         条目在同轮挤出里被保护；若仍超限则撤销本轮提升。
    """
    cfg = config.load(root.path)
    mem_quota = int(cfg.get("MEMORY_QUOTA", store.MEMORY_QUOTA))
    ratio = float(cfg.get("QUOTA_TRIGGER_RATIO", store.QUOTA_RATIO))
    trigger = int(mem_quota * ratio)
    min_occ = int(cfg.get("PROMOTE_MIN_OCCURRENCES", 3))
    min_sess = int(cfg.get("PROMOTE_MIN_SESSIONS", 2))
    promo_types = tuple(cfg.get("PROMOTE_TYPES", ("semantic", "procedural")))
    stable_days = int(cfg.get("PROMOTE_STABLE_DAYS", 14))
    min_hits = int(cfg.get("PROMOTE_MIN_HITS", 2))
    cooldown = int(cfg.get("EXTRUDE_COOLDOWN_DAYS", 30))
    promo_max = int(cfg.get("PROMOTE_MAX_PER_RUN", 8))

    conn = root.connect()
    try:
        stats = _hit_stats(conn)
        items = root.items()

        # ---- 提升候选 ----
        cands, blocked = [], []
        for it in items:
            fm = it["fm"]
            if it.get("status") != "active":
                continue
            if it.get("type") not in promo_types:
                blocked.append({"id": it["name"], "why": "type-not-promotable"})
                continue
            if fm.get("origin_session_id") == "extrude-from-L1":
                age = paths.days_between(fm.get("created") or fm.get("first_seen") or "")
                if age is not None and age < cooldown:
                    blocked.append({"id": it["name"], "why": "cooldown"})
                    continue
            occ = _int(fm.get("occurrences"))
            sess = _int(fm.get("session_count"))
            hits = int(stats.get(it["name"], (0, ""))[0])
            age = paths.days_between(fm.get("first_seen") or fm.get("created") or "") or 0.0
            why = []
            if occ >= min_occ and sess >= min_sess:
                why.append("A:occurrences")
            if age >= stable_days and hits >= min_hits:
                why.append("B:used")
            if occ >= 2 and age >= stable_days:
                why.append("C:reaffirmed")
            if not why:
                blocked.append({"id": it["name"], "why": "no-criterion",
                                "occ": occ, "sess": sess, "hits": hits,
                                "age_days": round(age, 1)})
                continue
            cands.append((it, why, hits, age))
        cands.sort(key=lambda x: (-x[2], -x[3]))
        cands = cands[:promo_max]

        # ---- 计算新 L1 视图 ----
        entries, nl = _read_l1(root.memory_md)
        before_chars = store.chars_of(entries)
        promoted, skipped = [], []
        # 同源保护：本轮被提升的条目在挤出阶段豁免
        promoted_previews = set()
        for it, why, hits, age in cands:
            preview = paths.norm_text(it["body"])[:120]
            if any(store.is_same_source(it["body"], e) for e in entries):
                skipped.append({"id": it["name"], "why": "already-in-L1"})
                continue
            entries.append(preview)
            promoted_previews.add(paths.norm_key(preview)[:30])
            promoted.append({"id": it["name"], "why": ",".join(why),
                             "hits": hits, "chars": len(preview)})

        # ---- 挤出 ----
        extruded = []
        if store.chars_of(entries) > trigger:
            hi_value = set()
            for it in items:
                if _is_promotable(it, stats, promo_types, min_occ, min_sess,
                                  stable_days, min_hits):
                    hi_value.add(paths.norm_key(it["body"])[:30])
            entries, extruded = _extrude(
                root, items, entries, trigger, dry_run,
                protect=promoted_previews, hi_value=hi_value)
            # 仍超限且有提升 → 撤销本轮提升（旧实现的有效做法）
            if store.chars_of(entries) > trigger and promoted:
                reverted = [p["id"] for p in promoted]
                entries, _ = _read_l1(root.memory_md)
                extruded = _extrude(root, items, entries, trigger, dry_run,
                                    protect=set(), hi_value=set())[1]
                promoted = []
                blocked.append({"why": "promote-reverted-over-quota", "ids": reverted})

        after_chars = store.chars_of(entries)

        if not dry_run:
            if promoted or extruded:
                paths.backup_before_write(
                    root.memory_md, tag="配额前",
                    keep=int(cfg.get("BACKUP_KEEP", config.BACKUP_KEEP)))
                paths.write_atomic(root.memory_md, store.serialize_l1(entries, nl))
            for p in promoted:
                ledger.quota_log(conn, "promote", p["id"],
                                 _line_of(items, p["id"]), "why=%s hits=%d" % (p["why"], p["hits"]))
            for e in extruded:
                ledger.quota_log(conn, "extrude", e["slug"], e["preview"], e["reason"])
            if not promoted and not extruded:
                ledger.quota_log(conn, "idle", "", "",
                                 "heartbeat 零动作（门在跑） chars=%d/%d" % (after_chars, trigger))
            conn.commit()
        return {"ok": True, "trigger": trigger, "quota": mem_quota,
                "chars_before": before_chars, "chars_after": after_chars,
                "promoted": promoted, "extruded": extruded,
                "skipped": skipped, "blocked_sample": blocked[:20],
                "blocked_count": len(blocked), "dry_run": dry_run}
    finally:
        conn.close()


def _is_promotable(it, stats, promo_types, min_occ, min_sess, stable_days, min_hits) -> bool:
    if it.get("status") != "active" or it.get("type") not in promo_types:
        return False
    fm = it["fm"]
    occ = _int(fm.get("occurrences"))
    sess = _int(fm.get("session_count"))
    hits = int(stats.get(it["name"], (0, ""))[0])
    age = paths.days_between(fm.get("first_seen") or fm.get("created") or "") or 0.0
    return ((occ >= min_occ and sess >= min_sess)
            or (age >= stable_days and hits >= min_hits)
            or (occ >= 2 and age >= stable_days))


def _extrude(root, items, entries, trigger, dry_run, protect: set, hi_value: set):
    """按序挤出直到 ≤ trigger：①非高价值同源 → ②非高价值历史日期 → ③非高价值靠后。

    第 ③ 类的排序语义是**近似 LRU**：L1 是 append-only 的，**尾部就是最新条目**，
    所以"靠后 = 最新"应当**最后**才被挤出。旧写法用 `-idx` 把它排到最前
    （索引大的先挤），语义正好反了——那会把刚写进去的记忆立刻挤出去。
    正确排序键：同类内 `idx` 升序（文件里越靠前 = 越老 = 越先被挤出）。

    每条被挤出的条目先保证 detail 里有同源副本（没有就现写一条），**绝不静默丢弃**。
    """
    cur = list(entries)
    extruded = []

    def norm30(s):
        return paths.norm_key(s)[:30]

    detail_pref = {norm30(it["body"]): (it.get("scope") or "global")
                   for it in items if it.get("status") == "active"}
    order = []
    for idx, e in enumerate(cur):
        key = norm30(e)
        if key in protect:
            continue
        already = key in detail_pref
        prio = 0 if already else (1 if _has_hist_date(e) else 2)
        # 同类内：越靠前（越老）越先挤出 —— 这才是"近似 LRU"
        order.append((prio, idx, key, already))
    order.sort()
    for _prio, idx, key, already in order:
        if store.chars_of(cur) <= trigger:
            break
        if key in hi_value:
            continue
        entry = cur[idx]
        if not already and not dry_run:
            # 圈归属：能查到就沿用，查不到才 glob-al（不猜）
            _write_extruded_detail(root, entry, detail_pref.get(key, "global") or "global")
            detail_pref[key] = "global"
        cur[idx] = None
        extruded.append({"slug": _slug_of(entry), "preview": entry[:80],
                         "reason": "挤出-%s → 写入detail%s"
                                   % ("同源" if already else "近似LRU",
                                      "" if already else "（已落盘）")})
    return [e for e in cur if e is not None], extruded


def _has_hist_date(text: str) -> bool:
    return bool(re.search(r"(?:19\d{2}|20[0-2]\d)年|(?:19\d{2}|20(?:0\d|1\d|2[0-5]))[-/.年]\d{1,2}",
                          text or ""))


def _write_extruded_detail(root, entry: str, scope: str = "global") -> None:
    """把被挤出 L1 的条目落回 detail。

    **scope 必须由调用方传**：L1 是纯文本、不带圈元数据，但"这条属于哪个圈"
    在挤出时是已知的（它来自某个圈的 detail 或提升时的圈）。硬编码成 `global`
    会把 proj-a 的条目变成所有圈可见——实测：proj-a 经 L1 挤出后，
    `catalog(scope="proj-b")` 能看到它。
    找不到归属时才退回 global（保守：至少不丢内容）。
    """
    slug = _slug_of(entry)
    p = root.detail / (slug + ".md")
    if p.exists():
        return
    fm = {"id": slug, "type": "semantic", "status": "active", "fate": "retained",
          "scope": store.norm_scope(scope), "created": paths.now(),
          "first_seen": paths.today(), "last_seen": paths.today(),
          "last_verified": paths.today(),
          "occurrences": "1", "session_count": "1",
          "origin_session_id": "extrude-from-L1", "superseded_by": ""}
    root.write_item(slug, fm, entry)


def _slug_of(text: str) -> str:
    """条目 → 文件名安全的 slug。

    **必须只产出 `[A-Za-z0-9._-]`**：`root.write_item` 会校验名字（因为名字会拼进
    文件路径，而 `memory_load` 的 id 来自模型）。旧实现允许中文，于是 slug 里带
    中文 → 被校验拒掉（实测：门② 挤出时 `ValueError: 不安全的条目名`）。
    现在统一成 `e-<哈希6>`（可读性交给 frontmatter 的 body，不靠文件名）。
    """
    return "e-" + paths.fingerprint(paths.norm_text(text))[:12]


def _read_l1(path: Path) -> tuple[list[str], str]:
    return store.parse_l1(paths.read_text(path))


def _line_of(items, ident: str) -> str:
    for it in items:
        if (it["fm"].get("id") or it["name"]) == ident:
            return paths.norm_text(it["body"])[:80]
    return ""


def _hit_stats(conn) -> dict:
    import sqlite3
    try:
        return {r[0]: (int(r[1]), r[2] or "")
                for r in conn.execute("SELECT id, count(*), max(ts) FROM hit GROUP BY id")}
    except sqlite3.Error:
        return {}


def _int(v) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


# ── session_count 的正确口径 ─────────────────────────────────────────
#
# **旧实现（以及本文件的第一版）把 session_count 写成
#   `max(现有, 1)` —— 它永远是 1。**
# 后果极严重：门② 的 A 判据要求 `session_count>=2`，于是这条判据在自己的实现里
# 结构性不可达——正是本项目立项要修的那个病（"门② 恒为 0"）被原样复刻了一遍。
#
# 正确做法：记录**出现过的会话集合的指纹**（`sessions` 字段存排序去重后的
# session 名的 sha1 前缀），每次合并把新会话并进去，`session_count` = 集合大小。
# 为什么不直接存会话名列表：frontmatter 是单行 `key: value`，列表会破坏格式；
# 而存哈希前缀足够判"是不是新会话"（碰撞概率可忽略），且不泄漏会话名。

def _session_hash(existing: str, session: str) -> str:
    """把 session 并入已有的会话集合指纹。空 session 不参与（匿名投递不算一次会话）。"""
    cur = set(x for x in str(existing or "").split(",") if x)
    s = (session or "").strip()
    if s:
        cur.add(paths.fingerprint("sess", s)[:10])
    return ",".join(sorted(cur))


def _bump_sessions(fm: dict, session: str) -> int:
    """把 session 并入 fm 的会话集合，返回新的 session_count。"""
    merged = _session_hash(fm.get("sessions") or "", session)
    fm["sessions"] = merged
    n = len([x for x in merged.split(",") if x])
    # 老库没有 sessions 字段但 session_count 已经是 N：取 max，别把历史计数抹掉
    return max(n, _int(fm.get("session_count")), 1)


# ── 门③ 画像蒸馏 ─────────────────────────────────────────────────────

def distill(root, dry_run: bool = False, llm=None) -> dict:
    """跨会话稳定事实 → USER 画像候选 → （观察期/交叉印证）→ USER.md。

    判稳**由规则做**，LLM 只做措辞（`llm=None` 时用原文截断，绝不因为没网就不动）。
    三个来源都落账：
      · `candidate`（新候选）
      · `promote`（晋升）
      · `blocked`（**为什么没晋升**——旧项目正是缺这一行，导致 89/0 无人能解释）
    """
    cfg = config.load(root.path)
    min_occ = int(cfg.get("DISTILL_MIN_OCCURRENCES", 2))
    min_sess = int(cfg.get("DISTILL_MIN_SESSIONS", 2))
    stable_days = int(cfg.get("DISTILL_STABLE_DAYS", 14))
    observe = int(cfg.get("DISTILL_OBSERVE_DAYS", 30))
    corroborate = int(cfg.get("DISTILL_CORROBORATE", 2))
    watermark = float(cfg.get("DISTILL_USER_WATERMARK", 0.90))
    max_trait = int(cfg.get("DISTILL_MAX_TRAIT", 80))
    max_promote = int(cfg.get("DISTILL_MAX_PROMOTE", 3))
    replace_ratio = float(cfg.get("DISTILL_REPLACE_RATIO", 0.80))
    user_quota = int(cfg.get("USER_QUOTA", store.USER_QUOTA))

    conn = root.connect()
    try:
        user_entries, nl = _read_l1(root.user_md)
        user_chars = store.chars_of(user_entries)
        stats = _hit_stats(conn)
        items = root.items()

        made, promote, rejected, blocked = 0, [], [], []
        if user_chars > user_quota * watermark:
            ledger.distill_log(conn, "skip", "", "USER 占用水位 %.0f%% > %.0f%%"
                               % (user_chars / max(1, user_quota) * 100, watermark * 100))
            conn.commit()
            return {"ok": True, "skipped": "user-watermark", "user_chars": user_chars,
                    "candidates": 0, "promoted": 0, "blocked": []}

        # ---- 阶段 1：产候选（规则判稳）----
        for it in items:
            fm = it["fm"]
            if it.get("status") != "active" or it.get("type") != "semantic":
                continue
            occ = _int(fm.get("occurrences"))
            sess = _int(fm.get("session_count"))
            hits = int(stats.get(it["name"], (0, ""))[0])
            age = paths.days_between(fm.get("first_seen") or fm.get("created") or "") or 0.0
            strong = (occ >= min_occ and sess >= min_sess) or (age >= stable_days and hits >= 1)
            if not strong:
                blocked.append({"id": it["name"], "why": "not-stable",
                                "occ": occ, "sess": sess, "age_days": round(age, 1)})
                continue
            if _candidate_exists(root, it["body"]):
                # **同一事实被更多会话印证 → 提升 corroborate**
                # 旧实现只写死 `corroborate: "1"`，从来没有任何自增点，于是
                # "交叉印证可提前晋升"这条旁路实际上只能靠手工改文件触发
                # （实测：7 个会话重申同一句，corroborate 仍是 1）。
                if not dry_run:
                    _corroborate_candidate(root, it, conn)
                continue
            trait = _trait_of(it["body"], max_trait, llm)
            root.user_candidates.mkdir(parents=True, exist_ok=True)
            cname = _slug_of(trait) + ".md"
            # **corroborate 的初值 = 实际看到过这条事实的独立会话数**（数 `sessions`
            # 集合，不是 `session_count` 字段）。这是"交叉印证旁路"的数据来源：
            #   · 写死 1 → "5 个会话都印证过"和"只见过 1 次"长得一样，旁路永远等不到 2；
            #   · 用 session_count → 手工把计数改成 2 就能绕过观察期，
            #     而"session_count=2"完全可能只是同一个会话内被重申了两次。
            seen = [x for x in str(it["fm"].get("sessions") or "").split(",") if x]
            n_seen = max(1, len(seen))
            meta = {"source": it["name"], "created": paths.today(),
                    "status": "observing", "sessions": str(max(1, sess)),
                    "occurrences": str(occ),
                    "corroborate": str(n_seen),
                    "sessions_seen": ",".join(seen)}
            paths.write_atomic(root.user_candidates / cname,
                               store.dump_frontmatter(meta, trait))
            ledger.distill_log(conn, "candidate", it["name"], "trait=%s" % trait[:60])
            made += 1

        # ---- 阶段 2：候选 → USER.md（观察期满 或 交叉印证）----
        for cf in sorted(root.user_candidates.glob("*.md")):
            if cf.name.endswith((".promoted.md", ".rejected.md", ".approved")):
                continue
            fm, body = store.parse_frontmatter(paths.read_text(cf))
            if (fm.get("status") or "").strip().lower() not in ("observing", ""):
                continue
            age = paths.days_between(fm.get("created") or "") or 0.0
            corr = _int(fm.get("corroborate"))
            src_ok = _source_still_active(root, fm.get("source") or "")
            if not src_ok:
                rejected.append({"cand": cf.name, "why": "source-gone"})
                ledger.distill_log(conn, "rejected", cf.name, "源条目已降级/被取代")
                if not dry_run:
                    # **必须改名**，否则下一轮还会重复 rejected（实测：连带 4 轮
                    # 全在刷同一条，账本被噪音淹没——正是 ledger 自己批评的那类问题）
                    _mark_candidate(cf, "rejected")
                continue
            if age < observe and corr < corroborate:
                blocked.append({"cand": cf.name, "why": "observing",
                                "age_days": round(age, 1), "need_days": observe,
                                "corroborate": corr, "need_corroborate": corroborate})
                continue
            if len(promote) >= max_promote:
                blocked.append({"cand": cf.name, "why": "batch-limit"})
                continue
            trait = paths.norm_text(body)[:max_trait]
            if not trait:
                continue
            # 同义替换（≥ replace_ratio）→ 整块替换；否则追加
            hit_idx, best = None, 0.0
            for i, e in enumerate(user_entries):
                s = similarity.ratio(trait, e)
                if s > best:
                    hit_idx, best = i, s
            if hit_idx is not None and best >= replace_ratio:
                old = user_entries[hit_idx]
                user_entries[hit_idx] = trait
                promote.append({"cand": cf.name, "mode": "replace", "ratio": round(best, 3),
                                "old": old[:60], "trait": trait})
            elif hit_idx is not None and best >= 0.6:
                # 相似但非同义 → 不替换（防误伤行为规则），留候选
                blocked.append({"cand": cf.name, "why": "similar-not-same",
                                "ratio": round(best, 3)})
                continue
            else:
                user_entries.append(trait)
                promote.append({"cand": cf.name, "mode": "append", "ratio": round(best, 3),
                                "trait": trait})
            if not dry_run:
                _mark_candidate(cf, "promoted")
                ledger.distill_log(conn, "promote", cf.name, "mode=%s ratio=%.3f"
                                   % (promote[-1]["mode"], best))

        if not dry_run and promote:
            paths.backup_before_write(root.user_md, tag="蒸馏前",
                                      keep=int(cfg.get("BACKUP_KEEP", config.BACKUP_KEEP)))
            paths.write_atomic(root.user_md, store.serialize_l1(user_entries, nl))
        if not dry_run:
            if not made and not promote:
                ledger.distill_log(conn, "idle", "", "heartbeat 零动作（门在跑）")
            conn.commit()
        return {"ok": True, "candidates": made,
                "promoted": len(promote), "promote": promote,
                "rejected": rejected, "blocked": blocked[:20],
                "blocked_count": len(blocked),
                "user_chars": store.chars_of(user_entries),
                "user_quota": user_quota, "dry_run": dry_run}
    finally:
        conn.close()


def _mark_candidate(cf: Path, status: str) -> None:
    """给候选文件改名标记终态（`promoted` / `rejected`）。

    这是**候选标记的改名**，不是记忆删除：内容已在 `.promoted.md` / `.rejected.md` 里。
    `load_candidates` / 阶段 2 都按后缀跳过它们，所以不会再重复处理。
    """
    try:
        target = cf.with_name("%s.%s.md" % (cf.name, status))
    except (ValueError, OSError):
        return
    if target.exists():
        target = cf.with_name("%s.%s.%s.md" % (cf.name, status, paths.stamp_ms()))
    try:
        cf.rename(target)
    except OSError:
        pass


def _corroborate_candidate(root, it: dict, conn) -> int:
    """把条目**真实见过的会话集合**并入对应的 observing 候选，返回新的 corroborate。

    **不能用条目名的哈希冒充会话**：那会凭空多算一个来源，于是"单会话反复重申"
    也会被当成"交叉印证"，直接绕过观察期（实测：单会话候选被提前晋升）。
    正确来源是条目的 `sessions` 字段（由门① 按候选文件名里的真实 session 累加）。
    """
    fm = it["fm"]
    real = [x for x in str(fm.get("sessions") or "").split(",") if x]
    if not real:
        return 0
    key = paths.norm_key(it["body"])[:60]
    d = root.user_candidates
    if not d.is_dir():
        return 0
    for cf in d.glob("*.md"):
        cfm, cbody = store.parse_frontmatter(paths.read_text(cf))
        if (cfm.get("status") or "").strip().lower() not in ("observing", ""):
            continue
        if paths.norm_key(cbody)[:60] != key:
            continue
        seen = set(x for x in str(cfm.get("sessions_seen") or "").split(",") if x)
        seen |= set(real)
        cfm["sessions_seen"] = ",".join(sorted(seen))
        new_c = max(len(seen), _int(cfm.get("corroborate")))
        cfm["corroborate"] = str(new_c)
        paths.write_atomic(cf, store.dump_frontmatter(cfm, cbody))
        return new_c
    return 0


def _source_still_active(root, source: str) -> bool:
    if not source:
        return True
    for it in root.items():
        if it["name"] == source or it["fm"].get("id") == source:
            return it.get("status") == "active"
    return True   # 源不在了（历史迁移）→ 不因缺证据而否决


def _candidate_exists(root, body: str) -> bool:
    key = paths.norm_key(body)[:60]
    d = root.user_candidates
    if not d.is_dir():
        return False
    for f in d.glob("*.md"):
        fm, b = store.parse_frontmatter(paths.read_text(f))
        if paths.norm_key(b)[:60] == key:
            return True
    return False


def _trait_of(body: str, max_trait: int, llm=None) -> str:
    """LLM 只做措辞；失败/未接 → 原文截断（**绝不因为没网就不动**）。"""
    text = paths.norm_text(body)
    if llm is not None:
        try:
            out = llm(text)
            if out and out.strip():
                text = out.strip()
        except Exception:   # noqa: BLE001 - fail-open 是承诺
            pass
    return text[:max_trait]


# ── 门④ 复核降级 ─────────────────────────────────────────────────────

def review(root, dry_run: bool = False) -> dict:
    """遗忘曲线降级：active → dormant → archived（**永不物理删除**）。

    两个修复：
      1. `last_verified` 由「被使用」刷新（检索命中/加载即视为已验证）——旧实现
         "从不刷新"，导致最活跃的记忆反而被归档；
      2. 窗口内被命中的条目免降级（使用驱动休眠）。
    """
    cfg = config.load(root.path)
    days_sem = int(cfg.get("REVIEW_DAYS_SEMANTIC", 90))
    days_epi = int(cfg.get("REVIEW_DAYS_EPISODIC", 30))
    use_window = bool(cfg.get("REVIEW_USE_WINDOW", True))
    conn = root.connect()
    try:
        stats = _hit_stats(conn)
        demoted, archived, refreshed, kept = [], [], [], []
        for it in root.items():
            fm = dict(it["fm"])
            mtype = it.get("type") or "semantic"
            threshold = days_epi if mtype == "episodic" else days_sem
            status = (fm.get("status") or "active").strip().lower()
            if status == "archived":
                # 终态：**只能靠显式 `bring-back` 恢复**（那条路会留一笔账）。
                # 一次偶然的检索命中不足以把已归档记忆自动拉回目录——
                # 撤销系统的最终判断需要人的意图，不能是副作用。
                continue
            last_hit = stats.get(it["name"], (0, ""))[1] or fm.get("last_hit") or ""
            verified = fm.get("last_verified") or fm.get("last_seen") or ""
            age_v = paths.days_between(verified)
            age_h = paths.days_between(last_hit) if last_hit else None

            used_recently = bool(use_window and age_h is not None and age_h <= threshold)
            if used_recently:
                # 使用驱动：刷新 last_verified（这就是"最活跃的反被归档"的修法）
                #
                # **但只刷新 last_verified 是不够的**（2026-10-29 实测）：
                # 条目一旦降级成 dormant，目录（`catalog_payload`）只收 active，
                # 于是"被反复检索命中 → last_verified 天天刷新 → 却永远不在目录里"。
                # 而 `bring_back()` 当时没有任何 CLI 入口，用户没有恢复通道 →
                # 目录只会单向收缩。门④ 长期不跑（H1）时这个缺陷被掩埋，
                # H1 修好立刻变成"记忆用着用着就消失了、还救不回来"。
                # 所以"被使用"必须包含**复活**：dormant → active（退一步，可逆）。
                revived = status == "dormant"
                dirty = (fm.get("last_verified") or "") != paths.today() or revived
                if dirty:
                    fm["last_verified"] = paths.today()
                    if revived:
                        fm["status"] = "active"
                        fm["fate"] = "retained"
                        fm["revived_at"] = paths.today()
                        fm["revived_reason"] = "used-within-%dd-window" % threshold
                    if not dry_run:
                        root.write_item(it["name"], fm, it["body"])
                        if revived:
                            ledger.review_log(
                                conn, "revive", it["name"],
                                "被使用（%.0f 天内命中 %d 次）→ 回 active"
                                % (age_h or 0, stats.get(it["name"], (0, ""))[0]))
                    if not revived:
                        refreshed.append(it["name"])
                kept.append({"id": it["name"], "why": "used-within-window",
                             "age_hit_days": round(age_h or 0, 1),
                             "revived": revived})
                continue
            overdue = age_v is None or age_v > threshold
            if not overdue:
                kept.append({"id": it["name"], "why": "not-overdue",
                             "age_days": round(age_v, 1), "threshold": threshold})
                continue
            why = ("last_verified 缺失/不可解析" if age_v is None
                   else "超期 %.0f 天（阈值 %d）" % (age_v, threshold))
            if status == "dormant":
                fm["status"] = "archived"
                fm["fate"] = "archived"
                fm["archived_at"] = paths.today()
                archived.append({"id": it["name"], "why": why})
                if not dry_run:
                    root.write_item(it["name"], fm, it["body"])
                    ledger.review_log(conn, "archive", it["name"], why)
            else:
                fm["status"] = "dormant"
                fm["dormant_at"] = paths.today()
                fm["last_verified"] = paths.today()   # 给足观察期
                demoted.append({"id": it["name"], "why": why})
                if not dry_run:
                    root.write_item(it["name"], fm, it["body"])
                    ledger.review_log(conn, "demote", it["name"], why)
        if not dry_run:
            if not demoted and not archived:
                ledger.review_log(conn, "verify", "", "全库复核零降级（%d 条未超期）"
                                  % len(kept))
            conn.commit()
        return {"ok": True, "demoted": demoted, "archived": archived,
                "refreshed": refreshed[:50], "refreshed_count": len(refreshed),
                "kept_count": len(kept), "dry_run": dry_run}
    finally:
        conn.close()


# ── 一次跑全部门（runMaintenance 的落点）────────────────────────────

def maintain(root, dry_run: bool = False, llm=None) -> dict:
    """四门顺跑（每次调用都幂等；没有任何 cron 依赖）。

    顺序有讲究：先整合（门①产条目）→ 再巩固（门②挑得出来的）→ 再蒸馏（门③挑稳定的）
    → 最后复核（门④降级用不到的）。反过来会把刚进来的条目当场降级。
    """
    g1 = integrate(root, archive=True, dry_run=dry_run)
    g2 = quota(root, dry_run=dry_run)
    g3 = distill(root, dry_run=dry_run, llm=llm)
    g4 = review(root, dry_run=dry_run)
    return {"ok": True, "gate1": g1, "gate2": g2, "gate3": g3, "gate4": g4,
            "dry_run": dry_run}



