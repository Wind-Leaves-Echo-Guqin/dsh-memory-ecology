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

import os
import sys
import tempfile
import time
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


class TestFirstAidStatusMachine(unittest.TestCase):
    """急救箱 add() 状态机（审计 05-P1-1）。

    旧实现：`status = "ok" if ok else ("fixed" if action and fix else "fail")`
    —— 只要检查项带了处置文案、又是 --fix 模式，就被标成"已修复"，哪怕修复逻辑
    根本没碰它。库损坏 / 数据根缺失 / 检索 CLI 缺失 / cron 报错全是这种，于是
    all_ok 恒 True、退出码恒 0，把真实故障报成"全部通过"。

    现在的口径：fixed 只能由**真执行了修复且复评通过**的调用点显式给。
    本类钉住四件事：
      ① 没有修复逻辑的失败项，--fix 下也必须 fail（不是 fixed）；
      ② 有修复逻辑但没修成（删除失败），也必须 fail；
      ③ 真修成了才算 fixed；
      ④ 只诊断模式永不出现 fixed。
    """

    def setUp(self):
        import first_aid  # noqa: E402
        self.fa = first_aid
        self._tmp = tempfile.TemporaryDirectory(prefix="eco-firstaid-")
        self.root = Path(self._tmp.name)
        self._env_backup = {}
        for k in ("MEMORY_ECOLOGY_ROOT", "ECO_SCRIPTS_DIR"):
            self._env_backup[k] = os.environ.get(k)
        os.environ["MEMORY_ECOLOGY_ROOT"] = str(self.root)
        os.environ["ECO_SCRIPTS_DIR"] = str(self.root / "scripts")
        # 隔离两个会看真实环境的探测：端口（可能 taskkill 真人进程）与 pythonw
        self._orig_pids = first_aid._pids_listening
        first_aid._pids_listening = lambda port: []
        # 索引新鲜 → 不触发 120s 的 eco_search 子进程
        (self.root / "ecosystem.db").write_bytes(b"")
        (self.root / "memories").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.fa._pids_listening = self._orig_pids
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _check(self, result, cid):
        for c in result["checks"]:
            if c["id"] == cid:
                return c
        self.fail("检查项 %r 不在结果里：%s" % (cid, [c["id"] for c in result["checks"]]))

    def _broken_root(self):
        """关键文件全缺（数据根损坏），且 eco.db 不存在 —— 这两项都没有自动修复逻辑。"""
        return self.root

    def test_failures_without_fix_logic_stay_fail_under_fix(self):
        """①②的核心：--fix 下，没修复逻辑的失败项必须 fail，不能被报成 fixed。"""
        out = self.fa.diagnose(fix=True)
        for cid in ("dataroot", "db-eco.db", "db-executions.db"):
            c = self._check(out, cid)
            self.assertEqual(
                c["status"], "fail",
                "%s 在 --fix 下被报成 %r —— 它没有任何自动修复逻辑，只能是 fail"
                % (cid, c["status"]))
        self.assertFalse(out["all_ok"], "存在真实失败项时 all_ok 必须为 False")
        self.assertFalse(
            any(c["status"] == "fixed" for c in out["checks"]),
            "没有任何修复动作被执行，不该出现 fixed：%s"
            % [c["id"] for c in out["checks"] if c["status"] == "fixed"])

    def test_diagnose_only_never_reports_fixed(self):
        out = self.fa.diagnose(fix=False)
        self.assertFalse([c["id"] for c in out["checks"] if c["status"] == "fixed"])
        self.assertFalse(out["all_ok"])

    def _stale_lock(self):
        lock = self.root / "memories" / "stale.lock"
        lock.write_text("x", encoding="utf-8")
        old = time.time() - 3 * 3600
        os.utime(lock, (old, old))
        return lock

    def test_lock_actually_removed_marks_fixed(self):
        """③真修成了才 fixed。"""
        lock = self._stale_lock()
        out = self.fa.diagnose(fix=True)
        self.assertFalse(lock.exists(), "陈旧锁应被删除")
        self.assertEqual(self._check(out, "locks")["status"], "fixed")

    def test_lock_removal_failure_stays_fail(self):
        """②有修复逻辑但没修成 → 必须 fail（旧实现在这里会说"已清理"）。"""
        self._stale_lock()
        orig = Path.unlink

        def boom(self, *a, **kw):
            raise OSError("模拟占用")

        Path.unlink = boom
        try:
            out = self.fa.diagnose(fix=True)
        finally:
            Path.unlink = orig
        self.assertEqual(self._check(out, "locks")["status"], "fail",
                         "删除失败却报 fixed，就是把没修报成已修（05-P1-1 原样）")
        self.assertFalse(out["all_ok"])

    def test_all_ok_implies_no_fail(self):
        """all_ok 与 status 必须自洽（旧实现里两者会互相打架）。"""
        for fix in (False, True):
            out = self.fa.diagnose(fix=fix)
            fails = [c["id"] for c in out["checks"] if c["status"] == "fail"]
            self.assertEqual(out["all_ok"], not fails,
                             "fix=%s: all_ok=%s 但 fail=%s" % (fix, out["all_ok"], fails))


if __name__ == "__main__":
    unittest.main(verbosity=2)
