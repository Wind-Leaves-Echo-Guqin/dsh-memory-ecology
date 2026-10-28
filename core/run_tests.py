#!/usr/bin/env python3
"""run_tests.py —— 一条命令跑完全部测试（任一失败 → 退出码非 0）。

分组（与交付物一一对应）：
  1. core         tests/test_core.py          核心：四门/圈/包/数据安全（86 条）
  2. gui          tests/test_gui.py           观测舱：安全/写通道/一致性/零写入（35 条）
  3. plugin       plugin/test_plugin.mjs      插件：目录稳定/fail-open/多圈/渲染一致（21 条）
  4. concurrency  tests/test_concurrency.py   多进程并发只追加
  5. mutation     tests/test_mutation.py      变异测试（证明前几套真的能抓 bug）——默认跑，`--no-mutation` 跳过
  6. compat       tests/test_compat.py        与旧 Hermes 线数据面的兼容/只读挂载

用法：
    python run_tests.py                # 全量
    python run_tests.py --fast         # 跳过变异与并发（改一行代码时的快反馈）
    python run_tests.py -k gui         # 只跑名字含 gui 的组
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
PY = sys.executable


def _bootstrap() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - pythonw/管道下可能没有 reconfigure
            pass


def child_env() -> dict:
    """子进程环境：UTF-8 + **摘掉宿主 MEMORY_ECOLOGY_ROOT**。

    测试全部自带沙盒；宿主的全局根反而可能把子进程指到真实生态根（那是最坏的一种
    "测试通过但写坏了生产数据"）。2026-10-26 起强制摘除。
    """
    env = os.environ.copy()
    env.pop("MEMORY_ECOLOGY_ROOT", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO / "src")
    return env


GROUPS = [
    ("core", [PY, str(REPO / "tests" / "test_core.py")], REPO, 300, True),
    ("plugin", ["node", str(REPO / "plugin" / "test_plugin.mjs")], REPO / "plugin", 300, True),
    ("compat", [PY, str(REPO / "tests" / "test_compat.py")], REPO, 300, True),
    ("regress", [PY, str(REPO / "tests" / "test_regressions.py")], REPO, 300, True),
    ("concurrency", [PY, str(REPO / "tests" / "test_concurrency.py")], REPO, 300, False),
    ("stability", [PY, str(REPO / "tests" / "test_stability.py")], REPO, 900, False),
    ("mutation", [PY, str(REPO / "tests" / "test_mutation.py")], REPO, 1800, False),
]
# 观测舱测试**不在本仓库**：真实 GUI 是 `memory-ecology-兼容版/integrations/gui` + `gui-v2.3`，
# 它的断言在那边 `tests/test_gui_v32.py`（24 条，随那个仓库的 run_tests.py 跑）。
# 这里曾经有一组 `gui` 指向本仓库的 `gui/`（我一度把服务端复制过来，目标错了），
# 那份已归档到 `_archive/gui-wrong-target-v0.4/`，测试组同步移除——
# 留着它只会指向一个不存在的目录并超时（实测 300s 超时）。


def main() -> int:
    _bootstrap()
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过变异与并发（快反馈）")
    ap.add_argument("-k", default="", help="只跑名字含该串的组")
    args = ap.parse_args()
    slow = {"mutation", "concurrency"}
    results = []
    print("=" * 72)
    print("记忆生态 v3.2.0 · 全量测试")
    print("=" * 72)
    for name, cmd, cwd, timeout, always in GROUPS:
        if args.k and args.k not in name:
            continue
        if args.fast and name in slow:
            print("▶ %-12s 跳过（--fast）" % name)
            continue
        path = Path(cmd[1] if cmd[0] == PY else cmd[1])
        if not path.is_file():
            print("▶ %-12s 跳过（文件不存在：%s）" % (name, path.name))
            results.append((name, None, ""))
            continue
        if cmd[0] == "node":
            try:
                subprocess.run(["node", "--version"], capture_output=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                print("▶ %-12s 跳过（没有 node）" % name)
                results.append((name, None, ""))
                continue
        t0 = time.time()
        print("▶ %-12s" % name, end="", flush=True)
        try:
            r = subprocess.run(cmd, cwd=str(cwd), env=child_env(), timeout=timeout,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            dt = time.time() - t0
            ok = r.returncode == 0
            # **失败时把出错的用例名列出来**：只给最后三行会只剩 "FAILED (failures=1)"，
            # 看不出是哪一个——排障时等于没有信息（实测踩到）。
            allout = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            fails = [x.strip() for x in allout
                     if x.lstrip().startswith(("FAIL:", "ERROR:"))]
            tail = fails[:8] if fails else allout[-4:]
            print("\r✅ %-12s %5.1fs" % (name, dt) if ok
                  else "\r❌ %-12s %5.1fs (exit=%s)" % (name, dt, r.returncode))
            if not ok:
                for line in tail:
                    print("     " + line)
            results.append((name, ok, "\n".join(tail)))
        except subprocess.TimeoutExpired:
            print("\r⌛ %-12s 超时（>%ss）" % (name, timeout))
            results.append((name, False, "timeout"))

    print()
    ran = [r for r in results if r[1] is not None]
    bad = [r for r in ran if not r[1]]
    skipped = [r for r in results if r[1] is None]
    print("=" * 72)
    print("汇总: %d/%d 通过，%d 失败，%d 跳过"
          % (len(ran) - len(bad), len(ran), len(bad), len(skipped)))
    if bad:
        print("失败组：" + "、".join(n for n, _, _ in bad))
    print("全部通过 ✅" if not bad else "有失败 ❌")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
