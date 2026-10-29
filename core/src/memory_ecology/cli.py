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
import sys
from pathlib import Path

from . import (__version__, config, gates, ledger, packs, paths, retrieve, root as rootmod,
               store)


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
    scope = store.norm_scope(args.scope)
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
            "chars": len(text)}


def cmd_reconcile(root, args) -> dict:
    if root.readonly:
        return root.readonly_payload()
    return gates.integrate(root, archive=not args.no_archive, dry_run=args.dry_run)


def cmd_gate(root, args) -> dict:
    if root.readonly:
        return root.readonly_payload()
    which = args.gate
    if which == "1":
        return gates.integrate(root, archive=True, dry_run=args.dry_run)
    if which == "2":
        return gates.quota(root, dry_run=args.dry_run)
    if which == "3":
        return gates.distill(root, dry_run=args.dry_run)
    if which == "4":
        return gates.review(root, dry_run=args.dry_run)
    if which == "all":
        return gates.maintain(root, dry_run=args.dry_run)
    return {"ok": False, "error": "unknown-gate", "gate": which}


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
        stats=retrieve.hit_stats(root.connect_ro()))
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
    out = {"ok": True, "id": it["fm"].get("id") or it["name"],
           "kind": it.get("type"), "scope": it.get("scope"),
           "status": it.get("status"), "body": it["body"], "chars": len(it["body"])}
    if not root.readonly:
        conn = root.connect()
        try:
            conn.execute("INSERT INTO hit(ts,id,kind,query) VALUES (?,?,?,?)",
                         (paths.now(), out["id"], out["kind"], "load"))
            ledger.log(conn, "inject_event", {
                "ts": paths.now(), "session": args.session or "", "source": "eco-load",
                "form": "load", "items": 1, "chars": len(it["body"]),
                "tokens": len(it["body"]) // 2, "ids": out["id"]})
            conn.commit()
        finally:
            conn.close()
    return out


def cmd_search(root, args) -> dict:
    conn = root.connect_ro()
    stats = retrieve.hit_stats(conn) if conn is not None else {}
    if conn is not None:
        conn.close()
    hits = retrieve.search(root.items(), args.query, limit=args.limit,
                           scope=args.scope, hit_stats=stats)
    if hits and not root.readonly:
        conn = root.connect()
        try:
            for h in hits:
                conn.execute("INSERT INTO hit(ts,id,kind,query) VALUES (?,?,?,?)",
                             (paths.now(), h["id"], h["kind"], args.query[:120]))
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
            "global_budget_chars": cfg.get("global_budget_chars")}


def cmd_route(root, args) -> dict:
    scope = store.route_circle(root.path, args.cwd) if args.cwd else "global"
    return {"ok": True, "cwd": args.cwd or "", "scope": scope}


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
        except ValueError as exc:
            return {"ok": False, "error": "bad-json", "detail": str(exc)[:200], "exit": 5}
    else:
        if not (args.json or "").strip():
            return {"ok": False, "error": "no-input",
                    "detail": "必须给 --file 或 --json（不写就是不写，别猜）", "exit": 5}
        try:
            data = json.loads(args.json)
        except ValueError as exc:
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
        moved = 0
        for target in (root.memory_md, root.user_md):
            moved += len(paths.prune_backups(target, config.BACKUP_KEEP))
        ledger.event(conn, "prune", "hit", "deleted=%d moved_backups=%d" % (n, moved))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "hit_rows_deleted": n, "backups_archived": moved}


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

    s = sub.add_parser("search", help="词面检索")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=config.SEARCH_LIMIT)
    s.add_argument("--scope", default=None)
    s.add_argument("--session", default="")

    sub.add_parser("circles", help="列圈")
    rt = sub.add_parser("route", help="cwd → 圈")
    rt.add_argument("--cwd", default="")

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
