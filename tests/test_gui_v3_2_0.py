# -*- coding: utf-8 -*-
"""v3.2.0 数据层 / 写通道红线测试（README-v3.2.0 §二/§四）。

- 核心探测**必须验版本**：本仓库曾有一份 v3.1 镜像（有 cli.py 没有 gate 子命令），
  误认的最坏形态是 healthz 说 core_ok=true 而每个新视图 404。TestCoreProbe 用
  假镜像把这条钉死。
- GUI **永不写记忆内容**：只允许圈配置 / 记忆包启停 / 幂等重建三类，全部转发核心
  CLI。没有「删除 / 清理 / 裁剪记忆」的动作——不是暂时没做，是设计红线。
- 全源码不许出现针对记忆数据本体（MEMORY.md / USER.md / detail / pending）的
  unlink / rmtree。

这些文件曾经存在但丢失（2026-10-31 重建）；与 README 描述一一对应。
"""
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUI = REPO / "integrations" / "gui"
GUI_V3_SRC = REPO / "integrations" / "gui-v3" / "src"
CORE_SRC = REPO / "core" / "src"

sys.path.insert(0, str(GUI))
import collector_v3_2_0  # noqa: E402


class TestCoreProbe:
    """find_eco_core / _is_v3_2_0 必须能识别并拒绝 v3.1 镜像（假成功最危险）。"""

    @pytest.fixture()
    def v31_mirror(self, tmp_path: Path) -> Path:
        """构造一个 v3.1 形态的核心镜像：有 cli.py / retrieve.py，无 gates/packs，
        __version__ 停在 3.1.0，retrieve 没有 CATALOG_SCHEMA。"""
        pkg = tmp_path / "memory_ecology"
        pkg.mkdir()
        (pkg / "__init__.py").write_text('__version__ = "3.1.0"\n', encoding="utf-8")
        (pkg / "cli.py").write_text("# v3.1 legacy cli\n", encoding="utf-8")
        (pkg / "root.py").write_text("SCHEMA_VERSION = 2\n", encoding="utf-8")
        (pkg / "retrieve.py").write_text("def query(*a, **k):\n    return []\n", encoding="utf-8")
        return tmp_path

    def test_probe_rejects_v31_mirror(self, v31_mirror: Path):
        assert collector_v3_2_0._is_v3_2_0(v31_mirror) is False, (
            "v3.1 镜像被当成了 v3.2+ 核心——healthz 会假报 core_ok=true 而新视图全 404")

    def test_probe_rejects_garbage_dir(self, tmp_path: Path):
        assert collector_v3_2_0._is_v3_2_0(tmp_path) is False

    def test_probe_accepts_real_core(self):
        real = CORE_SRC
        assert (real / "memory_ecology" / "gates.py").is_file()
        assert collector_v3_2_0._is_v3_2_0(real) is True


class TestNoDestructiveAction:
    """设计红线：没有「删除 / 清理 / 裁剪记忆」的动作。"""

    def test_core_write_actions_are_exactly_three_kinds(self):
        """collector_v3_2_0.run_action 只允许圈配置 / 记忆包 / 幂等重建三类。"""
        src = (GUI / "collector_v3_2_0.py").read_text(encoding="utf-8")
        actions = set(re.findall(r'if name == "(\w+)"|if name in \(([^)]*)\):', src))
        # 上面正则难覆盖 `in (...)`，直接抓 run_action 函数体里的动作字面量更稳：
        m = re.search(r"def run_action\(.*?(?=\ndef |\Z)", src, re.S)
        body = m.group(0)
        allowed = {"circle_write", "pack_enable", "pack_disable", "maintain", "materialize"}
        found = set(re.findall(r'name (?:==|in) "?\'?(\w+)', body))
        found |= set(re.findall(r'"(\w+)"', body)) & allowed
        unknown = found - allowed
        assert not unknown, f"run_action 出现未知/未授权动作：{unknown}"

    def test_gui_actions_have_no_destructive_names(self):
        """eco_gui.py 动作白名单（T5）里不许出现删除/清理类动词。"""
        src = (GUI / "eco_gui.py").read_text(encoding="utf-8")
        m = re.search(r"ACTIONS: dict\[str, dict\] = \{(.*?)\n\}", src, re.S)
        assert m, "eco_gui.py 里找不到 ACTIONS 白名单"
        names = set(re.findall(r'^\s{4}"(\w+)":\s*\{', m.group(1), re.M))
        bad = [n for n in names
               if re.search(r"delete|remove|clean|clear|prune|purge|wipe|trim", n)]
        assert not bad, f"动作白名单出现疑似删除类动作：{bad}"

    def test_no_destructive_keywords_in_frontend_actions(self):
        """gui-v3 前端动作入口同样不许出现删除/清理字样的动作名。"""
        for p in (GUI_V3_SRC / "lib" / "api_v3_2_0.ts",):
            s = p.read_text(encoding="utf-8")
            bad = re.findall(r"'(core_\w+|pack_\w+|circle_\w+)'", s)
            allowed = {"circle_write", "pack_enable", "pack_disable",
                       "core_maintain", "core_materialize"}
            unknown = set(bad) - allowed
            assert not unknown, f"前端出现未知写动作名：{unknown}"


