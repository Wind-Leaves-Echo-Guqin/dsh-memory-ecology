#!/usr/bin/env python3
"""test_mutation.py —— 变异测试：证明 v3.2.0 的测试集真的能抓 bug（而不是同义反复）。

为什么必须做
------------
一套 142 条全绿的测试，可能一条 bug 都抓不到：断言写宽了、夹具绕着缺陷走、
或者"测的是实现细节而不是行为"。变异测试是验证"测试有效性"的唯一手段——
故意把被测代码改坏成**真实缺陷模式**，看测试是否报警。

做法（与兼容版 `memory-ecology-兼容版/tests/test_mutation.py` 同类，但针对新核心）
------------------------------------------------------------------------------
1. 把 `src/` + `tests/` + `plugin/` + `gui/` 整棵树复制到 `%TEMP%` 下的独立沙盒
   （排除 `__pycache__` / `*.pyc` / `.git`），并种一个诱饵 `.git`——本机红线：
   **隔离实验绝不能在真身 `.git` 子树下跑**（findProjectRoot 会沿家目录上溯）。
2. 每个变异体：在副本上做**单点文本替换** → 断言"原文出现且只出现一次"
   （0 次 = 变异体失效，报 INVALID，**不能算存活**）→ 语法自检 → 跑**最小相关用例**。
3. 判定 CAUGHT / ALIVE / INVALID。ALIVE 说明测试无效，INVALID 说明变异体构造不当，
   两者都必须如实报告并给补强建议（**绝不为了让数字好看去改被测测试**）。
4. 跑变异体前先跑**基线**：未变异的副本上目标用例必须绿，否则"抓到"毫无意义。

用法
----
    python tests/test_mutation.py              # 全部（约 3~5 分钟）
    python tests/test_mutation.py -k M7        # 只跑名字含该串的
    python tests/test_mutation.py -k gate2     # 按组过滤
    python tests/test_mutation.py --no-baseline --timeout 120

退出码：全部 CAUGHT → 0；有 ALIVE 或 INVALID → 1（不确定即失败，不粉饰）。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PY = sys.executable
IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0
SKIP_DIRS = ("__pycache__", ".git", ".pytest_cache", "_bak-archive", "node_modules")

# ── 被测目标 ─────────────────────────────────────────────────────────

CORE = "tests/test_core.py"
# 真实观测舱不在这里：`integrations/gui` + `gui-v2.3` 在 memory-ecology-兼容版 仓库，
# 那边的 `tests/test_gui_v3_2_0.py` 自己守。本仓库的 `tests/test_gui.py` 已归档到
# `_archive/gui-wrong-target-v0.4/`（一度把服务端复制过来的错误目标）。
COMPAT = "tests/test_compat.py"
REG = "tests/test_regressions.py"
PLUGIN = "plugin/test_plugin.mjs"


def P(*nodes: str) -> dict:
    """pytest 跑法：一组 node id（最小集合，绝不上整套——别的用例通过会掩盖目标失败）。"""
    return {"kind": "pytest", "nodes": tuple(nodes)}


def N(*expect: str) -> dict:
    """插件跑法：整脚本跑（.mjs 是一体的），但只认**指定用例名**的失败。"""
    return {"kind": "plugin", "expect": tuple(expect)}


# 每个变异体：name / 组 / 文件 / 原文 / 替换 / 期望抓到它的用例 / 存活时的补强建议
MUTANTS: list[dict] = [
    # ── paths：并发三修 ───────────────────────────────────────────────
    dict(
        name="M1  write_atomic 临时名去 pid", group="paths",
        rel="src/memory_ecology/paths.py",
        old='    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))\n'
            '    try:\n'
            '        with open(tmp, "w", encoding="utf-8", newline="\\n") as f:\n',
        new='    tmp = path.with_name("%s.tmp" % (path.name))  # MUTANT: 固定临时名\n'
            '    try:\n'
            '        with open(tmp, "w", encoding="utf-8", newline="\\n") as f:\n',
        why="并发写同一目标会互相踩临时文件（设计文档 §9.6 bug 2）",
        runners=[P(CORE + "::TestPaths::test_atomic_write_tmp_is_pid_unique")],
        hint="现无任何『多进程写同一个目标文件』的用例：deposit 只走 append_line，"
             "并发投递根本不碰 write_atomic，所以这条断言对它完全免疫"
             "（实测 4 轮全绿，非偶发）。补强：N 进程同时 write_item 同一个 slug，"
             "断言最终内容等于某一次完整写入（无半截）、无残留 *.tmp；"
             "或直接单测：monkeypatch os.getpid 为两个值，捕获两次临时名并断言不同。",
    ),
    dict(
        name="M2  append_line 改文本模式", group="paths",
        rel="src/memory_ecology/paths.py",
        # 变异点：整个 _do_write 的二进制写入 → 换成分次写的文本模式。
        # （原变异点已被 2026-10-28 的并发丢行修复重写，见下方 why。）
        old='        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND |\n'
            '                     getattr(os, "O_BINARY", 0), 0o644)\n'
            '        try:\n'
            '            # 契约是"可能写得比要求少"，短写必须继续写，否则尾部静默丢失。\n'
            '            written = 0\n'
            '            while written < len(data):\n'
            '                n = os.write(fd, data[written:])\n',
        new='        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)  # MUTANT: 去 O_BINARY\n'
            '        try:\n'
            '            written = 0\n'
            '            while written < len(data):\n'
            '                n = os.write(fd, data[written:])\n',
        why="去掉 O_BINARY → Windows 把 \\n 翻成 \\r\\n，文件字节里出现 CR",
        runners=[P(CORE + "::TestPaths::test_append_line_writes_binary_no_crlf",
                   CORE + "::TestPaths::test_append_line_concurrent_same_file_no_interleave")],
        hint="（若存活）说明没人检查字节层面。应断言候选文件读出的字节**不含 b'\\r\\n'**。",
    ),
    dict(
        name="M2b append_line 去掉短写循环", group="paths",
        rel="src/memory_ecology/paths.py",
        old='            written = 0\n'
            '            while written < len(data):\n'
            '                n = os.write(fd, data[written:])\n'
            '                if n <= 0:\n'
            '                    raise OSError("append_line: write returned %r for %s"\n'
            '                                  % (n, path))\n'
            '                written += n\n',
        new='            os.write(fd, data)  # MUTANT: 不检查短写\n',
        why="短写时不续写 → 行尾静默被截断（记忆内容缺一截，且日志无异常）",
        # 必须有专门的短写用例：并发/CRLF 那两条都撞不到短写路径。
        runners=[P(CORE + "::TestPaths::test_append_line_handles_short_write")],
        hint="（若存活）说明没模拟过短写；应 monkeypatch os.write 第一次只写一半。",
    ),
    dict(
        name="M2c append_line 去掉并发锁", group="paths",
        rel="src/memory_ecology/paths.py",
        old='    with _append_lock(path):\n        _do_write()\n',
        new='    _do_write()  # MUTANT: 不加锁（Windows 并发丢行）\n',
        why="**实测真 bug**：Windows 的 O_APPEND 不是原子 seek+write，"
            "6 进程同时写 9KB 会算出同一偏移、后写覆盖先写 → 静默丢整行（8% 概率）",
        runners=[P(CORE + "::TestPaths::test_append_line_concurrent_same_file_no_interleave")],
        hint="（若存活）说明并发用例的行太短或进程太少；要 >8KB × ≥6 进程 × ≥5 行。",
    ),
    dict(
        name="M3  connect 去掉 WAL", group="paths",
        rel="src/memory_ecology/paths.py",
        old='        conn.execute("PRAGMA journal_mode=WAL")\n',
        new='        pass  # MUTANT: 不设 WAL\n',
        why="多进程并发整合会 database is locked",
        runners=[P(CORE + "::TestSafety::test_sqlite_is_wal")],
        hint="并发整合有 15s busy_timeout 兜底，1.4s 的小负载撞不出锁；"
             "现只有一条直接读 PRAGMA 的断言在守。补强：并发整合用例把 "
             "paths.connect(timeout=0.1) 收紧，断言仍全部成功。",
    ),
    dict(
        name="M4  probe 探针名去 pid", group="paths",
        rel="src/memory_ecology/paths.py",
        old='    probe = Path(path) / ".eco" / (".write-probe.%d" % os.getpid())\n',
        new='    probe = Path(path) / ".eco" / ".write-probe"  # MUTANT: 固定探针名\n',
        why="并发下互相删探针 → 后到进程被判『根不可写』→ 静默只读 → 丢写",
        # 补强过：原先只验『探针被清理』，固定名写→删同样清理干净 = 同义反复，
        # 于是它**单独跑被抓、跟在别的组后面跑就存活**（变异测试会波动）。
        # 现在两条都是行为断言：两个真进程的探针名必须不同；
        # 8 进程同时探必须全部 True。
        runners=[P(CORE + "::TestPaths::test_probe_is_pid_unique",
                   CORE + "::TestPaths::test_probe_writable_under_concurrency",
                   CORE + "::TestSafety::test_concurrent_deposit_no_loss")],
        hint="（已补强，应当稳定被抓）若存活，说明并发窗口没撞上——"
             "把 test_probe_writable_under_concurrency 的进程数调到 8 以上再试。",
    ),
    # ── store / gates ────────────────────────────────────────────────
    dict(
        name="M5  chars_of 不过滤 None", group="store",
        rel="src/memory_ecology/store.py",
        old="    real = [e for e in entries if e]\n",
        new="    real = list(entries)  # MUTANT: 不过滤 None\n",
        why="门② 挤出时用 cur[idx]=None 标记已移出 → NoneType.replace 崩门",
        runners=[P(CORE + "::TestGate2::test_extruded_entry_lands_in_detail")],
        hint="（若存活）补强：直接单测 chars_of([\"a\", None, \"b\"]) == 期望值。",
    ),
    dict(
        name="M6  integrate 指纹去 scope", group="gates",
        rel="src/memory_ecology/gates.py",
        old='            fp, body = store.candidate_fp(scope, text, body_max)\n',
        new='            fp, body = store.candidate_fp("global", text, body_max)  # MUTANT: 指纹不含 scope\n',
        why="跨圈同名条目被塌成一条（proj-a 的话进了 proj-b 的库）",
        runners=[P(CORE + "::TestGate1::test_cross_circle_never_merges",
                   REG + "::TestFingerprintConsistency::test_cross_circle_never_dedups")],
        hint="（若存活）补强：断言两圈的 detail 里 scope 分别为 proj-a/proj-b。",
    ),
    dict(
        name="M7  quota 删掉 B:used 判据", group="gates",
        rel="src/memory_ecology/gates.py",
        old='            if age >= stable_days and hits >= min_hits:\n'
            '                why.append("B:used")\n',
        new='            # MUTANT: 删掉 B:used（有使用证据也不提升）\n',
        why="『有使用证据』这条新判据消失 → 慢路径又空转（旧项目 0 promote）",
        runners=[P(CORE + "::TestGate2::test_promote_happens_when_used")],
        hint="（若存活）补强：断言 promoted[0]['why'] 含 'B:used'（该用例已有）。",
    ),
    dict(
        name="M8  review 删掉窗口内免降级", group="gates",
        rel="src/memory_ecology/gates.py",
        old='            used_recently = bool(use_window and age_h is not None and age_h <= threshold)\n',
        new='            used_recently = False  # MUTANT: 删掉『窗口内被命中免降级』\n',
        why="最活跃的记忆反被归档（v3.2.0 头号行为修复）",
        runners=[P(CORE + "::TestGate4::test_used_entry_is_not_archived")],
        hint="（若存活）补强：断言 archived == []。",
    ),
    dict(
        name="M9  review 删掉 last_verified 刷新", group="gates",
        rel="src/memory_ecology/gates.py",
        old='                if (fm.get("last_verified") or "") != paths.today():\n'
            '                    fm["last_verified"] = paths.today()\n'
            '                    if not dry_run:\n'
            '                        root.write_item(it["name"], fm, it["body"])\n'
            '                    refreshed.append(it["name"])\n',
        new='                pass  # MUTANT: 不刷新 last_verified、不记 refreshed\n',
        why="被使用不等于已验证 → refreshed 为空，下次仍会被降级",
        runners=[P(CORE + "::TestGate4::test_used_entry_is_not_archived")],
        hint="（若存活）补强：断言 last_verified == today 且 refreshed 含该 id。",
    ),
    dict(
        name="M10 distill 删掉交叉印证旁路", group="gates",
        rel="src/memory_ecology/gates.py",
        old='            if age < observe and corr < corroborate:\n',
        new='            if age < observe:  # MUTANT: corr<c corroborate 恒真（旁路失效）\n',
        why="观察期变成唯一闸门 → 89 候选 0 晋升的老病复发",
        runners=[P(CORE + "::TestGate3::test_corroboration_bypass_promotes_early")],
        hint="（若存活）补强：断言 corroborate=2 时 promoted==1。",
    ),
    dict(
        name="M11 distill 观察期永不阻塞", group="gates",
        rel="src/memory_ecology/gates.py",
        old='            if age < observe and corr < corroborate:\n',
        new='            if age < 0 and corr < corroborate:  # MUTANT: 永不阻塞\n',
        why="刚生成的候选立刻晋升（观察期形同虚设）",
        runners=[P(CORE + "::TestGate3::test_observation_period_blocks_promotion_and_says_why")],
        hint="（若存活）补强：断言 promoted==0 且 blocked 里有 why=observing。",
    ),
    dict(
        name="M12 quota 挤出时不写 detail", group="gates",
        rel="src/memory_ecology/gates.py",
        old='        if not already and not dry_run:\n'
            '            # 圈归属：能查到就沿用，查不到才 glob-al（不猜）\n'
            '            _write_extruded_detail(root, entry, detail_pref.get(key, "global") or "global")\n'
            '            detail_pref[key] = "global"\n',
        new='        if False:  # MUTANT: 挤出直接丢，不落 detail\n'
            '            pass\n',
        why="挤出＝静默删除（红线 1：绝不物理删除记忆）",
        runners=[P(CORE + "::TestGate2::test_extruded_entry_lands_in_detail")],
        hint="（若存活）补强：逐条断言被挤出条目能在 detail 找回同源副本（用例已有）。",
    ),
    # ── packs ───────────────────────────────────────────────────────
    dict(
        name="M13 validate_pack 跳过哈希校验", group="packs",
        rel="src/memory_ecology/packs.py",
        old='        elif want != got:\n',
        new='        elif False:  # MUTANT: 跳过哈希校验\n',
        why="被篡改的第三方 pack 仍被挂载（prompt injection 通道）",
        runners=[P(CORE + "::TestPacks::test_hash_mismatch_rejected",
                   CORE + "::TestPacks::test_tampered_pack_stops_being_mounted")],
        hint="（若存活）补强：断言 errors 里出现『哈希不符』。",
    ),
    dict(
        name="M14 validate_pack 跳过白名单", group="packs",
        rel="src/memory_ecology/packs.py",
        old='    if manifest.get("whitelisted") is not True:\n',
        new='    if False:  # MUTANT: 跳过来源白名单\n',
        why="默认一个都不加载的硬闸门失效",
        runners=[P(CORE + "::TestPacks::test_non_whitelisted_rejected")],
        hint="（若存活）补强：断言 errors 里出现 whitelisted。",
    ),
    dict(
        name="M15 uninstall 改成真删目录", group="packs",
        rel="src/memory_ecology/packs.py",
        old='    reg["packs"] = [p for p in reg["packs"] if p.get("id") != pid]\n',
        new='    _victim = next((p for p in reg["packs"] if p.get("id") == pid), None)\n'
            '    if _victim and _victim.get("path"):\n'
            '        import shutil as _sh\n'
            '        _sh.rmtree(_victim["path"], ignore_errors=True)  # MUTANT: 真删\n'
            '    reg["packs"] = [p for p in reg["packs"] if p.get("id") != pid]\n',
        why="卸载＝物理删除（红线 1；『人要删自己去删』）",
        runners=[P(CORE + "::TestPacks::test_uninstall_keeps_files")],
        hint="（若存活）补强：断言 manifest.json 仍在 且 payload['files_kept'] is True。",
    ),
    # ── retrieve / store / root / ledger / config ───────────────────
    dict(
        name="M16 read_materialized 去 schema 校验", group="retrieve",
        rel="src/memory_ecology/retrieve.py",
        old='    if data.get("schema") != CATALOG_SCHEMA:\n        return None\n',
        new='    if False:  # MUTANT: 不校验 schema\n        return None\n',
        why="旧格式物化文件被注入系统提示词（宁可少注入，不可注入旧格式）",
        runners=[P(CORE + "::TestCatalog::test_materialized_corruption_fails_open",
                   CORE + "::TestCatalog::test_materialized_wrong_schema_is_rejected",
                   CORE + "::TestCatalog::test_materialized_not_ok_flag_rejected"),
                 N("T14")],
        hint="test_core 那条只喂了**坏 JSON**（json.loads 当场失败，压根到不了 schema 判断）；"
             "插件 T14 虽然喂了 schema:999，但它比的是**插件侧** "
             "`readCatalogSync` 里的 `payload.schema !== CATALOG_SCHEMA`，与核心 "
             "`retrieve.read_materialized` 是两份代码——所以变异核心这条，T14 抓不到。"
             "补强：test_core 物化后把 global.json 的 schema 改成 999（仍是合法 JSON），"
             "断言 `read_materialized(r,'global') is None`；插件侧则改为经核心读、"
             "或两处判断收敛成一处（避免双口径）。",
    ),
    dict(
        name="M17 mark_consumed 去名字冲突处理", group="store",
        rel="src/memory_ecology/store.py",
        old='        target = f.with_name(f.name + ".done.md")\n'
            '        if target.exists():\n'
            '            target = f.with_name("%s.%s.done.md" % (f.name, paths.stamp_ms()))\n',
        new='        target = f.with_name(f.name + ".done.md")\n'
            '        # MUTANT: 去掉同名冲突处理（直接 rename）\n',
        why="同日同 session 再投 → 直接 rename：Windows 抛 FileExistsError（候选赖着）、"
            "POSIX 静默覆盖旧档（已消费记录消失）",
        runners=[P(CORE + "::TestGate1::test_archive_only_consumes_done_files")],
        hint="（若存活）补强：断言第二轮后 pending 下无活动文件且 .done.md 仍为 1 份。",
    ),
    dict(
        name="M18 parse_frontmatter 去 BOM 剥离", group="store",
        rel="src/memory_ecology/store.py",
        old='    if text.startswith("\\ufeff"):\n'
            '        text = text[1:]\n'
            '    fm: dict = {}\n',
        new='    fm: dict = {}  # MUTANT: 不剥 BOM\n',
        why="带 BOM 的首条解析失败（首条静默丢失）",
        runners=[P(COMPAT + "::TestOldFormat::test_parse_frontmatter_handles_bom_directly",
                   COMPAT + "::TestOldFormat::test_bom_first_entry_not_lost",
                   COMPAT + "::TestOldFormat::test_bom_in_detail_frontmatter")],
        hint="变异被上游防御**完全兜住**（等价变异体）：`paths.read_text` 用 "
             "`utf-8-sig` 打开，BOM 在进 store 之前就没了，所以删掉 store 的剥离分支"
             "行为不变——这条 ALIVE **不代表测试无效**，而是变异点与真实防线错位。"
             "要钉住 store 的这层防御本身，得直接喂字符串："
             "`parse_frontmatter(\"\\ufeff---\\ntype: semantic\\n---\\n正文\")`，"
             "断言 fm 解析成功；或者干脆删掉 store 里的重复防御，把 BOM 收敛成唯一入口。",
    ),
    dict(
        name="M19 Root.open schema 不符不置只读", group="root",
        rel="src/memory_ecology/root.py",
        old='        if schema != SCHEMA_VERSION:\n',
        new='        if False:  # MUTANT: schema 不匹配也照写\n',
        why="跨版本写坏数据根（迁移必须显式 migrate，不许隐式升级）",
        runners=[P(CORE + "::TestRoot::test_schema_mismatch_is_readonly")],
        hint="（若存活）补强：断言退出码 3 且 reason 含 schema-mismatch。",
    ),
    dict(
        name="M20 gate_status 把 idle 算进 effective", group="ledger",
        rel="src/memory_ecology/ledger.py",
        old='              "effective": ("promote", "extrude", "extrude-manual", "rollback"),\n',
        new='              "effective": ("promote", "extrude", "extrude-manual", "rollback",\n'
            '                            "idle"),  # MUTANT: idle 也算有效动作\n',
        why="246 行 idle 把 52 行 extrude 淹了 → 门② 明明 0 promote 却显示『活着』",
        # GUI 那两条已随 `tests/test_gui.py` 归档（真实观测舱迁到
        # memory-ecology-兼容版，那边有自己的 test_gui_v3_2_0.py）。
        # 核心这三条足以判定：effective 白名单一变，idle/selfcheck 立刻被算进去。
        runners=[P(CORE + "::TestGate2::test_idle_rows_never_count_as_effective",
                   CORE + "::TestGate2::test_selfcheck_rows_never_count_as_effective",
                   CORE + "::TestGate2::test_starved_when_effective_but_expected_never_happened")],
        hint="GUI G2 的两条断言只校验 kind ∈ {ok,dead,idle-spin,stale} 与"
             "『GUI 与核心一致』（两边跑的是同一份代码，必然一致），"
             "**没有任何一条断言 idle 不计入 effective**。补强：核心侧加一条——"
             "只插 3 行 action='idle' 的 quota_log，断言 "
             "`gate_status()['gates'][1]['effective'] == 0` 且 kind=='idle-spin'；"
             "GUI 侧加：门② effective==0 时 `dead` 必须为 True。",
    ),
    dict(
        name="M21 超期备份改成 unlink", group="paths",
        rel="src/memory_ecology/paths.py",
        old='            os.replace(b, arch / b.name)\n',
        new='            b.unlink()  # MUTANT: 超期备份直接删（红线 1）\n',
        why="备份被物理删除（红线 1：备份可逆、只归档不删）",
        runners=[P(CORE + "::TestPaths::test_backup_is_reversible_never_deletes")],
        hint="（若存活）补强：断言 _bak-archive 下文件数 > 0。",
    ),
    dict(
        name="M22 config 加一个没人读的常量", group="config",
        rel="src/memory_ecology/config.py",
        old="PACK_MAX_CHARS = 20000          # → packs.install()\n",
        new="PACK_MAX_CHARS = 20000          # → packs.install()\n"
            "DEAD_KEY_NOBODY_READS = 42      # MUTANT: 死配置（无人读）\n",
        why="死配置：改了没反应、也没人知道（旧项目 [quota] memory_limit 的老病）",
        runners=[P(CORE + "::TestSafety::test_config_keys_have_consumers")],
        hint="（若存活）补强：AST 扫描每个常量是否在别的模块被 attribute 读取，"
             "而不是简单的字符串包含（当前实现会把注释里出现的常量名算作消费者）。",
    ),
    # ── plugin ──────────────────────────────────────────────────────
    dict(
        name="M23 retrieve 截断省略号 … → ...", group="retrieve",
        rel="src/memory_ecology/retrieve.py",
        old='            one = one[:CATALOG_LINE_MAX - 1] + "…"\n',
        new='            one = one[:CATALOG_LINE_MAX - 1] + "..."  # MUTANT: ASCII 三点\n',
        why="渲染口径漂移（Python 用 …，JS 用 ...）→ 目录文本变长 + 前缀缓存失效",
        runners=[N("T11"), P(CORE + "::TestCatalog::test_render_uses_ellipsis_for_long_lines")],
        hint="（若存活）T11 的夹具条目太短，从不触发截断；补一条 ≥120 字符的条目。",
    ),
    dict(
        name="M24 事件名回退 agent/session-start", group="plugin",
        rel="plugin/index.js",
        old="  ctx.on('agent/created', (payload) => {\n",
        new="  ctx.on('agent/session-start', (payload) => {  // MUTANT: 死代码事件\n",
        why="监听 dsh 0.2.1-rc.x 上不存在的事件 → 静默死代码，会话边界补账从不生效",
        runners=[N("T3", "T3x")],
        hint="（若存活）补强：断言 handlers['agent/created'] 存在。",
    ),
    # ── 附加（不在必测清单里，用来定位『变异体错位』）──────────────────
    dict(
        name="M18b paths.read_text BOM 兜底去 sig", group="附加",
        rel="src/memory_ecology/paths.py",
        old='        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:\n',
        new='        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:  # MUTANT\n',
        why="（附加）BOM 处理的**唯一有效点**：去掉 sig 后首条应静默丢失",
        runners=[P(COMPAT + "::TestOldFormat::test_bom_first_entry_not_lost")],
        hint="**这是测试真的无效，不是偶发**（连跑 3 轮稳定 ALIVE）："
             "`test_bom_first_entry_not_lost` 只断言 `len(entries)==2` 且 "
             "`assertIn('首条带 BOM', entries[0])`，而 BOM 未被剥离时条目变成"
             "`'\\ufeff首条带 BOM'`——`assertIn` 对它**照样为真**，子串断言把缺陷吃掉了。"
             "补强（一行）：`self.assertTrue(entries[0].startswith('首条'))` "
             "或 `assertNotIn('\\ufeff', entries[0])`，变异立刻被抓。",
        extra=True,
    ),
    dict(
        name="M23b retrieve 省略号 … → ...", group="附加",
        rel="src/memory_ecology/retrieve.py",
        old='            one = one[:CATALOG_LINE_MAX - 1] + "…"\n',
        new='            one = one[:CATALOG_LINE_MAX - 1] + "..."  # MUTANT\n',
        why="（附加）截断省略号的口径；JS 侧原样转发，故『逐字节一致』可能不敏感",
        runners=[N("T11")],
        hint="**测试真的无效**（不是偶发）：JS 侧 `renderCatalog` 只是原样转发 "
             "`it.line`，真正截断（并插入 `…`）发生在 Python 的 `catalog_payload`。"
             "但 T11 的夹具条目都很短（<90 字符），**从不触发截断**，所以"
             "『逐字节一致』根本没走到省略号那条路径，变异自然抓不到。"
             "补强（只补夹具，不动核心）：T11 之前先 deposit 一条 ≥120 字符的长条目并"
             "跑 gate1，断言 Python 渲染出的行**以 `…`（U+2026）结尾**，再逐字节比对 JS。",
        extra=True,
    ),
]

# ── 沙盒 ─────────────────────────────────────────────────────────────

def _ignore(_dir: str, names: list[str]) -> set[str]:
    drop = set()
    for n in names:
        if n in SKIP_DIRS or n.endswith(".pyc") or n.endswith(".pyo"):
            drop.add(n)
        if n.startswith("MEMORY.md.bak-") or n.startswith("USER.md.bak-"):
            drop.add(n)
    return drop


def make_sandbox() -> Path:
    """复制一份可改的工作区到 %TEMP%，绝不碰真身（红线：隔离实验别在真身 .git 子树下）。"""
    base = Path(tempfile.mkdtemp(prefix="eco-mut-"))
    dst = base / "repo"
    shutil.copytree(REPO, dst, ignore=_ignore)
    (dst / ".git").mkdir(exist_ok=True)      # 诱饵 .git：阻断 findProjectRoot 上溯到家目录
    (dst / ".git" / "DECOY").write_text("isolation decoy, not a real repo\n",
                                        encoding="utf-8")
    return base


def child_env(repo: Path) -> dict:
    """子进程环境：UTF-8 + 摘掉宿主根 + 只指向副本的 src。"""
    e = dict(os.environ)
    for k in ("MEMORY_ECOLOGY_ROOT", "ECO_CORE", "ECO_HOME", "MEMORY_ECOLOGY_CONFIG"):
        e.pop(k, None)
    e["PYTHONPATH"] = str(repo / "src")
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONDONTWRITEBYTECODE"] = "1"       # 不留 .pyc（否则改回来的源码可能碰到旧字节码）
    e["PYTHONWARNINGS"] = "ignore"
    e["ECO_PYTHON"] = PY                     # 插件用例用它调核心，钉死同一个解释器
    e["ECO_GATE_STALE_DAYS"] = e.get("ECO_GATE_STALE_DAYS", "3")
    return e


def run(cmd: list[str], cwd: Path, env: dict, timeout: float) -> tuple[int | None, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=max(1.0, timeout),
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    except OSError as exc:
        return None, "SPAWN-FAILED: %s" % exc


def _cmd(runner: dict, repo: Path) -> tuple[list[str], Path]:
    if runner["kind"] == "pytest":
        return ([PY, "-m", "pytest", "-q", "--tb=line", "-p", "no:cacheprovider",
                 *runner["nodes"]], repo)
    return (["node", str(repo / "plugin" / "test_plugin.mjs")], repo / "plugin")


def _plugin_results(out: str) -> dict[str, str]:
    """插件输出 → {用例名: 'ok'|'FAIL'}。"""
    got = {}
    for line in out.splitlines():
        m = re.match(r"^(ok|FAIL)\s+(\S+)", line)
        if m:
            got[m.group(2)] = m.group(1)
    return got


def _first_failure(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("FAILED ") or line.startswith("E   "):
            return line.strip()[:110]
    for line in reversed(out.splitlines()):
        if line.strip():
            return line.strip()[:110]
    return "（无输出）"


# ── 基线：未变异的副本上，目标用例必须绿 ─────────────────────────────

class Baseline:
    def __init__(self, repo: Path, env: dict, timeout: float):
        self.repo, self.env, self.timeout = repo, env, timeout
        self._pytest: dict[tuple, tuple] = {}
        self._plugin: tuple | None = None

    def check(self, runner: dict) -> tuple[bool, str]:
        if runner["kind"] == "pytest":
            key = runner["nodes"]
            if key not in self._pytest:
                cmd, cwd = _cmd(runner, self.repo)
                rc, out = run(cmd, cwd, self.env, self.timeout)
                self._pytest[key] = (rc, out)
            rc, out = self._pytest[key]
            if rc is None:
                return False, "基线超时/无法起进程"
            if rc != 0:
                return False, "基线红：%s" % _first_failure(out)
            return True, "ok"
        if self._plugin is None:
            cmd, cwd = _cmd(runner, self.repo)
            rc, out = run(cmd, cwd, self.env, self.timeout)
            self._plugin = (rc, _plugin_result_ok(out))
        rc, got = self._plugin
        if got is None:
            return False, "基线：插件脚本没跑起来（rc=%s）" % rc
        bad = [n for n in runner["expect"] if not any(g.startswith(n) and v == "ok"
                                                      for g, v in got.items())]
        if bad:
            return False, "基线红：%s" % ",".join(bad)
        return True, "ok"


def _plugin_result_ok(out: str) -> dict[str, str] | None:
    got = _plugin_results(out)
    return got or None


# ── 判定 ─────────────────────────────────────────────────────────────

def judge(runner: dict, rc: int | None, out: str) -> tuple[str, str]:
    if rc is None:
        return "INVALID", out[:80] or "超时/无法起进程"
    if runner["kind"] == "pytest":
        if rc in (4, 5) or "no tests ran" in out:
            return "INVALID", "node id 不存在/未收集（rc=%s）" % rc
        if rc == 0:
            return "ALIVE", "目标用例仍全绿"
        return "CAUGHT", _first_failure(out)
    got = _plugin_results(out)
    if not got:
        return "INVALID", "插件脚本无任何用例结果（rc=%s）：%s" % (rc, out.strip()[-120:])
    hit = [n for n in runner["expect"]
           if any(g.startswith(n) and v == "FAIL" for g, v in got.items())]
    if hit:
        return "CAUGHT", "%s 失败" % ",".join(hit)
    missing = [n for n in runner["expect"] if not any(g.startswith(n) for g in got)]
    if missing:
        return "INVALID", "输出里找不到用例 %s" % ",".join(missing)
    return "ALIVE", "%s 全绿（rc=%s）" % (",".join(runner["expect"]), rc)


# ── 单变异体执行 ─────────────────────────────────────────────────────

def evaluate(m: dict, baseline: Baseline | None, budget: float) -> dict:
    t0 = time.time()
    deadline = t0 + budget
    base = make_sandbox()
    repo = base / "repo"
    try:
        target = repo / m["rel"]
        if not target.is_file():
            return dict(verdict="INVALID", detail="目标文件不存在：%s" % m["rel"])
        src = target.read_text(encoding="utf-8")
        n = src.count(m["old"])
        if n != 1:
            return dict(verdict="INVALID",
                        detail="原文出现 %d 次（须恰好 1 次）→ 变异体失效" % n)
        mutated = src.replace(m["old"], m["new"], 1)
        if m["rel"].endswith(".py"):
            try:
                compile(mutated, str(target), "exec")
            except SyntaxError as exc:
                return dict(verdict="INVALID", detail="变异后语法错误：%s" % exc)
        target.write_text(mutated, encoding="utf-8")

        env = child_env(repo)
        details, verdict = [], "ALIVE"
        for runner in m["runners"]:
            if baseline is not None:
                ok, why = baseline.check(runner)
                if not ok:                       # 基线不绿 → 这次『抓到』毫无意义
                    return dict(verdict="INVALID", detail="基线不绿，判定无效：%s" % why,
                                elapsed=time.time() - t0)
            left = min(deadline - time.time(), budget)
            if left <= 5:
                return dict(verdict="INVALID", detail="超时（单变异体预算 %.0fs 耗尽）"
                            % budget, elapsed=time.time() - t0)
            cmd, cwd = _cmd(runner, repo)
            rc, out = run(cmd, cwd, env, left)
            v, d = judge(runner, rc, out)
            details.append("%s: %s" % (runner["kind"], d))
            if v == "CAUGHT":
                verdict = "CAUGHT"
                break
            if v == "INVALID" and verdict == "ALIVE":
                verdict = "INVALID"
        return dict(verdict=verdict, detail="；".join(details)[:150],
                    elapsed=time.time() - t0)
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── 输出 ─────────────────────────────────────────────────────────────

def w(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def fmt(name: str, result: str, note: str, width: int) -> str:
    return "%s %s| %-7s | %s" % (name, " " * max(1, width - w(name)),
                                 result, note)


def main() -> int:
    ap = argparse.ArgumentParser(description="记忆生态 v3.2.0 变异测试")
    ap.add_argument("-k", default="", help="只跑名字/组名含该串的变异体")
    ap.add_argument("--timeout", type=float, default=180.0, help="单变异体预算（秒）")
    ap.add_argument("--no-baseline", action="store_true", help="跳过基线校验（不推荐）")
    ap.add_argument("--keep", action="store_true", help="保留沙盒（排障用）")
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    picked = [m for m in MUTANTS if not args.k or args.k in m["name"]
              or args.k in m["group"]]
    if not picked:
        print("没有匹配 -k %r 的变异体" % args.k)
        return 1

    print("=" * 78)
    print("记忆生态 v3.2.0 · 变异测试（证明现有测试真的能抓 bug）")
    print("沙盒: %%TEMP%% 副本（绝不改工作区源码）  单变异体预算: %.0fs" % args.timeout)
    print("=" * 78)

    baseline, bbase = None, None
    if not args.no_baseline:
        print("▶ 基线：在未变异的副本上确认目标用例本来是全绿的 …", flush=True)
        bbase = make_sandbox()
        baseline = Baseline(bbase / "repo", child_env(bbase / "repo"), args.timeout)
        print("  基线副本: %s" % (bbase / "repo"), flush=True)

    # 基线预热：把本轮所有目标用例先在**未变异**副本上各跑一遍并登记。
    # 基线红 → 该变异体的判定无效（没有基线的"抓到"只是碰运气）。
    if baseline is not None:
        seen = []
        for m in picked:
            for r in m["runners"]:
                key = (r["kind"], r.get("nodes") or r.get("expect"))
                if key in seen:
                    continue
                seen.append(key)
                ok, why = baseline.check(r)
                label = ",".join(r.get("nodes") or r.get("expect") or ())
                print("  基线 %-72s %s" % (label[-72:], "ok" if ok else why), flush=True)

    results = []
    t_all = time.time()
    try:
        for m in picked:
            print("\n=== %s ===" % m["name"], flush=True)
            print("  目的: %s" % m["why"], flush=True)
            r = evaluate(m, baseline, args.timeout)
            r["mutant"] = m
            results.append(r)
            icon = {"CAUGHT": "✅ 抓到", "ALIVE": "❌ 存活（测试无效！）",
                    "INVALID": "⚠️ 无效（变异体构造不当）"}[r["verdict"]]
            print("  %s  %s" % (icon, r["detail"]), flush=True)
    finally:
        if bbase is not None and not args.keep:
            shutil.rmtree(bbase, ignore_errors=True)

    width = max(w(r["mutant"]["name"]) for r in results) + 2
    print("\n" + "=" * 78)
    print("变异体结果")
    print("=" * 78)
    print(fmt("名称", "结果", "说明", width))
    print("-" * (width + 78))
    for r in results:
        print(fmt(r["mutant"]["name"], r["verdict"], r["detail"], width))

    required = [r for r in results if not r["mutant"].get("extra")]
    extra = [r for r in results if r["mutant"].get("extra")]

    def tally(rs):
        c = sum(1 for r in rs if r["verdict"] == "CAUGHT")
        a = sum(1 for r in rs if r["verdict"] == "ALIVE")
        i = sum(1 for r in rs if r["verdict"] == "INVALID")
        return c, a, i, len(rs)

    c, a, i, n = tally(required)
    print("\n必测清单：%d/%d 被抓到（存活 %d，无效 %d）" % (c, n, a, i))
    if extra:
        ec, ea, ei, en = tally(extra)
        print("附加变异体：%d/%d 被抓到（存活 %d，无效 %d）" % (ec, en, ea, ei))
    print("总耗时 %.1fs" % (time.time() - t_all))

    bad = [r for r in results if r["verdict"] != "CAUGHT"]
    if bad:
        print("\n" + "=" * 78)
        print("未抓到的变异体：为什么，以及该补什么断言")
        print("=" * 78)
        for r in bad:
            m = r["mutant"]
            print("\n[%s] %s → %s" % (m["name"], r["verdict"], r["detail"]))
            print("  应为测试所覆盖的行为: %s" % m["why"])
            print("  建议: %s" % m["hint"])
    print("\n" + ("全部变异体被抓到 ✅" if not bad
                  else "有 %d 个未被抓到 —— 测试集在这些缺陷上无效 ❌" % len(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())

