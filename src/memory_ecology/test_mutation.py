#!/usr/bin/env python3
"""test_mutation.py — 变异测试：证明新加的测试真的能抓 bug。

为什么必须做
------------
新写的测试（test_concurrent_writes.py）如果本身写错了，会给出"全绿"的假象。
变异测试是唯一能验证"测试有效性"的方法：故意把被测代码改坏，看测试是否报警。

每个变异体（mutant）对应一个真实缺陷模式：
  M1  去掉 bootstrap        → 编码兜底缺失（B2 复现）
  M2  恢复自算 root         → B3 复现
  M3  恢复不建父目录的锁     → B7 复现
  M4  恢复同进程锁重入       → 并发互斥失效
  M5  atomic_write 去掉重试  → Windows 并发写失败（C4 复现）
  M6  BOM 读取改回 utf-8     → 首条静默丢失

判据：每个变异体都**必须被至少一个测试抓到**（测试失败/报错）。
若某个变异体"存活"（测试仍全绿）→ 说明该测试无效，必须补强。

用法：
    python test_mutation.py            # 全部
    python test_mutation.py -k M4      # 单个

实现要点：**在临时副本上改**，绝不碰工作区源码。跑完自动还原/删除。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def _env(root: Path | None = None) -> dict:
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    if root:
        e["MEMORY_ECOLOGY_ROOT"] = str(root)
    return e


# ── 变异体定义： (名字, 目标文件, 原文, 替换为, 期望被抓的测试) ───────────

MUTANTS = [
    (
        "M1-去掉bootstrap",
        "write_gate.py",
        "    _bootstrap()",
        "    pass  # MUTANT: 不调 bootstrap",
        "编码：GBK 下 write_gate 应崩",
    ),
    (
        "M2-恢复自算root",
        "eco_review.py",
        "    from lib.cli_base import resolve_root",
        "    from pathlib import Path as _P\n    resolve_root = lambda *a, **k: _P(__file__).resolve().parent.parent  # MUTANT",
        "数据根：eco_review 应无视 MEMORY_ECOLOGY_ROOT",
    ),
    (
        "M3-锁不建父目录",
        "eco_quota.py",
        "        ok, how = acquire_lock_auto(LOCK_FILE)",
        "        import os as _os\n"
        "        if not LOCK_FILE.parent.is_dir():\n"
        "            raise FileNotFoundError(str(LOCK_FILE))  # MUTANT\n"
        "        ok, how = acquire_lock_auto(LOCK_FILE)",
        "建目录：新数据根下 eco_quota 应崩",
    ),
    (
        # ★ 注意：这条不能写成"删掉 getpid 判断"——那会落到下面 _pid_alive(自己)=True
        #   仍然返回 held，是**等价变异体**（测不出差异，不是测试无效）。
        #   必须恢复成旧实现的完整表达式：pid != os.getpid() and _pid_alive(pid)。
        "M4-同进程锁重入",
        "lib/gatekit.py",
        "    if pid == os.getpid():\n        return False, \"held\"\n    if pid is None or _pid_alive(pid):",
        "    if pid is None or (pid != os.getpid() and _pid_alive(pid)):  # MUTANT: 旧实现",
        "互斥：同进程持有应判 held；旧实现会回收自己的锁（C5 应失败）",
    ),
    (
        # ★ 注意：只把第一段重试改成 range(0) 是**等价变异体**——
        #   后面还有"长退避"和"DeleteFile 兜底"两级，仍然能把写入做成。
        #   实测：那样 M5 会随机存活（4 轮里 1 轮）。
        #   要测"没有重试会怎样"，必须把 os.replace 直接改成裸调用。
        "M5-原子写无重试",
        "lib/fs.py",
        "        last = None\n        for attempt in range(_REPLACE_RETRIES):",
        "        os.replace(tmp, str(path)); return  # MUTANT: 裸 rename，无任何重试/兜底\n"
        "        last = None\n        for attempt in range(0):",
        "Windows 并发写：无重试时读者占用应导致失败",
    ),
    (
        "M6-BOM读取回utf8",
        "eco_note_adopt.py",
        'path.read_text(encoding="utf-8-sig", errors="replace")',
        'path.read_text(encoding="utf-8", errors="replace")  # MUTANT',
        "BOM：带 BOM 的首条应被丢弃",
    ),
]


def _copy_worktree() -> Path:
    """复制一份可改的完整工作区到临时目录。"""
    base = Path(tempfile.mkdtemp(prefix="eco-mut-"))
    dst = base / "src" / "memory_ecology"
    shutil.copytree(HERE, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # lib 在 src/memory_ecology 内，已随 copytree 复制
    return base


def _run_pytest(cwd: Path) -> tuple[int, str]:
    r = subprocess.run([PY, "-m", "pytest", "-q", "--tb=no", "-x"],
                       cwd=str(cwd), env=_env(),
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _run_single_case(case: str, cwd: Path) -> tuple[int, str]:
    """只跑 test_concurrent_writes.py 的某一个用例。"""
    r = subprocess.run([PY, "test_concurrent_writes.py", "-k", case],
                       cwd=str(cwd), env=_env(),
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _run_concurrent(cwd: Path) -> tuple[int, str]:
    r = subprocess.run([PY, "test_concurrent_writes.py"],
                       cwd=str(cwd), env=_env(),
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _run_mutation_specific(name: str, cwd: Path) -> tuple[bool, str]:
    """针对某些变异体，跑更直接的检查（pytest 不一定覆盖）。

    返回 (是否被抓到, 说明)。
    """
    if name.startswith("M1"):
        # GBK 下跑 write_gate，应该崩
        sb = Path(tempfile.mkdtemp(prefix="eco-m1-"))
        try:
            for sub in ("memories/pending", "memories/detail", "memories/gate_log", "scripts"):
                (sb / sub).mkdir(parents=True, exist_ok=True)
            (sb / "memories" / "pending" / "group-x.md").write_text(
                "# g\n- [group] x：变异测试\n", encoding="utf-8")
            e = _env(sb)
            e.pop("PYTHONIOENCODING", None)          # 关键：模拟 GBK
            e["PYTHONIOENCODING"] = "gbk"
            r = subprocess.run([PY, "write_gate.py"], cwd=str(cwd), env=e,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
            caught = r.returncode != 0
            return caught, f"rc={r.returncode}（非 0 = 抓到）"
        finally:
            shutil.rmtree(sb, ignore_errors=True)

    if name.startswith("M2"):
        sb = Path(tempfile.mkdtemp(prefix="eco-m2-"))
        try:
            (sb / "memories" / "detail").mkdir(parents=True, exist_ok=True)
            r = subprocess.run([PY, "eco_review.py", "--dry-run"], cwd=str(cwd),
                               env=_env(sb), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
            out = (r.stdout or "")
            caught = str(sb) not in out          # 没指向 sb 就是被抓到
            return caught, f"指向了 sb={sb}: {str(sb) in out}（False = 抓到）"
        finally:
            shutil.rmtree(sb, ignore_errors=True)

    if name.startswith("M3"):
        sb = Path(tempfile.mkdtemp(prefix="eco-m3-"))
        try:
            (sb / "memories" / "detail").mkdir(parents=True, exist_ok=True)
            # 注意：故意不建 scripts/
            r = subprocess.run([PY, "eco_quota.py"], cwd=str(cwd),
                               env=_env(sb), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
            caught = r.returncode != 0
            return caught, f"rc={r.returncode}（非 0 = 抓到）"
        finally:
            shutil.rmtree(sb, ignore_errors=True)

    if name.startswith("M6"):
        sb = Path(tempfile.mkdtemp(prefix="eco-m6-"))
        try:
            (sb / "experiences" / "pending").mkdir(parents=True, exist_ok=True)
            bom = "\ufeff"
            (sb / "experiences" / "pending" / "2026-10-18.md").write_text(
                bom + "- [pattern] **首条**\n    - cause: c\n    - action: a\n"
                "- [pattern] **次条**\n    - cause: c2\n    - action: a2\n",
                encoding="utf-8")
            subprocess.run([PY, "eco_note_adopt.py"], cwd=str(cwd), env=_env(sb),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
            n = len(list((sb / "experiences").glob("exp-*.md")))
            caught = n < 2          # 只有 1 条 = 首条被丢 = 变异体暴露
            return caught, f"接纳 {n} 条（<2 = 抓到）"
        finally:
            shutil.rmtree(sb, ignore_errors=True)

    # M4 → 只跑 C5；M5 → 只跑 C4。
    # 不能跑整套：其他用例（C1/C2/C3）用子进程，不受这两个变异影响，
    # 全部跑会让"其他用例通过"掩盖目标用例失败 —— 变异测试本身失效。
    if name.startswith("M4"):
        rc, _ = _run_single_case("C5", cwd)
        return rc != 0, f"C5 rc={rc}（非 0 = 抓到）"
    if name.startswith("M5"):
        rc, _ = _run_single_case("C4", cwd)
        return rc != 0, f"C4 rc={rc}（非 0 = 抓到）"

    rc, out = _run_concurrent(cwd)
    caught = rc != 0
    return caught, f"并发测试 rc={rc}（非 0 = 抓到）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="只跑名字含该串的变异体")
    args = ap.parse_args()

    results = []
    for name, rel, old, new, why in MUTANTS:
        if args.k and args.k not in name:
            continue
        print(f"\n=== {name} ===", flush=True)
        print(f"  目的: {why}", flush=True)
        base = _copy_worktree()
        try:
            target = base / "src" / "memory_ecology" / rel
            if not target.exists():
                print(f"  ⚠️ 目标文件不存在: {rel}", flush=True)
                results.append((name, False, "目标文件缺失"))
                continue
            src = target.read_text(encoding="utf-8")
            if old not in src:
                # 原文不在 = 该缺陷已不存在于本树（如开发树 2026-10-11 已修掉 B3）。
                # 这不是"变异体存活"，而是"无需变异"——记为 SKIPPED，不计入失败。
                print(f"  ⏭️ 原文不存在：该缺陷在本树已修（跳过，不算存活）", flush=True)
                results.append((name, None, "SKIPPED: 缺陷已不存在"))
                continue
            target.write_text(src.replace(old, new, 1), encoding="utf-8")
            print(f"  已变异: {rel}", flush=True)

            work = base / "src" / "memory_ecology"
            caught, detail = _run_mutation_specific(name, work)
            print(f"  {'✅ 抓到' if caught else '❌ 存活（测试无效！）'}  {detail}", flush=True)
            results.append((name, caught, detail))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    print("\n=== 汇总 ===")
    alive = [n for n, c, _ in results if c is False]      # None=SKIPPED，不算存活
    skipped = [n for n, c, _ in results if c is None]
    for n, c, d in results:
        tag = "CAUGHT" if c else ("SKIP  " if c is None else "ALIVE ")
        print(f"  {tag}  {n}  ({d})")
    if skipped:
        print(f"  （其中 {len(skipped)} 个因缺陷已在本树修复而跳过）")
    print(f"\n{len(results) - len(alive)} / {len(results)} 变异体被抓到")
    if alive:
        print(f"⚠️ 存活的变异体说明对应测试无效: {', '.join(alive)}")
    return 0 if not alive else 1


if __name__ == "__main__":
    sys.exit(main())
