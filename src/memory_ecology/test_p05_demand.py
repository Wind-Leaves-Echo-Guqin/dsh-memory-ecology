#!/usr/bin/env python3
"""批 3 需求链路回归（P0.5，2026-10-04）

1. memory_query 打分：名字命中 > 正文命中；active 优于 dormant；归档降权
2. 命中回写：last_hit=今天、use_count+1；缺行自动补
3. 遥测 JSONL 与复活提案文件落盘（--no-log 关闭路径）
4. 门④ 使用驱动休眠：窗口内有命中 → 免于降级；命中过旧 → 照常降级
5. revive：dormant 原地激活；archived 移回 detail 激活；账本留痕

隔离：全部写临时目录（模块级路径属性 patch）。运行: python test_p05_demand.py
"""
from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import eco_review as er
import memory_query as mq
from lib.memstore import parse_frontmatter_strict

TODAY = datetime.date.today()


def _entry_md(slug: str, body: str, status: str = "active", mtype: str = "semantic",
              last_hit: str | None = None, use_count: int | None = None,
              last_verified: str = "2026-09-01") -> str:
    lines = ["---", f"name: {slug}", f"type: {mtype}", f"status: {status}",
             "occurrences: 3", "session_count: 2",
             f"last_verified: {last_verified}", "last_seen: 2026-09-01"]
    if last_hit:
        lines.append(f"last_hit: {last_hit}")
    if use_count is not None:
        lines.append(f"use_count: {use_count}")
    lines += ["---", "", body, ""]
    return "\n".join(lines)


class MemoryQueryBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.detail = self.root / "detail"
        self.archive = self.root / "archive"
        self.detail.mkdir(parents=True)
        self.archive.mkdir(parents=True)
        self.hits_log = self.root / ".memory_hits.jsonl"
        self.gate_log = self.root / "gate_log"
        self.db = self.root / "eco.db"

    def tearDown(self):
        self._td.cleanup()

    def _write(self, dirpath: Path, slug: str, text: str) -> Path:
        p = dirpath / f"{slug}.md"
        p.write_text(text, encoding="utf-8")
        return p