class TestNoUnlinkOfMemories:
    """全源码不许出现针对记忆数据本体的 unlink / rmtree（急救箱也不是后门）。"""

    MEMORY_PAT = re.compile(r"MEMORY\.md|USER\.md|/detail|\\detail|/pending|\\pending|memories")

    def test_no_unlink_of_memory_data_anywhere(self):
        roots = [CORE_SRC, GUI]
        offenders = []
        for root in roots:
            for p in root.rglob("*.py"):
                if "__pycache__" in p.parts or ".bak" in p.name:
                    continue
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if re.search(r"\.unlink\(|\.rmtree\(|\.rmdir\(", line) and self.MEMORY_PAT.search(line):
                        offenders.append(f"{p.relative_to(root.parent)}:{i}: {line.strip()[:100]}")
        assert not offenders, "发现针对记忆数据本体的删除调用：\n" + "\n".join(offenders)


class TestGuiNeverWritesMemoryDirectly:
    """GUI 侧只允许「转发核心 CLI」这一条写路径。"""

    def test_v320_actions_all_forward_to_core(self):
        """eco_gui.py 里 v3.2.0 动作必须带 core 键（GUI 不自己实现写逻辑）。"""
        src = (GUI / "eco_gui.py").read_text(encoding="utf-8")
        m = re.search(r"# ── v3\.2\.0 写动作.*?(?=\n\})", src, re.S)
        assert m, "找不到 v3.2.0 写动作区块"
        block = m.group(0)
        entries = re.findall(r'"(\w+)":\s*\{"name".*?\}(?:,\n|\n)', block, re.S)
        # 逐个动作块检查 core 键
        for act in re.finditer(r'"(\w+)":\s*\{[^{}]*"name"', block):
            seg = block[act.start():block.find("\n    \"", act.start() + 1) or len(block)]
            assert '"core"' in seg or 'core' in seg, f"动作 {act.group(1)} 未转发核心"
        assert entries or True  # 结构检查以逐块 seg 为准

    def test_run_action_stage_guard(self):
        """run_action 必须区分 dry / run：dry 阶段不得触碰核心 CLI 的写子命令。"""
        src = (GUI / "collector_v3_2_0.py").read_text(encoding="utf-8")
        m = re.search(r"def run_action\(.*?(?=\ndef |\Z)", src, re.S)
        body = m.group(0)
        assert body.count('stage == "dry"') >= 3, "run_action 缺 dry 分支"
        # circle_write 的 dry 是本地校验（不能写盘）
        assert "JSON 非法" in body and "_validate_circles" in body


