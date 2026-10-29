#!/usr/bin/env python3
"""数据安全性 + 旧库兼容（v3.2.0）。

这一组回答两个问题：
  1. **在真实旧数据上，新核心会不会写坏东西**（只读打开、不迁移、不改 mtime）
  2. **旧库的格式能不能被正确读进来**（frontmatter 词汇、`§` 分隔、BOM、残缺字段）

设计原则：**测试跑在副本上**。真实生产根（`%LOCALAPPDATA%\\hermes`）只做**只读**断言
（打开、解析、计数），绝不写。若生产根不存在，相关用例自动 skip 而不是失败——
这个项目要能在一台没装过 Hermes 的机器上跑（那是 v3.2.0 的硬判据）。

运行：python tests/test_compat.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from memory_ecology import (config, gates, ledger, packs, paths, retrieve,  # noqa: E402
                            root as rootmod, store)
from memory_ecology.cli import main as cli_main  # noqa: E402

HERMES = Path(os.environ.get("LOCALAPPDATA") or "") / "hermes"


def production_root() -> Path | None:
    """真实生产根（只读用途）。不存在返回 None。"""
    if HERMES.is_dir() and (HERMES / "memories").is_dir():
        return HERMES
    return None


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="eco-compat-"))
        self.root = self.tmp / "root"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def init(self):
        cli_main(["--root", str(self.root), "init"])
        return rootmod.Root(self.root).open()

    def cli(self, *argv) -> dict:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli_main(["--root", str(self.root), *argv])
        out = buf.getvalue().strip().splitlines()
        self.assertTrue(out, "无输出：%s" % (argv,))
        data = json.loads(out[-1])
        data["_code"] = code
        return data


# ── 旧库格式兼容 ──────────────────────────────────────────────────────

class TestOldFormat(Base):
    """旧库（memory-ecology v3.x）的格式必须能被读进来，且词汇不退化。"""

    def _write_detail(self, name: str, fm: str, body: str) -> None:
        d = self.root / "memories" / "detail"
        d.mkdir(parents=True, exist_ok=True)
        (d / (name + ".md")).write_text("---\n%s\n---\n\n%s\n" % (fm, body),
                                        encoding="utf-8")

    def test_old_frontmatter_all_fields_survive(self):
        self.init()
        self._write_detail("old-1", "\n".join([
            "type: episodic", "status: active", "occurrences: 7",
            "session_count: 3", "first_seen: 2026-09-01", "last_seen: 2026-10-01",
            "last_hit: 2026-10-20", "valid_time: 2026-10-01",
            "transaction_time: 2026-10-01T13:05:54", "last_verified: 2026-10-01",
            "origin_session_id: sess-abc", "superseded_by:",
        ]), "旧格式的一条情景记忆")
        it = rootmod.Root(self.root).open().item("old-1")
        self.assertIsNotNone(it)
        self.assertEqual(it["type"], "episodic")
        self.assertEqual(it["status"], "active")
        self.assertEqual(it["fm"]["occurrences"], "7")
        self.assertEqual(it["fm"]["session_count"], "3")
        self.assertEqual(it["fm"]["origin_session_id"], "sess-abc")

    def test_old_type_vocabulary_not_degraded(self):
        """4 类旧词汇必须各归其位（退化 = 以后挂载旧库时每条都变同一类）。"""
        self.init()
        for i, t in enumerate(("semantic", "episodic", "procedural", "lesson")):
            self._write_detail("t%d" % i, "type: %s\nstatus: active" % t, "第 %d 类" % i)
        types = {it["type"] for it in rootmod.Root(self.root).open().items()}
        self.assertEqual(types, {"semantic", "episodic", "procedural", "lesson"})

    def test_old_l1_section_split(self):
        self.init()
        mem = self.root / "memories" / "MEMORY.md"
        # **必须写字节**：Python 文本模式写入会把 \n 翻成 \r\n，
        # 于是 \r\n 变成 \r\r\n，测的就不是我们要测的东西了。
        mem.write_bytes("第一条内容\n\n§\n\n第二条内容\r\n§\r\n第三条内容\n".encode("utf-8"))
        entries, nl = store.parse_l1(paths.read_text(mem))
        self.assertEqual(len(entries), 3, entries)
        self.assertEqual(entries[0], "第一条内容")
        self.assertIn("第三条内容", entries[2])
        self.assertEqual(nl, "\r\n", "换行风格识别错了（会导致写回时全文件换行变化）")

    def test_l1_roundtrip_preserves_crlf(self):
        """**写回必须保持原换行风格**：否则每写一次就是一整个文件的 diff。"""
        self.init()
        mem = self.root / "memories" / "MEMORY.md"
        mem.write_bytes("甲\r\n§\r\n乙\r\n".encode("utf-8"))
        entries, nl = store.parse_l1(paths.read_text(mem))
        self.assertEqual(nl, "\r\n")
        paths.write_atomic(mem, store.serialize_l1(entries, nl))
        raw = mem.read_bytes()
        self.assertIn(b"\r\n", raw, "CRLF 文件被写成了 LF")
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""),
                         "写回后混入了孤立 LF（换行风格不纯）")
        entries2, nl2 = store.parse_l1(paths.read_text(mem))
        self.assertEqual(entries2, entries, "写回后条目变了")
        self.assertEqual(nl2, "\r\n", "写回后换行风格变了")

    def test_bom_first_entry_not_lost(self):
        """**BOM 会让首条静默丢失** —— 这是旧项目实测踩过的坑。

        断言必须够严：`assertIn("首条带 BOM", entries[0])` 对
        `'\\ufeff首条带 BOM'` 也成立，等于什么都没测（变异实测：去掉 BOM 处理
        仍然全绿）。所以这里直接断言 **BOM 字符不在结果里**。
        """
        self.init()
        mem = self.root / "memories" / "MEMORY.md"
        mem.write_bytes("\ufeff首条带 BOM\n§\n第二条\n".encode("utf-8"))
        entries, _ = store.parse_l1(paths.read_text(mem))
        self.assertEqual(len(entries), 2, "首条被 BOM 吃掉了：%s" % entries)
        self.assertNotIn("\ufeff", entries[0], "首条里还残留 BOM 字符")
        self.assertTrue(entries[0].startswith("首条"), "entries[0]=%r" % entries[0])

    def test_bom_in_detail_frontmatter(self):
        """BOM 出现在 detail 文件里时，frontmatter 仍要能解析。"""
        self.init()
        d = self.root / "memories" / "detail"
        d.mkdir(parents=True, exist_ok=True)
        (d / "bom.md").write_bytes(
            "\ufeff---\ntype: procedural\nstatus: active\n---\n\n带 BOM 的正文\n".encode("utf-8"))
        it = rootmod.Root(self.root).open().item("bom")
        self.assertIsNotNone(it)
        self.assertEqual(it["type"], "procedural", "BOM 让 frontmatter 解析失败了")
        self.assertEqual(it["body"].strip(), "带 BOM 的正文")

    def test_parse_frontmatter_handles_bom_directly(self):
        """直接喂带 BOM 的**字符串**（不经过 read_text）—— 钉住 store 这层防御本身。

        为什么单独测：`paths.read_text` 用 `utf-8-sig` 打开，BOM 在进 store 之前
        就没了，于是只测"读文件"的话，store 里的 BOM 分支是死代码（变异实测存活）。
        """
        fm, body = store.parse_frontmatter("\ufeff---\ntype: semantic\nstatus: active\n---\n正文")
        self.assertEqual(fm.get("type"), "semantic", "BOM 让 frontmatter 的第一行失效了")
        self.assertEqual(body, "正文")

    def test_missing_frontmatter_is_not_fatal(self):
        self.init()
        d = self.root / "memories" / "detail"
        d.mkdir(parents=True, exist_ok=True)
        (d / "nofm.md").write_text("完全没有 frontmatter 的正文\n", encoding="utf-8")
        items = rootmod.Root(self.root).open().items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "semantic", "无类型应归一为 semantic 而不是空")
        self.assertEqual(items[0]["status"], "active", "无状态应视为 active")

    def test_corrupt_frontmatter_degrades_not_crashes(self):
        """frontmatter 坏了不要崩——**而且不能因为"坏了"就变成全局可见**。

        ⚠️ 这条用例原来断言 `out["detail"] == 1`，**等于把 H2 的 bug 当成了规格**：
        宽松解析对坏 frontmatter 返回 `({}, 全文)`，`scope` 缺失于是回落成 `"global"`
        —— 最宽的取值。只要损坏一个字节（把结束分隔符 `---` 写成 `--x`），
        一条圈内机密就单向放宽成全局可见（实测复现）。

        正确规格是**隔离**：解析失败 → 不进目录/检索/门（`detail` 不计数），
        但必须如实报告在 `damaged[]` 里——"看不见"不等于"没发生"。
        """
        self.init()
        d = self.root / "memories" / "detail"
        d.mkdir(parents=True, exist_ok=True)
        (d / "bad.md").write_text("---\ntype: seman\n没有结束分隔符\n正文\n",
                                  encoding="utf-8")
        out = self.cli("stats")     # 不应崩
        self.assertTrue(out["ok"])
        self.assertEqual(out["detail"], 0,
                         "损坏的 detail 仍被计入目录 —— 它会以 scope=global 泄漏到所有圈")
        health = self.cli("health")
        self.assertTrue(health["ok"], "health 不该因为一条坏文件就崩")
        self.assertEqual([x["id"] for x in health["damaged"]], ["bad"],
                         "损坏文件没有出现在 health.damaged[] 里 —— 问题隐身了")
        self.assertEqual(health["status"], "warn", "有损坏条目时 health 应给出 warn")
        self.assertTrue(any("损坏" in n for n in health["notes"]),
                        "notes 里没有说明损坏：%s" % health["notes"])

    def test_empty_body_skipped(self):
        self.init()
        d = self.root / "memories" / "detail"
        d.mkdir(parents=True, exist_ok=True)
        (d / "empty.md").write_text("---\ntype: semantic\n---\n\n\n", encoding="utf-8")
        self.assertEqual(len(rootmod.Root(self.root).open().items()), 0)


# ── 真实生产根：只读断言 ──────────────────────────────────────────────

class TestProductionReadOnly(unittest.TestCase):
    """在**真实生产数据**上验证"只读打开不会写坏任何东西"。

    只做：打开、解析、计数、读账本。**绝不调用任何写命令**。
    """

    def setUp(self):
        self.prod = production_root()
        if self.prod is None:
            self.skipTest("本机没有旧生产根（干净机器上跳过是对的）")

    def _fingerprint(self) -> dict:
        out = {}
        for p in (self.prod / "memories").rglob("*"):
            if p.is_file():
                try:
                    st = p.stat()
                    out[str(p)] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    pass
        return out

    def test_old_detail_parses(self):
        n = 0
        for f in (self.prod / "memories" / "detail").glob("*.md"):
            text = paths.read_text(f)
            fm, body = store.parse_frontmatter(text)
            self.assertIsInstance(fm, dict)
            n += 1
        print("\n    旧库 detail 可解析：%d 篇" % n)

    def test_old_l1_parses(self):
        for name in ("MEMORY.md", "USER.md"):
            p = self.prod / "memories" / name
            if not p.is_file():
                continue
            entries, nl = store.parse_l1(paths.read_text(p))
            self.assertGreater(len(entries), 0, "%s 解析出 0 条" % name)
            self.assertIn(nl, ("\n", "\r\n"))
            print("    %s: %d 条 / %d 字符" % (name, len(entries), store.chars_of(entries)))

    def test_old_ledger_readonly_open(self):
        db = self.prod / "eco.db"
        if not db.is_file():
            self.skipTest("无 eco.db")
        conn = ledger.open_ro(db)
        self.assertIsNotNone(conn)
        try:
            gs = ledger.gate_status(conn, stale_days=config.effective_gate_stale_days())
        finally:
            conn.close()
        self.assertEqual(len(gs["gates"]), 4)
        by = {g["gate"]: g for g in gs["gates"]}
        print("\n    旧库四门：%s" % {k: (v["effective"], v["expected_count"])
                                     for k, v in by.items()})
        # 这两条是本项目的**历史证据**，也是 v3.2.0 存在的理由：
        #   门② 挤出过 52 次，但**从未提升过任何条目**（L1 是净流出系统）
        #   门③ 产过 89 个候选，但**从未晋升过一条**（上游在产、下游从不合流）
        self.assertEqual(by["gate2"]["expected_count"], 0,
                         "旧库门② 居然有 promote —— 请重新核对历史结论")
        self.assertEqual(by["gate3"]["expected_count"], 0,
                         "旧库门③ 居然有 promote —— 请重新核对历史结论")
        # 而"有动作"不等于"健康"：门② 的 effective 是 56（52 挤出 + 3 手挤 + 1 回滚），
        # **判死不能只看 effective>0** —— 这正是 starved 这个 kind 存在的理由。
        self.assertGreater(by["gate2"]["effective"], 0)
        self.assertEqual(by["gate2"]["kind"], "starved",
                         "门② 挤出 56 次 0 提升，却没被判成 starved")
        self.assertTrue(by["gate2"]["dead"])

    def test_readonly_scan_does_not_touch_files(self):
        """**核心断言**：只读扫描前后，生产根所有文件的 size/mtime 必须一字不差。"""
        before = self._fingerprint()
        # 只读动作
        for f in (self.prod / "memories" / "detail").glob("*.md"):
            store.parse_frontmatter(paths.read_text(f))
        store.parse_l1(paths.read_text(self.prod / "memories" / "MEMORY.md"))
        conn = ledger.open_ro(self.prod / "eco.db")
        if conn is not None:
            ledger.gate_status(conn)
            conn.close()
        after = self._fingerprint()
        changed = {k: (before[k], after[k]) for k in before if before.get(k) != after.get(k)}
        self.assertEqual(changed, {}, "只读扫描改动了生产文件：%s"
                         % list(changed.items())[:3])


# ── 只读挂载语义（3.2.0 不做 mount 旧根，但要做到"不误写"）───────────

class TestNoCrossRootWrite(Base):
    def test_dsh_root_isolated_from_old_root(self):
        """dsh 根与旧根是**两个根**：写 dsh 根不得碰旧根的任何一个字节。"""
        prod = production_root()
        if prod is None:
            self.skipTest("无旧根")
        before = {}
        for p in (prod / "memories").rglob("*"):
            if p.is_file():
                st = p.stat()
                before[str(p)] = (st.st_size, st.st_mtime_ns)
        self.init()
        self.cli("deposit", "--session", "s", "--text", "新的 dsh 侧记忆")
        self.cli("gate", "all")
        self.cli("materialize")
        after = {}
        for p in (prod / "memories").rglob("*"):
            if p.is_file():
                st = p.stat()
                after[str(p)] = (st.st_size, st.st_mtime_ns)
        self.assertEqual(before, after, "写 dsh 根时碰了旧根")


# ── 数据安全：损坏与异常输入 ──────────────────────────────────────────

class TestDataSafety(Base):
    def test_corrupt_circles_json_fails_open(self):
        self.init()
        (self.root / "circles.json").write_text("{坏 json", encoding="utf-8")
        cfg = store.load_circles(self.root)
        self.assertIsInstance(cfg, dict)
        self.assertEqual(store.route_circle(self.root, "D:/x"), "global",
                         "损坏的 circles.json 让路由崩了")

    def test_corrupt_packs_json_fails_open(self):
        self.init()
        (self.root / "packs.json").write_text("[[[", encoding="utf-8")
        reg = packs.load_registry(rootmod.Root(self.root).open())
        self.assertIn("packs", reg)

    def test_corrupt_root_json_readonly(self):
        self.root.mkdir(parents=True)
        (self.root / ".eco").mkdir()
        (self.root / ".eco" / "root.json").write_text("not json", encoding="utf-8")
        out = self.cli("gate", "1")
        self.assertEqual(out["_code"], 3)
        self.assertIn("corrupt", out["reason"])

    def test_truncated_db_does_not_crash_health(self):
        self.init()
        db = self.root / "eco.db"
        db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 40)   # 半截文件
        out = self.cli("health")
        self.assertTrue(out["ok"], "损坏的账本让 health 崩了（GUI 会白屏）")

    def test_huge_body_is_truncated_not_rejected(self):
        self.init()
        self.cli("deposit", "--session", "s", "--text", "长" * 5000)
        self.cli("gate", "1")
        items = rootmod.Root(self.root).open().items()
        self.assertEqual(len(items), 1)
        self.assertLessEqual(len(items[0]["body"]), config.BODY_MAX)

    def test_empty_and_whitespace_deposit_rejected(self):
        self.init()
        self.assertFalse(self.cli("deposit", "--session", "s", "--text", "   ")["ok"])
        self.assertFalse(self.cli("deposit", "--session", "s", "--text", "")["ok"])

    def test_scope_sanitized_no_path_escape(self):
        """圈名会被拼进目录名 —— 必须清洗，否则 `--scope ../../x` 就能写到根外。"""
        self.init()
        for evil in ("../../etc", "..\\..\\win", "a/b", "  ", "x" * 100):
            out = self.cli("deposit", "--session", "s", "--text", "内容", "--scope", evil)
            self.assertTrue(out["ok"], out)
            self.assertFalse("/" in out["scope"] or "\\" in out["scope"],
                             "scope 未被清洗：%r" % out["scope"])
            self.assertFalse(out["scope"].startswith("."))
            self.assertLessEqual(len(out["scope"]), 40)
        # 所有候选文件都必须在 pending 子树内
        base = (self.root / "memories" / "pending").resolve()
        for f in (self.root / "memories" / "pending").rglob("*.md"):
            self.assertTrue(str(f.resolve()).startswith(str(base)),
                            "候选文件跑到了 pending 外：%s" % f)

    def test_global_scope_names_collide_safely(self):
        self.init()
        for evil in (".", "..", "global", "GLOBAL", "con", "aux"):
            out = self.cli("deposit", "--session", "s", "--text", "x" + evil, "--scope", evil)
            self.assertTrue(out["ok"])
        for f in (self.root / "memories" / "pending").rglob("*.md"):
            self.assertTrue(f.is_file())

    def test_session_name_sanitized(self):
        self.init()
        out = self.cli("deposit", "--session", "../../evil", "--text", "内容")
        self.assertTrue(out["ok"])
        self.assertFalse("/" in out["file"] or "\\" in out["file"])
        self.assertFalse(out["file"].startswith("."))


if __name__ == "__main__":
    unittest.main(verbosity=2)
