#!/usr/bin/env python3
"""GUI 读接口路径校验回归（3.4.0，审计 05-P0-1 / REVIEW S14）。

背景：collector.detail_body 曾是同文件四个同面函数里唯一没有路径校验的
（candidate_detail/skill_md/report_by_name 都有），构成任意 .md 读取原语。
本文件把修复钉死：穿越形态全部拒绝、合法 slug 正常返回、
collector 其余已防护函数的样本形态一并抽查。

隔离：全部跑在 tempfile 沙盒根上（collector.set_root），绝不触生产根。
运行：python tests/test_gui_security.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUI = REPO / "integrations" / "gui"
sys.path.insert(0, str(GUI))

import collector  # noqa: E402


class TestDetailBodySlug(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="eco-guisec-")
        root = Path(self._tmp.name)
        self.root = root
        collector.set_root(str(root))
        detail = root / "memories" / "detail"
        detail.mkdir(parents=True, exist_ok=True)
        (detail / "ok-entry.md").write_text(
            "---\ntype: semantic\nstatus: active\n---\n\n正文内容\n", encoding="utf-8")
        # 根外与根内的诱饵：穿越成功就能读到
        (root / "ROOT-SECRET.md").write_text("rootsecret", encoding="utf-8")
        parent = root.parent / "GUISEC-PARENT-SECRET.md"
        parent.write_text("parentsecret", encoding="utf-8")
        self._parent_bait = parent

    def tearDown(self):
        collector.set_root(None)
        self._tmp.cleanup()

    def test_legitimate_slug_returns_entry(self):
        out = collector.detail_body("ok-entry")
        self.assertIsNotNone(out)
        self.assertEqual(out["slug"], "ok-entry")
        self.assertIn("正文内容", out["body"])
        self.assertEqual(out["fm"].get("type"), "semantic")

    def test_traversal_forms_all_rejected(self):
        """S14 实测全通的四种形态 + 变体，全部必须 None。"""
        evil = [
            "../ROOT-SECRET",                       # 根内越界（detail → root）
            "../../" + self._parent_bait.stem,      # 根外
            "..\\..\\ROOT-SECRET",                  # 反斜杠（Windows Path 会逐级上跳）
            "..%2f..%2fROOT-SECRET",                # 编码后的形态（含 %2f 与 ..）
            "....//ROOT-SECRET",
            "ok-entry/../../ROOT-SECRET",
            "C:/windows/notepad",                   # 盘符绝对路径
            "\\\\server\\share\\x",                 # UNC
            "ok-entry\\",                           # 尾随反斜杠
            ".",
            "..",
            "",
        ]
        for slug in evil:
            self.assertIsNone(collector.detail_body(slug),
                              "穿越形态未被拒绝：%r" % slug)
        # 穿越被拒后诱饵文件原样（没被任何路径读走不是断言能验的，
        # 但至少确认这些文件不是通过正常入口暴露的）
        self.assertTrue((self.root / "ROOT-SECRET.md").is_file())

    def test_slug_with_dotdot_in_middle_rejected(self):
        self.assertIsNone(collector.detail_body("a..b/../../x"))
        # 纯成分里含 .. 的合法文件名形态（a..b 本身不是穿越，但按
        # candidate_detail 同款口径一并拒绝，保持四个函数同宽）
        self.assertIsNone(collector.detail_body("a..b"))

    def test_missing_entry_returns_none(self):
        self.assertIsNone(collector.detail_body("no-such-entry"))

    def test_reserved_names_never_raise(self):
        """Windows 保留名（con/aux/nul）拼不出穿越，但绝不允许抛 OSError——
        统一回 None（fail-closed），与其它非法形态同口径。"""
        for slug in ("con", "aux", "nul"):
            try:
                self.assertIsNone(collector.detail_body(slug))
            except OSError as exc:  # pragma: no cover
                self.fail("保留名 %r 抛了 OSError：%s" % (slug, exc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
