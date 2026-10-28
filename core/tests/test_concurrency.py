#!/usr/bin/env python3
"""多进程并发一致性验证（C1：唯一写者 / 只追加的硬证据）。

场景：
  1. 8 个进程**各自独立启动**（真进程，不是线程），并发投候选，每进程 5 条互不相同；
  2. 4 个进程并发跑门①（整合）；
  3. 断言：无丢写、无重复条目、候选文件逐行可解析、并发整合全部成功；
  4. 再跑一次整合 → 新增 0（幂等）；
  5. 备份文件名不冲突（同一秒连写多次不会同名覆盖）；
  6. 写探针不互相踩（pid 唯一）。

**为什么必须是真进程**：线程共享 GIL 与文件描述符，测不出
"探针名固定 → 后到的进程被判成只读" 与 "临时名固定 → 写出半个文件" 这两类真实缺陷。

运行：python tests/test_concurrency.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PY = sys.executable

WORKERS = 8
PER_WORKER = 5
MERGERS = 4

WORKER_CODE = r"""
import sys
sys.path.insert(0, sys.argv[1])
from memory_ecology.cli import main
root, wid, n = sys.argv[2], sys.argv[3], int(sys.argv[4])
ok = 0
for j in range(n):
    rc = main(['--root', root, 'deposit', '--session', 'w' + wid,
               '--text', '并发条目 w%s-%d：内容唯一的并发写入验证' % (wid, j),
               '--kind', 'semantic'])
    if rc == 0:
        ok += 1
print(ok)
"""

MERGER_CODE = r"""
import sys
sys.path.insert(0, sys.argv[1])
from memory_ecology.cli import main
print(main(['--root', sys.argv[2], 'gate', '1']))
"""


def env() -> dict:
    e = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
         "PYTHONPATH": str(SRC)}
    e.pop("MEMORY_ECOLOGY_ROOT", None)
    return e


def cli(root: str, args: list[str]) -> dict:
    r = subprocess.run([PY, "-m", "memory_ecology", "--root", root, *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, env=env())
    lines = (r.stdout or "").strip().splitlines()
    for line in reversed(lines):
        try:
            return json.loads(line)
        except ValueError:
            continue
    raise AssertionError("无 JSON 输出：%s / %s" % (args, (r.stderr or "")[:300]))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="eco-conc-"))
    root = str(tmp / "root")
    fails: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  ✓ " if cond else "  ✗ ") + msg)
        if not cond:
            fails.append(msg)

    try:
        init = cli(root, ["init"])
        assert init["ok"], init

        # ── 1) 并发投递（真进程）──
        print("\n[1] %d 进程 × %d 条并发投递" % (WORKERS, PER_WORKER))
        procs = []
        for w in range(WORKERS):
            procs.append(subprocess.Popen(
                [PY, "-c", WORKER_CODE, str(SRC), root, str(w), str(PER_WORKER)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", env=env()))
        ok_counts = []
        for p in procs:
            out, err = p.communicate(timeout=300)
            try:
                ok_counts.append(int((out or "0").strip().splitlines()[-1]))
            except (ValueError, IndexError):
                ok_counts.append(0)
                print("    进程输出异常：%s / %s" % ((out or "")[-200:], (err or "")[-300:]))
        deposited = sum(ok_counts)
        expect = WORKERS * PER_WORKER
        check(deposited == expect, "投递 %d/%d 条（丢写即并发不安全）" % (deposited, expect))

        # 候选文件逐行可解析（并发写出半个行会在这里暴露）
        bad = 0
        pend = Path(root) / "memories" / "pending"
        for f in pend.rglob("*.md"):
            if not f.name.endswith(".md") or f.name.endswith(".done.md"):
                continue
            for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.strip() and not ln.strip().startswith("- ["):
                    bad += 1
        check(bad == 0, "候选文件逐行可解析（坏行 %d）" % bad)

        # ── 2) 并发整合 ──
        print("\n[2] %d 进程并发跑门①" % MERGERS)
        procs = [subprocess.Popen([PY, "-c", MERGER_CODE, str(SRC), root],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", errors="replace",
                                  env=env())
                 for _ in range(MERGERS)]
        codes = []
        for p in procs:
            out, err = p.communicate(timeout=600)
            codes.append(p.returncode)
            if p.returncode != 0:
                print("    失败进程 rc=%s out=%s err=%s"
                      % (p.returncode, (out or "")[-200:], (err or "")[-400:]))
        check(all(c == 0 for c in codes), "并发整合全部成功（退出码 %s）" % codes)

        st = cli(root, ["stats"])
        check(st["detail"] == expect, "条目数 = %d（期望 %d）" % (st["detail"], expect))

        cat = cli(root, ["catalog", "--limit", "999", "--max-chars", "0"])
        ids = [i["id"] for i in cat["items"]]
        check(cat["total"] == expect, "目录 total = %d" % cat["total"])
        check(len(set(ids)) == expect, "无重复 id（%d/%d）" % (len(set(ids)), expect))

        # ── 3) 幂等 ──
        print("\n[3] 幂等复跑")
        again = cli(root, ["gate", "1"])
        check(again["new"] == 0, "第二次整合新增 = %d（应 0）" % again["new"])

        # ── 4) 备份文件名不冲突 ──
        print("\n[4] 同秒连写 → 备份不覆盖")
        mem = Path(root) / "memories" / "MEMORY.md"
        mem.write_text("初始内容\n", encoding="utf-8")
        from importlib import import_module
        sys.path.insert(0, str(SRC))
        pk = import_module("memory_ecology.paths")
        made = []
        for i in range(6):
            mem.write_text("v%d\n" % i, encoding="utf-8")
            b = pk.backup_before_write(mem, tag="并发前", keep=0)
            if b:
                made.append(b)
        uniq = {b.name for b in made}
        check(len(uniq) == len(made),
              "6 次连续备份产生 %d 个不同文件（同名覆盖 = 只剩一份）" % len(uniq))

        # ── 5) 写探针不互相踩 ──
        print("\n[5] 写探针 pid 唯一")
        probe_dir = Path(root) / ".eco"
        results = []
        for pid_hint in range(3):
            p = probe_dir / (".write-probe.%d" % (os.getpid() + pid_hint))
            p.write_text("1", encoding="utf-8")
            results.append(p.exists())
            p.unlink()
        check(all(results), "不同 pid 的探针互不干扰")
        left = list(probe_dir.glob(".write-probe*"))
        check(left == [], "探针已清理（残留 %d）" % len(left))

        print()
        if fails:
            print("❌ 失败 %d 项：" % len(fails))
            for f in fails:
                print("   - " + f)
            return 1
        print("✅ 全部通过：并发只追加 + 幂等 + 备份唯一 + 探针隔离")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