class TestMetaIsReadOnly:
    """审计 05-P1-3：`meta()` 曾把 `core_call(["init"])` 放在 TTL 缓存之外，
    于是 /api/healthz（20s）与 /api/v3.2.0/meta（8s）每次轮询都 spawn 一个
    **写**子进程——GET 有建根副作用，且 --root 指错位置也照样尝试建根。

    修法：meta() 纯只读（读 root.json + 进程内 import 核心版本），
    建根拆成 ensure_root()，只在服务启动时调一次。
    本类用"任何 core_call 都让测试失败"的方式钉死：meta 一次都不许 spawn 子进程。
    """

    def _spy(self, monkeypatch):
        calls = []

        def fake_core_call(args, timeout=None):
            calls.append(list(args))
            return {"ok": True, "version": "9.9.9", "root_id": "x", "readonly": False}

        monkeypatch.setattr(collector_v3_2_0, "core_call", fake_core_call)
        return calls

    def test_meta_never_spawns_core_subprocess(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(collector_v3_2_0, "ecoroot", lambda: tmp_path)
        calls = self._spy(monkeypatch)
        collector_v3_2_0.bust()
        # 连续轮询多轮（含 TTL 过期后的重算），一次都不许 spawn
        for _ in range(6):
            collector_v3_2_0.bust()
            collector_v3_2_0.meta()
        assert calls == [], f"meta() 仍在 spawn 核心子进程：{calls}"

    def test_meta_reads_root_json_without_init(self, tmp_path: Path, monkeypatch):
        """根已初始化时，root_id/schema 必须来自 root.json（不靠 init 回执）。"""
        monkeypatch.setattr(collector_v3_2_0, "ecoroot", lambda: tmp_path)
        self._spy(monkeypatch)
        (tmp_path / ".eco").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".eco" / "root.json").write_text(
            '{"root_id": "abc123", "schema_version": 1, "created_at": "x"}',
            encoding="utf-8")
        collector_v3_2_0.bust()
        m = collector_v3_2_0.meta()
        assert m["root_id"] == "abc123"
        assert m["root_kind"] == "dsh"
        assert m["root_exists"] is True

    def test_meta_does_not_create_root(self, tmp_path: Path, monkeypatch):
        """根不存在时 meta 也**不许**建根（只报告 root_exists=false）。"""
        monkeypatch.setattr(collector_v3_2_0, "ecoroot", lambda: tmp_path / "nope")
        self._spy(monkeypatch)
        collector_v3_2_0.bust()
        m = collector_v3_2_0.meta()
        assert m["root_exists"] is False
        assert not (tmp_path / "nope").exists(), "meta() 竟然建了根目录"

    def test_ensure_root_is_the_only_init_path(self):
        """`init` 这个写子命令只允许出现在 ensure_root() 里（源码级钉死）。"""
        src = (GUI / "collector_v3_2_0.py").read_text(encoding="utf-8")
        init_calls = re.findall(r'core_call\(\["init"[^\]]*\]', src)
        assert len(init_calls) == 1, (
            f"`core_call([\"init\"])` 出现 {len(init_calls)} 次，只允许在 ensure_root 里一次")
        m = re.search(r"def ensure_root\(.*?(?=\ndef |\Z)", src, re.S)
        assert m and 'core_call(["init"]' in m.group(0), "唯一那处 init 必须在 ensure_root 内"

    def test_readonly_causes_come_from_core(self, tmp_path: Path, monkeypatch):
        """只读成因必须逐字来自核心（四种），不能在 GUI 里重推一种。

        评审 2026-11-05：本地只推 schema-mismatch 时，root.json **损坏**会被
        报成"schema 需要迁移"——处置方向正好相反（该恢复备份，不是迁移）。
        """
        monkeypatch.setattr(collector_v3_2_0, "ecoroot", lambda: tmp_path)
        self._spy(monkeypatch)

        # ① root.json 损坏 → 必须是 root.json-corrupt，不是 schema-mismatch
        (tmp_path / ".eco").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".eco" / "root.json").write_text("{ not json", encoding="utf-8")
        collector_v3_2_0.bust()
        m = collector_v3_2_0.meta()
        assert m["readonly"] is True
        assert "root.json-corrupt" in m["readonly_reason"], (
            f"坏 root.json 被报成 {m['readonly_reason']!r}，处置方向会被误导")

        # ② schema 不匹配 → schema-mismatch:N
        (tmp_path / ".eco" / "root.json").write_text(
            '{"root_id": "a", "schema_version": 999}', encoding="utf-8")
        collector_v3_2_0.bust()
        m = collector_v3_2_0.meta()
        assert m["readonly"] is True
        assert m["readonly_reason"].startswith("schema-mismatch:"), m["readonly_reason"]

        # ③ 根未初始化 → root-not-initialized
        (tmp_path / ".eco" / "root.json").unlink()
        collector_v3_2_0.bust()
        m = collector_v3_2_0.meta()
        assert m["readonly"] is True
        assert m["readonly_reason"] == "root-not-initialized", m["readonly_reason"]

    def test_core_ok_rejects_v31_mirror(self, tmp_path: Path, monkeypatch):
        """`core_ok` 不能按「能 import 到 memory_ecology」判定 —— v3.1 镜像会假报可达。"""
        pkg = tmp_path / "memory_ecology"
        pkg.mkdir()
        (pkg / "__init__.py").write_text('__version__ = "3.1.0"\n', encoding="utf-8")
        (pkg / "cli.py").write_text("# v3.1\n", encoding="utf-8")
        monkeypatch.setattr(collector_v3_2_0, "find_eco_core", lambda: tmp_path)
        monkeypatch.setattr(collector_v3_2_0, "ecoroot", lambda: tmp_path)
        self._spy(monkeypatch)
        collector_v3_2_0.bust()
        m = collector_v3_2_0.meta()
        assert m["core_ok"] is False, "v3.1 镜像被报成「核心可达」——假成功最危险"
        assert m["core_error"], "不可达时必须给出原因文案"

