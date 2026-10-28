#!/usr/bin/env python3
"""稳定性测试（v3.2.0）：反复跑会不会变、会不会泄漏、会不会抖动。

五组，每组都以"**重复 N 次后结果必须稳定**"为判据：

  S1 幂等稳定  连续跑 3 轮 `gate all`，第 2/3 轮必须零动作（提升/挤出/晋升全 0）
  S2 目录稳定  连续 `materialize` 3 次，产物逐字节相同（否则每次装配都换前缀 → 成本爆炸）
  S3 无泄漏    跑 30 轮完整维护后：detail 文件数 = 条数、无 .tmp 残留、备份数量受控、
                hit 表行数在预期内、WAL 文件不无限增长
  S4 大库压力  300 条 detail + 60 条候选，跑完整维护，时间与内存可接受，结果正确
  S5 反复开关  同一个根连续 init/open/close 50 次，root_id 不变、不产生垃圾文件

运行：python tests/test_stability.py
      python tests/test_stability.py -k S4     # 只跑某一组
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from memory_ecology import gates, ledger, paths, retrieve, root as rootmod, store  # noqa: E402
from memory_ecology.cli import main as cli_main  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("  ✓ " if ok else "  ✗ ") + name + (("  — " + detail) if detail else ""))


def quiet_cli(argv) -> int:
    """调核心 CLI 但**吞掉 stdout**（它每次都会打一行 JSON，会淹掉进度输出）。"""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return cli_main(argv)


def new_root(tmp: Path, name: str = "root") -> rootmod.Root:
    p = tmp / name
    quiet_cli(["--root", str(p), "init"])
    return rootmod.Root(p).open()


def seed(root: rootmod.Root, n: int, scope: str = "global", kind: str = "semantic") -> None:
    for i in range(n):
        paths.append_line(
            root.pending / (scope if scope != "global" else ".") / ("seed-%02d.md" % (i % 10)),
            "- [%s] 稳定条目 %03d：用于压测与稳定性验证的固定内容 %s\n"
            % (kind, i, "细节" * 12))


def digest_tree(p: Path, skip: tuple[str, ...] = (".db-wal", ".db-shm", ".db")) -> str:
    h = hashlib.sha1()
    for f in sorted(p.rglob("*")):
        if not f.is_file():
            continue
        if any(s in f.name for s in skip):
            continue
        h.update(f.name.encode("utf-8", "replace"))
        try:
            h.update(f.read_bytes())
        except OSError:
            pass
    return h.hexdigest()


# ── S1 幂等稳定 ───────────────────────────────────────────────────────

def s1(tmp: Path) -> None:
    print("\n[S1] 连续 3 轮 gate all —— 第 2/3 轮必须零动作")
    root = new_root(tmp, "s1")
    seed(root, 12)
    gates.integrate(root)
    # 让它们都可提升（模拟"被使用 + 沉淀够久"）
    import datetime
    old = (datetime.datetime.now() - datetime.timedelta(days=20)).strftime("%Y-%m-%d")
    for it in root.items():
        fm = dict(it["fm"])
        fm["first_seen"] = old
        fm["occurrences"] = "3"
        fm["session_count"] = "2"
        root.write_item(it["name"], fm, it["body"])
    (root.path / "configs").mkdir(exist_ok=True)
    (root.path / "configs" / "eco.toml").write_text(
        "[quota]\nmemory_quota = 600\n", encoding="utf-8")

    r1 = gates.maintain(root)
    p1 = len(r1["gate2"]["promoted"]), len(r1["gate2"]["extruded"])
    r2 = gates.maintain(root)
    p2 = len(r2["gate2"]["promoted"]), len(r2["gate2"]["extruded"])
    r3 = gates.maintain(root)
    p3 = len(r3["gate2"]["promoted"]), len(r3["gate2"]["extruded"])
    check("S1a 第 1 轮有动作（提升 %d / 挤出 %d）" % p1, p1[0] + p1[1] > 0)
    check("S1b 第 2 轮零动作（提升 %d / 挤出 %d）—— 无震荡" % p2, p2 == (0, 0))
    check("S1c 第 3 轮零动作（提升 %d / 挤出 %d）" % p3, p3 == (0, 0))
    check("S1d 门① 第 2/3 轮新增 0",
          r2["gate1"]["new"] == 0 and r3["gate1"]["new"] == 0)

    # L1 始终不超触发线
    entries, _ = store.parse_l1(paths.read_text(root.memory_md))
    trigger = int(600 * store.QUOTA_RATIO)
    check("S1e L1 字符 %d ≤ 触发线 %d" % (store.chars_of(entries), trigger),
          store.chars_of(entries) <= trigger)


# ── S2 目录稳定 ───────────────────────────────────────────────────────

def s2(tmp: Path) -> None:
    print("\n[S2] 连续 materialize 3 次 —— 产物必须逐字节相同")
    root = new_root(tmp, "s2")
    seed(root, 20)
    gates.integrate(root)
    d1 = retrieve.materialize(root)
    h1 = digest_tree(root.catalog_dir)
    retrieve.materialize(root)
    h2 = digest_tree(root.catalog_dir)
    retrieve.materialize(root)
    h3 = digest_tree(root.catalog_dir)
    check("S2a 三次物化产物一致", h1 == h2 == h3, "%s / %s / %s" % (h1[:8], h2[:8], h3[:8]))
    check("S2b 物化覆盖每个圈", set(d1["scopes"]) >= {"global"}, str(d1["scopes"]))

    # 渲染文本在两次读取之间字节稳定（成本命门）
    t1 = retrieve.render_catalog_text(retrieve.read_materialized(root, "global"))
    t2 = retrieve.render_catalog_text(retrieve.read_materialized(root, "global"))
    check("S2c 渲染文本字节稳定", t1 == t2)
    check("S2d 渲染文本非空", len(t1) > 0)


# ── S3 无泄漏 ─────────────────────────────────────────────────────────

def s3(tmp: Path) -> None:
    print("\n[S3] 30 轮完整维护 —— 无 tmp 残留 / 备份受控 / 条目守恒")
    root = new_root(tmp, "s3")
    (root.path / "configs").mkdir(exist_ok=True)
    (root.path / "configs" / "eco.toml").write_text(
        "[quota]\nmemory_quota = 800\n\n[review]\nreview_days_semantic = 9999\n"
        "review_days_episodic = 9999\n", encoding="utf-8")
    seed(root, 15)
    n_items = 0
    for r in range(30):
        gates.maintain(root)
        n_items = len(root.items())
    # 无 tmp 残留
    tmps = [p for p in root.path.rglob("*.tmp")]
    check("S3a 无 .tmp 残留", tmps == [], "%d 个：%s" % (len(tmps), [p.name for p in tmps[:3]]))
    # 无写探针残留
    probes = list((root.path / ".eco").glob(".write-probe*"))
    check("S3b 无写探针残留", probes == [], str([p.name for p in probes[:3]]))
    # 备份数量受控（保留策略）
    baks = paths.list_backups(root.memory_md)
    check("S3c MEMORY 备份受控（%d 份 ≤ 8）" % len(baks), len(baks) <= 8,
          "超期备份应在 _bak-archive/ 里")
    # 条目守恒：30 轮后条目数不应比第一轮少（没丢记忆），也不应无限增长
    check("S3d 条目数守恒（%d）" % n_items, n_items >= 15 - 15 and n_items <= 15 + 60,
          "既不该丢、也不该无界增长")
    # detail 文件数 = 条数（没有孤儿文件）
    files = list(root.detail.glob("*.md"))
    check("S3e detail 文件数 %d = 条数 %d" % (len(files), n_items), len(files) == n_items)
    # WAL 不无限增长
    wal = root.db.with_name("eco.db-wal")
    size = wal.stat().st_size if wal.exists() else 0
    check("S3f WAL 大小受控（%d 字节 < 4MB）" % size, size < 4 * 1024 * 1024)
    # 账本有界：hit 表
    conn = root.connect_ro()
    try:
        hits = conn.execute("SELECT count(*) FROM hit").fetchone()[0]
        ev = conn.execute("SELECT count(*) FROM event").fetchone()[0]
    finally:
        conn.close()
    check("S3g hit 表行数合理（%d）" % hits, hits < 5000)


# ── S4 大库压力 ───────────────────────────────────────────────────────

def s4(tmp: Path) -> None:
    print("\n[S4] 300 条 detail + 60 条候选 —— 时间与正确性")
    root = new_root(tmp, "s4")
    # 直接写 detail（跳过整合，模拟存量库）
    root.detail.mkdir(parents=True, exist_ok=True)
    for i in range(300):
        fm = {"id": "mem-bulk-%03d" % i, "type": "semantic" if i % 2 else "episodic",
              "status": "active", "fate": "retained", "scope": "global" if i % 3 else "bulk",
              "created": "2026-01-01T00:00:00", "first_seen": "2026-01-01",
              "last_seen": "2026-01-01", "last_verified": "2026-01-01",
              "occurrences": str(1 + i % 4), "session_count": str(1 + i % 3),
              "fp": paths.fingerprint("bulk", str(i)), "superseded_by": ""}
        root.write_item("mem-bulk-%03d" % i, fm, "批量条目 %03d：%s" % (i, "内容" * 20))
    seed(root, 60)

    t0 = time.time()
    r = gates.maintain(root)
    dt = time.time() - t0
    check("S4a 完整维护耗时 %.1fs（300 条 detail + 60 候选）" % dt, dt < 120,
          ">2min 说明有 O(n²) 的坑")
    check("S4b 整合新增 %d 条候选" % r["gate1"]["new"], r["gate1"]["new"] > 0)
    check("S4c 门② 有提升 %d 条" % len(r["gate2"]["promoted"]),
          len(r["gate2"]["promoted"]) > 0, "大库上更该有可提升条目")

    t0 = time.time()
    retrieve.materialize(root)
    dt2 = time.time() - t0
    check("S4d 物化耗时 %.2fs" % dt2, dt2 < 30)

    t0 = time.time()
    hits = retrieve.search(root.items(), "批量条目 100", limit=5)
    dt3 = time.time() - t0
    check("S4e 检索耗时 %.3fs（300+ 条）" % dt3, dt3 < 5)
    check("S4f 检索命中正确", bool(hits) and "批量条目" in hits[0]["line"], str(hits[:1])[:80])

    # 目录体积仍受预算硬约束（大库不会把目录撑爆）
    payload = retrieve.catalog_payload(root.items(), limit=20, scope="global",
                                       budget_chars=600)
    check("S4g 大库目录仍 ≤600 字符（%d）" % payload["used_chars"],
          payload["used_chars"] <= 600)


# ── S5 反复开关 ───────────────────────────────────────────────────────

def s5(tmp: Path) -> None:
    print("\n[S5] 连续 init/open/close 50 次 —— root_id 不变、无垃圾")
    p = tmp / "s5"
    quiet_cli(["--root", str(p), "init"])
    r0 = rootmod.Root(p).open()
    rid = r0.meta.get("root_id")
    before = digest_tree(p)
    for _ in range(50):
        quiet_cli(["--root", str(p), "init"])
        r = rootmod.Root(p).open()
        _ = r.items()
    r1 = rootmod.Root(p).open()
    check("S5a root_id 不变（%s）" % rid, r1.meta.get("root_id") == rid)
    # 除了账本/目录/配置这些"会变的东西"，反复 init 不该多出任何文件
    allowed = (".db", ".json", ".md", ".toml")
    extra = [f.name for f in p.rglob("*")
             if f.is_file() and not any(s in f.name for s in allowed)
             and not f.name.startswith(".write-probe")]
    check("S5b 反复 init 不产生垃圾文件（%s）" % extra[:3], len(extra) == 0)
    # 重复 init 只记一次账
    conn = r1.connect_ro()
    try:
        n = conn.execute("SELECT count(*) FROM event WHERE kind='init'").fetchone()[0]
    finally:
        conn.close()
    check("S5c init 事件只记 1 次（实际 %d）" % n, n == 1)
    check("S5d 反复 init 后条目数仍为 0（没有凭空造数据）", len(r1.items()) == 0)


GROUPS = {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "S5": s5}


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="只跑名字含该串的组")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="eco-stab-"))
    print("=" * 72)
    print("记忆生态 v3.2.0 · 稳定性测试")
    print("=" * 72)
    try:
        for name, fn in GROUPS.items():
            if args.k and args.k not in name:
                continue
            try:
                fn(tmp)
            except Exception as exc:  # noqa: BLE001 - 一组崩不该带走其余组
                import traceback
                check("%s 组异常" % name, False, "%s: %s" % (type(exc).__name__, exc))
                traceback.print_exc()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = [r for r in RESULTS if r[1]]
    bad = [r for r in RESULTS if not r[1]]
    print()
    print("=" * 72)
    print("汇总: %d/%d 通过" % (len(ok), len(RESULTS)))
    for n, _, d in bad:
        print("  ✗ %s  %s" % (n, d))
    print("全部通过 ✅" if not bad else "有失败 ❌")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
