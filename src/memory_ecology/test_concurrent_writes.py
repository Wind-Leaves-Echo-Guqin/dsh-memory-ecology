#!/usr/bin/env python3
"""test_concurrent_writes.py — 多 agent 并发写入实测（Windows 优先）。

为什么需要这一套
----------------
现有 150 个测试全是**单进程串行**的。真实部署是：
  · cron 到点跑门 ①②③④
  · 用户/agent 同时手动跑同一条命令
  · dsh 每个 tool call 都会 spawn 一次 CLI
  · 多个 agent 会话共用同一个 MEMORY_ECOLOGY_ROOT

这些都会造成**同一份数据被多个进程同时读写**。锁设计得对不对，
串行测试永远测不出来。

本套件测四件事
--------------
  C1  同一道门并发 N 个实例 → 只应有 1 个真正执行，其余跳过；数据不重复不丢失
  C2  不同门并发（门①写 detail / 门②挤 L1 / 门④归档）→ 互不破坏
  C3  stale 锁回收 → 进程被 kill 后残留的锁能被下一次自动清掉
  C4  原子写 → 并发写同一文件时读者永远看不到半截内容

判据（硬）
----------
  不重复：detail 条目数 == 期望条目数（无重复 slug）
  不丢失：所有候选都进了 detail 或明确记录为跳过
  不损坏：所有 .md 文件都能被 parse_frontmatter 解析
  无半截：并发读期间没有出现解析失败的中间态

运行：
    python test_concurrent_writes.py            # 全部
    python test_concurrent_writes.py -k C1      # 只跑 C1
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def _mkroot(base: Path) -> Path:
    """建一个最小可用数据根。"""
    for sub in ("memories/pending", "memories/detail", "memories/quarantine",
                "memories/archive", "memories/gate_log", "scripts"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    (base / "memories" / "MEMORY.md").write_text("", encoding="utf-8")
    (base / "memories" / "USER.md").write_text("", encoding="utf-8")
    return base


def _cand(root: Path, name: str, body: str) -> Path:
    p = root / "memories" / "pending" / f"group-{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# group {name}\n- [group] {name}：{body}\n", encoding="utf-8")
    return p


def _run(args, cwd, env_root, timeout=120):
    env = dict(os.environ)
    env["MEMORY_ECOLOGY_ROOT"] = str(env_root)
    env["PYTHONIOENCODING"] = "utf-8"   # 不模拟 GBK：编码问题由其他测试覆盖
    return subprocess.run([PY, *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _detail_files(root: Path):
    d = root / "memories" / "detail"
    return sorted(p for p in d.glob("*.md") if not p.name.startswith("."))


def _all_parse(root: Path) -> tuple[int, int]:
    """返回 (可解析数, 损坏数)。损坏含 YAML 错、frontmatter 缺。"""
    ok = bad = 0
    for p in _detail_files(root):
        try:
            txt = p.read_text(encoding="utf-8")
            # 最小可用性检查：必须有 --- 包裹的 frontmatter
            if txt.startswith("---") and txt.count("---") >= 2:
                ok += 1
            else:
                bad += 1
        except Exception:
            bad += 1
    return ok, bad


# ── C1：同一道门并发 ─────────────────────────────────────────────────────

def case_c1(n: int = 6) -> dict:
    """n 个 write_gate 同时跑，验证只有 1 个真执行且数据不重复。"""
    base = Path(tempfile.mkdtemp(prefix="eco-c1-"))
    try:
        root = _mkroot(base)
        cands = n
        for i in range(cands):
            _cand(root, f"c1-{i}", f"并发写入测试第 {i} 条")

        procs = []
        for _ in range(n):
            procs.append(subprocess.Popen(
                [PY, "write_gate.py"], cwd=str(HERE),
                env={**os.environ, "MEMORY_ECOLOGY_ROOT": str(root),
                     "PYTHONIOENCODING": "utf-8"},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace"))

        outs = []
        for p in procs:
            o, e = p.communicate(timeout=180)
            outs.append((p.returncode, o, e))

        detail = _detail_files(root)
        names = [p.name for p in detail]
        dup = len(names) - len(set(names))
        ok, bad = _all_parse(root)
        ran = sum(1 for rc, o, e in outs if "本轮处理" in (o or "") and "0 条候选" not in (o or ""))

        return {
            "case": "C1 同门并发",
            "procs": n,
            "ran": ran,
            "detail": len(detail),
            "expected_detail": cands,
            "dup": dup,
            "parse_ok": ok,
            "parse_bad": bad,
            "pass": ran == 1 and dup == 0 and bad == 0 and len(detail) == cands,
            "raw": [(rc, (o or "")[:160].replace("\n", " ")) for rc, o, e in outs],
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── C2：不同门并发 ───────────────────────────────────────────────────────

def case_c2() -> dict:
    """门①写 detail、门②挤 L1、门④复核 同时跑，互不破坏。"""
    base = Path(tempfile.mkdtemp(prefix="eco-c2-"))
    try:
        root = _mkroot(base)
        for i in range(4):
            _cand(root, f"c2-{i}", f"跨门并发第 {i} 条")

        cmds = [["write_gate.py"], ["eco_quota.py"], ["eco_review.py"]]
        procs = [subprocess.Popen(
            [PY, *c], cwd=str(HERE),
            env={**os.environ, "MEMORY_ECOLOGY_ROOT": str(root),
                 "PYTHONIOENCODING": "utf-8"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace") for c in cmds]

        outs = []
        for p in procs:
            o, e = p.communicate(timeout=180)
            outs.append((p.returncode, (o or "")[:200]))

        detail = _detail_files(root)
        ok, bad = _all_parse(root)
        names = [p.name for p in detail]
        return {
            "case": "C2 跨门并发",
            "detail": len(detail),
            "dup": len(names) - len(set(names)),
            "parse_ok": ok,
            "parse_bad": bad,
            "pass": bad == 0 and len(names) == len(set(names)) and len(detail) == 4,
            "raw": outs,
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── C3：stale 锁回收 ─────────────────────────────────────────────────────

def case_c3() -> dict:
    """伪造一个死进程的锁，验证下一次能自动回收而不是永久跳过。"""
    base = Path(tempfile.mkdtemp(prefix="eco-c3-"))
    try:
        root = _mkroot(base)
        _cand(root, "c3-1", "stale 锁回收测试")
        lock = root / "scripts" / ".eco_quota.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        # 写一个几乎必定不存在的 PID
        lock.write_text("999999", encoding="utf-8")

        r = _run(["eco_quota.py"], HERE, root)
        return {
            "case": "C3 stale 锁回收",
            "rc": r.returncode,
            "stdout": (r.stdout or "")[:300],
            "stderr": (r.stderr or "")[:300],
            "lock_left": lock.exists(),
            "recovered": "回收" in (r.stderr or "") or "回收" in (r.stdout or ""),
            "pass": not lock.exists(),   # 关键：锁必须被清掉，不能永久留
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── C4：原子写（并发读看不到半截）─────────────────────────────────────────

def case_c4(rounds: int = 60) -> dict:
    """一边反复写同一批文件，一边并发读，统计是否读到无法解析的内容。"""
    base = Path(tempfile.mkdtemp(prefix="eco-c4-"))
    try:
        root = _mkroot(base)
        target = root / "memories" / "detail" / "atomic-probe.md"
        target.write_text("---\nid: probe\n---\n初始\n", encoding="utf-8")

        sys.path.insert(0, str(HERE))
        from lib.fs import atomic_write

        stop = threading.Event()
        bad_reads = []
        reads = [0]

        def reader():
            # 真实读者（memstore.load_detail / memory_query）在 Windows 上遇到
            # 文件正被 replace 时会拿到 WinError 32/5，正常实现都会重试一次。
            # 这里模拟同样的行为：读失败立即重试，只有"内容坏"才算真正的失败
            # —— 因为原子写的承诺是"要么旧内容，要么新内容"，不是"永远不报错"。
            while not stop.is_set():
                txt = None
                for _ in range(20):
                    try:
                        txt = target.read_text(encoding="utf-8")
                        break
                    except (OSError, UnicodeDecodeError):
                        time.sleep(0.001)
                if txt is None:
                    bad_reads.append("<persistent read error>")
                    continue
                reads[0] += 1
                if not (txt.startswith("---") and txt.count("---") >= 2):
                    bad_reads.append(txt[:60])

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        payload = "---\nid: probe\n---\n" + ("内容行\n" * 200)
        for i in range(rounds):
            atomic_write(target, payload + f"# {i}\n")
        stop.set()
        t.join(timeout=5)

        return {
            "case": "C4 原子写可见性",
            "rounds": rounds,
            "reads": reads[0],
            "bad_reads": len(bad_reads),
            "pass": len(bad_reads) == 0 and reads[0] > 0,
            "sample": bad_reads[:2],
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── C5：同进程锁重入（多 agent 同进程场景）─────────────────────────────────

def case_c5() -> dict:
    """同一进程内重复获取同一把锁必须互斥。

    为什么需要：多 agent 可能是**同进程多线程**（宿主内嵌、dsh 插件同进程调用），
    此时 O_EXCL 锁看到的是自己的 PID。若实现把"pid == 自己"当成残留锁回收，
    就会把外层持有的锁 unlink 掉再重抢 —— 互斥完全失效，两个写者同时进临界区。

    实测背景：gatekit.acquire_lock_auto 旧实现正是这个行为
    （`pid != os.getpid() and _pid_alive(pid)`），嵌套 with locked 内层返回 True。
    """
    sys.path.insert(0, str(HERE))
    from lib.cli_base import locked

    base = Path(tempfile.mkdtemp(prefix="eco-c5-"))
    try:
        root = _mkroot(base)
        lockp = root / "scripts" / ".c5.lock"

        nested_inner = None
        with locked(lockp) as outer:
            with locked(lockp) as inner:
                nested_inner = inner

        # 顺序两次必须都成功（释放后应可重抢）
        with locked(lockp) as s1:
            pass
        with locked(lockp) as s2:
            pass

        return {
            "case": "C5 同进程锁重入",
            "outer": True,
            "inner": nested_inner,
            "seq1": s1,
            "seq2": s2,
            "pass": bool(outer) and nested_inner is False and s1 and s2,
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="只跑名字含该串的用例")
    ap.add_argument("--n", type=int, default=6, help="C1 的并发数")
    args = ap.parse_args()

    cases = [("C1", lambda: case_c1(args.n)),
             ("C2", case_c2),
             ("C3", case_c3),
             ("C4", case_c4),
             ("C5", case_c5)]
    results = []
    for name, fn in cases:
        if args.k and args.k not in name:
            continue
        print(f"\n=== {name} ===", flush=True)
        try:
            r = fn()
        except Exception as e:
            r = {"case": name, "pass": False, "error": f"{type(e).__name__}: {e}"}
        results.append(r)
        for k, v in r.items():
            if k == "raw":
                continue
            print(f"  {k:16} {v}", flush=True)

    print("\n=== 汇总 ===")
    allpass = True
    for r in results:
        flag = "PASS" if r.get("pass") else "FAIL"
        if not r.get("pass"):
            allpass = False
        print(f"  {flag}  {r.get('case')}")
    print(f"\n{sum(1 for r in results if r.get('pass'))} / {len(results)} 通过")
    return 0 if allpass else 1


if __name__ == "__main__":
    sys.exit(main())
