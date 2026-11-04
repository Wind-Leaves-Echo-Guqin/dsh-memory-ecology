#!/usr/bin/env python3
"""审计 04-P1-5 / 04-P1-8 回归（3.4.0 PR-A2）。

1. eco_note_adopt._is_accepted_line：短 trigger 不得被长 trigger 的已接纳
   标记行误杀（原 `trigger in line` 子串匹配 → "备份" 撞 "备份判据" 的
   标记行 → 候选静默永久跳过、无痕丢失）。
2. eco_backup._sweep_orphans：崩溃残留的明文中转（.plain-*）与演练临时
   （.tmp-drill-*）必须被清扫；1 小时内的新文件（可能正在写）不动。

隔离：全部 tempfile，绝不触生产根 / 生产 backup_core。
说明：04-P1-3（distill 双计数）与 04-P1-6（approved glob）深嵌 _run 主
流程，修复为删行/并集 glob 的极小 diff，由 src 全量回归守护，不单独
构造端到端。
运行: python memory_ecology/test_p34_adopt_backup.py
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import eco_note_adopt as adopt
import eco_backup as eb


class TestAcceptedLineExactMatch(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.p = Path(self._td.name) / "cand-1.md"
        # 长 trigger "备份判据" 已被接纳（标记行由 mark_accepted 写出）；
        # 短 trigger "备份" 与 "缓存" 未接纳
        self.p.write_text(
            "<!-- accepted 2026-11-05T00:00:00 -->- [error] **备份判据**\n"
            "- [error] **备份**\n"
            "- [error] **缓存**\n",
            encoding="utf-8")

    def tearDown(self):
        self._td.cleanup()

    def test_long_marked_trigger_detected(self):
        self.assertTrue(adopt._is_accepted_line(self.p, "备份判据"))

    def test_short_trigger_not_killed_by_long_marker(self):
        """修复点：'备份' in '...**备份判据**...' 曾恒 True → 短候选无痕丢失。"""
        self.assertFalse(adopt._is_accepted_line(self.p, "备份"),
                         "短 trigger 被长 trigger 的标记行误杀（04-P1-5）")

    def test_unmarked_trigger_false(self):
        self.assertFalse(adopt._is_accepted_line(self.p, "缓存"))

    def test_empty_trigger_false(self):
        self.assertFalse(adopt._is_accepted_line(self.p, ""))


class TestSweepOrphans(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.bak = Path(self._td.name) / "backup_core"
        self.bak.mkdir(parents=True)
        self._orig_dir = eb.BACKUP_DIR
        eb.BACKUP_DIR = self.bak

    def tearDown(self):
        eb.BACKUP_DIR = self._orig_dir
        self._td.cleanup()

    def _mk(self, name: str, age_hours: float) -> Path:
        f = self.bak / name
        f.write_text("x", encoding="utf-8")
        old = time.time() - age_hours * 3600
        import os
        os.utime(f, (old, old))
        return f

    def test_stale_plain_and_drill_removed(self):
        plain = self._mk(".plain-index.db-20261101-000000", 48)
        drill = self._mk(".tmp-drill-abc", 48)
        eb._sweep_orphans()
        self.assertFalse(plain.exists(), "明文中转残留未被清扫（04-P1-8）")
        self.assertFalse(drill.exists(), "演练临时残留未被清扫")

    def test_fresh_files_untouched(self):
        fresh = self.bak / ".plain-index.db-justnow"
        fresh.write_text("x", encoding="utf-8")   # 当前时间 = 1 小时内
        eb._sweep_orphans()
        self.assertTrue(fresh.exists(), "1 小时内的新中转被误删（可能正在写）")

    def test_never_touches_other_files(self):
        keep = self._mk("MEMORY.md.bak-core-20261101-000000.enc", 48)
        eb._sweep_orphans()
        self.assertTrue(keep.exists(), "清扫碰了备份本体（超范围删除）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