class TestScoring(MemoryQueryBase):
    def test_name_hit_beats_body_hit(self):
        self._write(self.detail, "缓存策略要点", _entry_md("缓存策略要点", "正文中提到部署相关内容"))
        self._write(self.detail, "部署脚本清单", _entry_md("部署脚本清单", "正文中顺带提到缓存策略四个字"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        scored = [(mq.score_entry(["缓存"], e), e) for e in entries]
        scored = [(s, e) for s, e in scored if s is not None]
        scored.sort(key=lambda t: (-t[0], t[1]["slug"]))
        self.assertIn("缓存策略要点", scored[0][1]["slug"])

    def test_active_beats_dormant(self):
        self._write(self.detail, "条目甲", _entry_md("条目甲", "相同正文关键词", status="dormant"))
        self._write(self.detail, "条目乙", _entry_md("条目乙", "相同正文关键词", status="active"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        scored = sorted([(mq.score_entry(["关键词"], e), e["slug"]) for e in entries],
                        key=lambda x: -x[0])
        self.assertEqual(scored[0][1], "条目乙")

    def test_archive_downgraded(self):
        self._write(self.detail, "条目甲", _entry_md("条目甲", "唯一关键词正文"))
        self._write(self.archive, "条目乙", _entry_md("条目乙", "唯一关键词正文", status="archived"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        self.assertEqual(len(entries), 2)
        s_detail = mq.score_entry(["唯一关键词正文"], next(e for e in entries if e["source"] == "detail"))
        s_archive = mq.score_entry(["唯一关键词正文"], next(e for e in entries if e["source"] == "archive"))
        self.assertGreater(s_detail, s_archive)

    def test_multi_term_and_semantics(self):
        self._write(self.detail, "条目甲", _entry_md("条目甲", "包含部署的内容"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        self.assertIsNone(mq.score_entry(["部署", "迁移"], entries[0]))  # 缺一个 term → 不命中
        self.assertIsNotNone(mq.score_entry(["部署", "内容"], entries[0]))


class TestHitWriteback(MemoryQueryBase):
    def test_bump_hit_updates_fields(self):
        p = self._write(self.detail, "目标条目",
                        _entry_md("目标条目", "正文", last_hit="2026-08-01", use_count=2))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        self.assertTrue(mq.bump_hit(entries[0]))
        fields, _ = parse_frontmatter_strict(p.read_text(encoding="utf-8"))
        self.assertEqual(fields["last_hit"], TODAY.isoformat())
        self.assertEqual(fields["use_count"], "3")
        self.assertEqual(p.read_text(encoding="utf-8").count("last_hit:"), 1)

    def test_bump_hit_inserts_missing_lines(self):
        p = self._write(self.detail, "裸条目", _entry_md("裸条目", "正文"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        self.assertTrue(mq.bump_hit(entries[0]))
        fields, _ = parse_frontmatter_strict(p.read_text(encoding="utf-8"))
        self.assertEqual(fields["last_hit"], TODAY.isoformat())
        self.assertEqual(fields["use_count"], "1")

    def test_telemetry_and_proposal(self):
        self._write(self.detail, "休眠条目", _entry_md("休眠条目", "正文", status="dormant"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        hits = [{"score": 1, **e} for e in entries]
        with mock.patch.object(mq, "HITS_LOG", self.hits_log), \
             mock.patch.object(mq, "GATE_LOG_DIR", self.gate_log):
            mq.log_hits("测试词", hits)
            proposed = mq.propose_revive(hits)
        self.assertTrue(self.hits_log.exists())
        rec = json.loads(self.hits_log.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(rec["query"], "测试词")
        self.assertEqual(rec["hits"], ["休眠条目"])
        self.assertEqual(proposed, 1)
        rf = self.gate_log / f"revive-{TODAY.isoformat()}.md"
        self.assertTrue(rf.exists())
        self.assertIn("休眠条目", rf.read_text(encoding="utf-8"))
        # 重复提案去重
        with mock.patch.object(mq, "GATE_LOG_DIR", self.gate_log):
            self.assertEqual(mq.propose_revive(hits), 0)


class TestUseDrivenDormancy(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.detail = Path(self._td.name) / "detail"
        self.detail.mkdir(parents=True)
        self.archive = Path(self._td.name) / "archive"
        self.archive.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def _scan(self) -> list[dict]:
        """只取**降级/归档**动作（v3.2.3 起 actions 也含"只刷新复核戳"的条目）。

        历史上 actions 等于"要改状态的条目"，语义单一；v3.2.3 让"只推进
        last_verified"的条目也进来（否则刷新根本不会落盘）。于是这个 helper 把
        "要改状态"的口径显式写出来，老用例的意图（不降级）保持不变。
        """
        actions, files, failures = er.scan_detail(self.detail)
        return [a for a in actions if a["action"]]

    def test_recent_hit_exempts_from_dormancy(self):
        recent = (TODAY - datetime.timedelta(days=2)).isoformat()
        (self.detail / "被使用的旧条目.md").write_text(
            _entry_md("被使用的旧条目", "正文", last_verified="2026-01-01", last_hit=recent),
            encoding="utf-8")
        actions = self._scan()
        self.assertEqual(actions, [], "窗口内有命中 → 不降级")

    def test_recent_hit_advances_last_verified(self):
        """v3.2.3（2026-10-29）：窗口内被使用 → `last_verified` 必须前进。

        这条是「门④ last_verified 从不刷新」的回归锁。旧实现只用 last_hit 把条目
        **这一轮**挪出超期名单，复核戳纹丝不动，于是用户一旦停用超过阈值天数，
        条目立刻按"几个月没核验"被降级——恰好违背"最活跃的不先归档"这个核心承诺。

        断言必须落在**写盘后的文件内容**上，不能只断言 actions：只挪名单的那版
        同样返回 []，从 actions 根本看不出区别。
        """
        recent = (TODAY - datetime.timedelta(days=2)).isoformat()
        p = self.detail / "被使用且复核戳该前进.md"
        p.write_text(
            _entry_md("被使用且复核戳该前进", "正文", last_verified="2026-01-01", last_hit=recent),
            encoding="utf-8")
        actions, _, _ = er.scan_detail(self.detail)
        self.assertEqual([a for a in actions if a["action"]], [], "窗口内有命中 → 不降级")
        self.assertTrue(any(a["refresh_lv"] for a in actions), "窗口内被使用却没标 refresh_lv")
        er.apply_detail_actions(actions, self.archive, dry_run=False)
        fields, _ = parse_frontmatter_strict(p.read_text(encoding="utf-8"))
        self.assertEqual(fields["last_verified"], TODAY.isoformat(),
                         "窗口内被使用却没推进 last_verified —— 复核戳与'最近被用到'脱节")

    def test_refresh_lv_is_recorded_on_the_record(self):
        """刷新意图要能被上层数出来（报告口径），否则等于静默行为。"""
        recent = (TODAY - datetime.timedelta(days=2)).isoformat()
        (self.detail / "需刷新条目.md").write_text(
            _entry_md("需刷新条目", "正文", last_verified="2026-01-01", last_hit=recent),
            encoding="utf-8")
        (self.detail / "今天已核验.md").write_text(
            _entry_md("今天已核验", "正文", last_verified=TODAY.isoformat(), last_hit=recent),
            encoding="utf-8")
        _, files, _ = er.scan_detail(self.detail)
        flags = {f["slug"]: f["refresh_lv"] for f in files}
        self.assertTrue(flags["需刷新条目"], "复核戳是旧的 → 应标 refresh_lv")
        self.assertFalse(flags["今天已核验"], "已是今天 → 不该重复写盘（幂等）")

    def test_refresh_last_verified_helper_edges(self):
        """`refresh_last_verified` 的四个易错形态（与 rewrite_status 同口径）。"""
        today = TODAY.isoformat()
        # ① 幂等：已是今天 → changed=False，内容不变
        raw = "---\nname: a\nlast_verified: %s\n---\nb\n" % today
        out, changed = er.refresh_last_verified(raw)
        self.assertFalse(changed)
        self.assertEqual(out, raw)
        # ② 缺字段 → 补在首个 --- 之后
        out, changed = er.refresh_last_verified("---\nname: b\nstatus: active\n---\nb\n")
        self.assertTrue(changed)
        self.assertIn("last_verified: %s" % today, out)
        # ③ 键名带空格仍要命中（P0-7 同类坑）
        out, changed = er.refresh_last_verified(
            "---\nname: c\nlast_verified : 2026-01-01\n---\nb\n")
        self.assertTrue(changed)
        self.assertIn("last_verified: %s" % today, out)
        # ④ 正文里的 --- 之后的 last_verified 绝不能被改
        raw = ("---\nname: d\nlast_verified: 2026-01-01\n---\n---\n"
               "last_verified: 1999-01-01\n")
        out, changed = er.refresh_last_verified(raw)
        self.assertTrue(changed)
        self.assertIn("last_verified: 1999-01-01", out, "正文里的伪字段被误改了")
        # ⑤ BOM 必须原样保留
        out, _ = er.refresh_last_verified("\ufeff---\nname: e\nlast_verified: 2026-01-01\n---\n")
        self.assertTrue(out.startswith("\ufeff"), "BOM 被吞了")

    def test_old_hit_still_expires(self):
        old = (TODAY - datetime.timedelta(days=200)).isoformat()
        (self.detail / "从未再被使用的条目.md").write_text(
            _entry_md("从未再被使用的条目", "正文", last_verified="2026-01-01", last_hit=old),
            encoding="utf-8")
        actions = self._scan()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"][0], "mark_dormant")

    def test_no_hit_field_keeps_calendar_rule(self):
        (self.detail / "无命中字段条目.md").write_text(
            _entry_md("无命中字段条目", "正文", last_verified="2026-01-01"),
            encoding="utf-8")
        actions = self._scan()
        self.assertEqual(len(actions), 1)


class TestRevive(MemoryQueryBase):
    def test_revive_dormant_in_place(self):
        p = self._write(self.detail, "休眠待复活", _entry_md("休眠待复活", "正文", status="dormant"))
        ok, msg = mq.revive("休眠待复活", self.detail, self.archive, self.db)
        self.assertTrue(ok, msg)
        fields, _ = parse_frontmatter_strict(p.read_text(encoding="utf-8"))
        self.assertEqual(fields["status"], "active")
        self.assertEqual(fields["last_verified"], TODAY.isoformat())

    def test_revive_archived_moves_back(self):
        self._write(self.archive, "归档待复活", _entry_md("归档待复活", "正文", status="archived"))
        ok, msg = mq.revive("归档待复活", self.detail, self.archive, self.db)
        self.assertTrue(ok, msg)
        self.assertFalse(list(self.archive.rglob("归档待复活*.md")))
        restored = list(self.detail.glob("归档待复活*.md"))
        self.assertEqual(len(restored), 1)
        fields, _ = parse_frontmatter_strict(restored[0].read_text(encoding="utf-8"))
        self.assertEqual(fields["status"], "active")

    def test_revive_missing_slug_fails_cleanly(self):
        ok, msg = mq.revive("不存在的条目", self.detail, self.archive, self.db)
        self.assertFalse(ok)

    def test_revive_logs_to_ledger(self):
        import sqlite3
        self._write(self.detail, "留痕条目", _entry_md("留痕条目", "正文", status="dormant"))
        mq.revive("留痕条目", self.detail, self.archive, self.db)
        conn = sqlite3.connect(str(self.db))
        try:
            row = conn.execute(
                "SELECT action, slug FROM review_log WHERE action='revive' AND slug='留痕条目'").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)


class TestDisputeConsumer(MemoryQueryBase):
    """v3.2.3（2026-10-29）销「disputed_by 无消费方」。

    债的形态：write_gate 的 DISPUTE 路径只写 `disputed_by`/`disputed_at`/
    `disputed_reason` 三个字段，**没有任何读方** —— 冲突被检测出来就悬在那里，
    两条矛盾的记忆同时留在库里、都还能被检索到。本组锁定刚补上的消费方。
    """

    def _disputed(self, slug: str, by: str = "新值条目", when: str = "2026-10-01"):
        fm = _entry_md(slug, "旧的说法", status="active")
        fm = fm.replace("---\n\n旧的说法", "")
        lines = ["---", f"name: {slug}", "type: semantic", "status: active",
                 "occurrences: 3", "session_count: 2",
                 "last_verified: 2026-09-01", "last_seen: 2026-09-01",
                 f"disputed_by: {by}", f"disputed_at: {when}",
                 "disputed_reason: 同槽位不同值", "---", "", "旧的说法", ""]
        return self._write(self.detail, slug, "\n".join(lines))

    def test_list_disputes_finds_marks(self):
        self._disputed("备份保留七天")
        self._write(self.detail, "无关条目", _entry_md("无关条目", "正文"))
        rows = mq.list_disputes(self.detail)
        self.assertEqual(len(rows), 1, "带 disputed_by 的条目没被列出")
        self.assertEqual(rows[0]["slug"], "备份保留七天")
        self.assertEqual(rows[0]["disputed_by"], "新值条目")
        self.assertEqual(rows[0]["disputed_reason"], "同槽位不同值")

    def test_list_disputes_empty_is_not_an_error(self):
        self._write(self.detail, "干净条目", _entry_md("干净条目", "正文"))
        self.assertEqual(mq.list_disputes(self.detail), [])

    def test_resolve_kept_clears_marks_but_keeps_content(self):
        p = self._disputed("备份保留七天")
        ok, msg = mq.resolve_dispute("备份保留七天", keep=True, reason="人工确认本条正确",
                                     detail_dir=self.detail, db=self.db)
        self.assertTrue(ok, msg)
        fields, body = parse_frontmatter_strict(p.read_text(encoding="utf-8"))
        for k in ("disputed_by", "disputed_at", "disputed_reason"):
            self.assertNotIn(k, fields, f"{k} 没被清掉 —— 争议仍会一直悬着")
        self.assertEqual(fields["dispute_verdict"], "kept")
        self.assertEqual(fields["status"], "active", "保留判定不该改动 status")
        self.assertIn("旧的说法", body, "终审不得丢内容（宁可重复，不可丢内容）")

    def test_resolve_superseded_sends_to_dormant_without_deleting(self):
        p = self._disputed("备份保留七天")
        ok, msg = mq.resolve_dispute("备份保留七天", keep=False,
                                     detail_dir=self.detail, db=self.db)
        self.assertTrue(ok, msg)
        fields, body = parse_frontmatter_strict(p.read_text(encoding="utf-8"))
        self.assertEqual(fields["status"], "dormant")
        self.assertEqual(fields["fate"], "superseded")
        self.assertEqual(fields["superseded_by"], "新值条目")
        self.assertTrue(p.exists(), "终审也不该物理删除条目")
        self.assertIn("旧的说法", body)

    def test_resolve_writes_ledger(self):
        import sqlite3
        self._disputed("有争议的条目")
        mq.resolve_dispute("有争议的条目", keep=True, detail_dir=self.detail, db=self.db)
        conn = sqlite3.connect(str(self.db))
        try:
            row = conn.execute(
                "SELECT action, slug FROM review_log "
                "WHERE action='dispute-resolve' AND slug='有争议的条目'").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, "终审没有留痕 —— 争议了结必须可审计")

    def test_refresh_entries_do_not_crash_report_and_log(self):
        """v3.2.3 回归锁：`detail_actions` 混入"无 action"条目后，
        `build_report()` 与账本循环原来直接 `act, reason = r['action']` → TypeError。

        独立审查抓到的正是这里：单元层面只测 `apply_detail_actions` 全绿，
        但真实跑 `eco_review.py` 会在**生成报告**那一步崩。所以这条必须同时走
        `build_report` 与账本路径，而不是只看 actions。
        """
        recent = (TODAY - datetime.timedelta(days=2)).isoformat()
        self.detail.mkdir(parents=True, exist_ok=True)
        (self.detail / "被使用条目.md").write_text(
            _entry_md("被使用条目", "正文", last_verified="2026-01-01", last_hit=recent),
            encoding="utf-8")
        actions, files, _ = er.scan_detail(self.detail)
        self.assertTrue(any(a["refresh_lv"] and not a["action"] for a in actions),
                        "前提不成立：这里应当有一条'只刷新'的条目")
        # 崩点 1：报告生成
        report = er.build_report(TODAY.isoformat(), actions, [], [], files, 0)
        self.assertIn("refresh_lv", report, "报告里没有把刷新动作标出来")
        # 崩点 2：账本条目构造（_run 里的循环，抽成同样形态验证）
        entries = []
        for r in actions:
            if not r["action"]:
                act, reason = "refresh_lv", "x"
            else:
                act, reason = r["action"]
            entries.append((r["slug"], act))
        self.assertIn(("被使用条目", "refresh_lv"), entries)

    def test_resolve_on_clean_entry_is_refused(self):
        self._write(self.detail, "干净条目", _entry_md("干净条目", "正文"))
        ok, msg = mq.resolve_dispute("干净条目", detail_dir=self.detail, db=self.db)
        self.assertFalse(ok, "没有争议标记却报成功 = 静默假成功")
        self.assertIn("无需处理", msg)

    def test_resolve_refuses_path_traversal(self):
        """`--resolve ../evil` 曾能改写 detail/ 之外的任意 .md（实测确认）。

        `slug` 被直接拼进 `detail_dir / f"{slug}.md"`；只要越界目标文件真实存在，
        `../` 就生效。这条用例把"越界必须被拒"钉死——注意要让目录外文件**真的
        带 disputed_by 标记**，否则旧代码会因为"没有标记"而拒绝，测试会假绿。
        """
        outside = self.root / "outside.md"
        outside.write_text(
            "---\nname: evil\nstatus: active\ndisputed_by: x\n"
            "disputed_at: 2026-10-01\n---\n危险内容\n", encoding="utf-8")
        before = outside.read_text(encoding="utf-8")
        for bad in ("../outside", "..\\outside", "a/../../outside"):
            ok, _msg = mq.resolve_dispute(bad, detail_dir=self.detail, db=self.db)
            self.assertFalse(ok, "%r 竟然被接受了" % bad)
        self.assertEqual(outside.read_text(encoding="utf-8"), before,
                         "detail/ 之外的文件被改写了 —— 路径穿越")


if __name__ == "__main__":
    unittest.main(verbosity=2)
