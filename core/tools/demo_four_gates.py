"""端到端：让四道门在**真实条件**下各产出一条有效账，证明治理链路真的会动。

这不是单测（单测在 tests/ 里），这是**验收演示**：
用真实 CLI 走完"投递 → 整合 → 使用 → 巩固 → 蒸馏 → 复核"的全链路，
然后打印四张账本的实际新增行。
"""
import io
import contextlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memory_ecology import ledger, paths, store  # noqa: E402
from memory_ecology.cli import main as cli  # noqa: E402
from memory_ecology.root import Root  # noqa: E402

ROOT = Path(tempfile.mkdtemp(prefix="eco-demo-")) / "root"


def run(*argv, show=False) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli(["--root", str(ROOT), *argv])
    d = json.loads(buf.getvalue().strip().splitlines()[-1])
    if show:
        print("   $ eco %s\n     → %s" % (" ".join(argv)[:90],
                                          json.dumps(d, ensure_ascii=False)[:160]))
    return d


def counts() -> dict:
    conn = Root(ROOT).open().connect_ro()
    try:
        return {t: conn.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
                for t in ("gate_log", "quota_log", "distill_log", "review_log",
                          "inject_event", "hit")}
    finally:
        conn.close()


def section(title: str) -> None:
    print("\n" + "─" * 66)
    print(title)
    print("─" * 66)


try:
    section("0 · 建根 + 声明圈")
    print("   ", run("init", show=True)["root_id"])
    run("circles-write", "--json", json.dumps({
        "global_budget_chars": 600,
        "circles": [{"id": "demo", "display_name": "演示圈",
                     "match": {"cwd_prefix": ["D:/demo"]}, "budget_chars": 600}],
    }), show=True)
    before = counts()

    section("1 · 门① 写入整合（3 个会话印证同一条事实 + 1 条圈内 + 1 条全局）")
    fact = "用户偏好中文简洁回复并反感 AI 腔"
    for i in range(3):
        run("deposit", "--session", "sess-%d" % i, "--text", fact, "--kind", "semantic")
    run("deposit", "--session", "s", "--text", "演示圈：部署用 blue-green", "--kind", "procedural", "--scope", "demo")
    run("deposit", "--session", "s", "--text", "全局约定：提交前跑全量测试", "--kind", "lesson")
    g1 = run("gate", "1", show=True)
    print("     → detail %d 条（同一条事实被 3 个会话印证，仍然只有 1 条）" % g1["new"])

    section("2 · 制造'被使用'证据（门②B 判据 / 门④ 免降级都靠它）")
    run("search", "简洁回复", show=True)
    run("search", "简洁回复")
    run("load", run("catalog", "--limit", "8")["items"][0]["id"], show=True)

    section("3 · 把条目'拨老'（模拟沉淀 20 天；真实系统里靠时间自然发生）")
    old = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    r0 = Root(ROOT).open()
    for it in r0.items():
        fm = dict(it["fm"])
        fm["first_seen"] = old
        if not fm.get("created"):
            fm["created"] = old
        r0.write_item(it["name"], fm, it["body"])
    print("     3 条 detail 的 first_seen → %s" % old)
    (ROOT / "configs").mkdir(exist_ok=True)
    (ROOT / "configs" / "eco.toml").write_text(
        "[quota]\nmemory_quota = 300\n", encoding="utf-8")
    print("     L1 配额调到 300（触发线 255）—— 让门② 既有提升也有挤出")

    section("4 · 门② 配额巩固（提升 L2→L1 = 旧项目从未发生过的动作）")
    g2 = run("gate", "2", show=True)
    print("     提升 %d 条 / 挤出 %d 条 → L1 %d→%d 字符（触发线 %d）"
          % (len(g2["promoted"]), len(g2["extruded"]), g2["chars_before"],
             g2["chars_after"], g2["trigger"]))
    for p in g2["promoted"]:
        print("       ↑ promote %s  依据 %s" % (p["id"], p["why"]))
    for e in g2["extruded"][:3]:
        print("       ↓ extrude %s  %s" % (e["slug"][:22], e["reason"][:40]))

    section("5 · 门③ 画像蒸馏（3 个会话印证 → 走交叉印证旁路，不等 30 天）")
    g3 = run("gate", "3", show=True)
    print("     候选 %d / 晋升 %d" % (g3["candidates"], g3["promoted"]))
    for p in g3["promote"]:
        print("       ↑ %s  trait=%s" % (p["mode"], p["trait"][:44]))
    user = (ROOT / "memories" / "USER.md")
    if user.is_file() and user.read_text(encoding="utf-8").strip():
        print("       USER.md 现在有：%s"
              % paths.norm_text(user.read_text(encoding="utf-8"))[:60])

    section("6 · 门④ 复核降级（把一条没有使用记录的老条目降到 dormant）")
    r1 = Root(ROOT).open()
    target = None
    for it in r1.items():
        if it.get("type") == "lesson" and "全局约定" in it["body"]:
            target = it
            break
    if target:
        fm = dict(target["fm"])
        fm["last_verified"] = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        fm["last_seen"] = fm["last_verified"]
        r1.write_item(target["name"], fm, target["body"])
        print("     把 %s 的 last_verified 拨到 200 天前（超 semantic 90 天阈值）" % target["name"])
    g4 = run("gate", "4", show=True)
    print("     降级 %d 条 / 归档 %d 条 / 刷新 last_verified %d 条"
          % (len(g4["demoted"]), len(g4["archived"]), g4["refreshed_count"]))
    for d in g4["demoted"]:
        print("       ↓ demote %s  %s" % (d["id"], d["why"][:50]))
    for d in g4["archived"]:
        print("       ↓ archive %s  %s" % (d["id"], d["why"][:50]))

    section("7 · 四张账本的实际新增（这是 v3.2.0 的验收判据）")
    after = counts()
    gate_names = {"gate_log": "门① 写入整合", "quota_log": "门② 配额巩固",
                  "distill_log": "门③ 画像蒸馏", "review_log": "门④ 复核降级"}
    ok = True
    for t, label in gate_names.items():
        d = after[t] - before.get(t, 0)
        print("   %-12s %-14s 新增 %+d 行%s" % (t, label, d, "" if d > 0 else "   ← 没动！"))
        if d <= 0:
            ok = False
    print("   %-12s %-14s 新增 %+d 行" % ("inject_event", "注入审计",
                                        after["inject_event"] - before["inject_event"]))
    print("   %-12s %-14s 新增 %+d 行" % ("hit", "检索命中",
                                        after["hit"] - before["hit"]))

    section("8 · 四门实况（观测舱 G2 的数据源）")
    h = run("health")
    for g in h["gates"]["gates"]:
        mark = "✗ 死" if g["dead"] else "✓ 活"
        print("   %-6s %-14s %s  有效 %2d / 期望动作 %2d  最后动作 %-8s  %s"
              % (g["gate"], g["name"], mark, g["effective"], g["expected_count"],
                 g["last_action"] or "—", ("← " + g["reason"][:34]) if g["reason"] else ""))

    section("结论")
    if ok:
        print("   ✅ 四道门在真实条件下各产出了有效动作 —— 治理链路真的会动。")
        print("      这正是旧项目做不到的：门② 0 promote、门③ 0 晋升、门④ 两个月 2 行。")
    else:
        print("   ❌ 有门没有产出有效动作，需要排查。")
    print("\n   数据根（可删）：%s" % ROOT)
    sys.exit(0 if ok else 1)
finally:
    shutil.rmtree(ROOT.parent, ignore_errors=True)
