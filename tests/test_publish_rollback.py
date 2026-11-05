#!/usr/bin/env python3
"""publish_compat 失败清场回归（3.4.0 审计 05-P1-2）。

背景：`main()` 里 `except SystemExit: raise` 直接重抛，绕过了回滚分支。而
`build()` 内部的 SystemExit 有三处发生在**已经写入几百个文件之后**：
core 源树缺失（:467）/ 版本不一致（:498）/ README 占位符未渲染（:517）。
半新半旧的发布树正是回滚注释自己预言的形态——「没看退出码就 add -A && push」
就把半成品带出去了。

同时钉住反向约束：同一函数开头还有两道**拒绝型** SystemExit（--out 指向开发树、
指向无 .git 的陌生目录）。那两种情况下发布树原封未动，清场反而会去删"刚刚
才拒绝清空的陌生目录"——所以清场必须以 `_PUBLISH_TOUCHED` 为闸。

隔离：只用 tmp_path 假发布树，绝不碰真发布树（DEFAULT_OUT）。
运行：python -m pytest tests/test_publish_rollback.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "publish_compat.py"


def _load_tool():
    """按文件路径加载 publish_compat（tools/ 不是包，也没有 __init__.py）。"""
    spec = importlib.util.spec_from_file_location("publish_compat_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def pc():
    mod = _load_tool()
    # 老版本没有 _PUBLISH_TOUCHED（那正是缺陷本身）；补一个 False 让"清场"用例
    # 走到真正的断言上，而不是靠 AttributeError 假装失败——否则测不出缺陷。
    old = getattr(mod, "_PUBLISH_TOUCHED", False)
    mod._PUBLISH_TOUCHED = False
    yield mod
    mod._PUBLISH_TOUCHED = old


def _mk_publish_tree(root: Path) -> Path:
    """造一个已存在的发布树（带 .git）+ 几个"半更新"文件。"""
    out = root / "release"
    (out / ".git").mkdir(parents=True)
    (out / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (out / "src" / "memory_ecology").mkdir(parents=True)
    (out / "src" / "memory_ecology" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (out / "README.md").write_text("half-new half-old\n", encoding="utf-8")
    return out


def test_systemexit_after_touch_rolls_back(pc, tmp_path, monkeypatch):
    """build 动过树之后抛 SystemExit → 必须清场（保 .git），再把退出码传出去。"""
    out = _mk_publish_tree(tmp_path)

    def boom(_out):
        pc._PUBLISH_TOUCHED = True          # 模拟"已写入几百个文件"
        raise SystemExit("❌ 模拟 core 版本不一致")

    monkeypatch.setattr(pc, "build", boom)
    monkeypatch.setattr(sys, "argv", ["publish_compat.py", "--out", str(out)])

    with pytest.raises(SystemExit):
        pc.main()

    assert (out / ".git").is_dir(), ".git 必须保留（开源树含发布历史）"
    leftovers = [p.name for p in out.iterdir() if p.name != ".git"]
    assert leftovers == [], f"SystemExit 留下了半更新文件：{leftovers}"


def test_systemexit_before_touch_does_not_wipe_stranger_dir(pc, tmp_path, monkeypatch):
    """拒绝型 SystemExit（还没动过树）绝不能清场——否则删了刚拒绝清空的陌生目录。"""
    out = tmp_path / "stranger"
    out.mkdir()
    (out / "important.txt").write_text("用户的东西\n", encoding="utf-8")

    def refuse(_out):
        # _PUBLISH_TOUCHED 保持 False：等价于 build 开头的两道守卫直接 raise
        raise SystemExit("❌ --out 指向已存在的非发布目录（无 .git），拒绝清空陌生目录")

    monkeypatch.setattr(pc, "build", refuse)
    monkeypatch.setattr(sys, "argv", ["publish_compat.py", "--out", str(out)])

    with pytest.raises(SystemExit):
        pc.main()

    assert (out / "important.txt").is_file(), "拒绝型退出竟然清空了陌生目录"
    assert (out / "important.txt").read_text(encoding="utf-8") == "用户的东西\n"


def test_rollback_keeps_git_and_clears_rest(pc, tmp_path):
    """_rollback_publish 本身的契约。"""
    out = _mk_publish_tree(tmp_path)
    assert pc._rollback_publish(out) is True
    assert [p.name for p in out.iterdir()] == [".git"]
    # 不存在的目录：返回 False，不抛
    assert pc._rollback_publish(tmp_path / "nope") is False


def test_build_guard_still_refuses_stranger_dir(pc, tmp_path):
    """真实 build() 的守卫仍生效（防本次改动把守卫一起改坏）。"""
    stranger = tmp_path / "nonrepo"
    stranger.mkdir()
    (stranger / "keep.txt").write_text("keep\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        pc.build(stranger)
    assert (stranger / "keep.txt").is_file(), "build 守卫失效：陌生目录被清空"


def test_wipe_loop_failure_midway_still_rolls_back(pc, tmp_path, monkeypatch):
    """评审抓到的窗口：清空循环中途失败（Windows 上文件被占用就够）时，
    树已经被删掉一部分 —— 旗标必须在进入破坏性区域**之前**就置真，
    否则 main() 的回滚闸门不成立，半清空的树会留在原地且无任何提示。
    """
    out = _mk_publish_tree(tmp_path)
    # 再加一个顶层文件，让它成为循环里"抛错的那一个"
    (out / "vendor").mkdir()
    (out / "vendor" / "lib.js").write_text("x\n", encoding="utf-8")

    real_unlink = Path.unlink

    def flaky_unlink(self, *a, **kw):
        if self.name == "README.md":
            raise OSError("文件被占用（模拟编辑器/杀软锁）")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    monkeypatch.setattr(sys, "argv", ["publish_compat.py", "--out", str(out)])

    with pytest.raises((SystemExit, OSError)):
        pc.build(out)
    # build 直接抛时 main 没参与；这里直接验旗标语义：中途失败后旗标必须已为真
    assert pc._PUBLISH_TOUCHED is True, (
        "清空循环中途失败时旗标仍为 False —— main() 的回滚闸门不成立")
    monkeypatch.setattr(Path, "unlink", real_unlink)
    # 收尾：真的清一次，别把半清空的树留给后续用例
    pc._rollback_publish(out)


def test_build_resets_flag_between_runs(pc, tmp_path):
    """同进程重复调用：上一轮的真值不能残留，否则拒绝型 SystemExit 会误删陌生目录。"""
    out = _mk_publish_tree(tmp_path)
    pc._PUBLISH_TOUCHED = True          # 模拟上一轮留下的真值
    stranger = tmp_path / "stranger2"
    stranger.mkdir()
    (stranger / "keep.txt").write_text("keep\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        pc.build(stranger)
    assert (stranger / "keep.txt").is_file(), "旗标残留导致拒绝型退出也清了陌生目录"


def test_flag_set_before_destructive_wipe(pc, tmp_path, monkeypatch):
    """源码级钉死：置位语句必须出现在清空循环**之前**。

    这是评审指出的那个窗口的静态表达——行为用例（wipe_loop_failure）依赖
    具体哪一行抛错，容易被重构绕过；这条直接看顺序。
    """
    import re
    src = TOOL.read_text(encoding="utf-8")
    m = re.search(r"def build\(.*?(?=\ndef |\Z)", src, re.S)
    body = m.group(0)
    set_pos = body.index("_PUBLISH_TOUCHED = True")
    loop_pos = body.index("for child in list(out.iterdir()):")
    assert set_pos < loop_pos, (
        "置位语句排在清空循环之后 —— 循环中途失败时回滚闸门不成立（评审抓到的窗口）")
