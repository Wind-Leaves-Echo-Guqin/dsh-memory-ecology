#!/usr/bin/env python3
"""经验笔记本回扫并行执行器（2026-09-03 并行化：3 路并发，总时长 4-6h → ~2h）

从 queue.json 取会话，用 ThreadPoolExecutor 并行调用 eco_note_backfill.main()。
竞态隔离：
- 候选输出按会话分文件 cand-<session>.md（--out）
- processed 状态按会话分文件 .pc-<sid>.json（backfill 已改造）
- main(argv) 传参（不共享 sys.argv），线程安全

用法（cron：no_agent 无参数；后台：直接 python 本文件）：
    python eco_note_backfill_runall.py [--workers 3] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import eco_note_backfill as bf

QUEUE = bf.backfill_dir() / "queue.json"


from lib.gatekit import pid_alive as _pid_alive  # R7：单源收编（2026-10-04），跨平台+范围防御以 gatekit 为准

def acquire_lock() -> Path | None:
    """② v2.1.1 队列锁（#53 双执行器教训）：O_EXCL 创建 .runall.lock；
    已存在且 pid 存活 → 拒绝；pid 已死/stale → 清理重拿。"""
    bd = bf.backfill_dir()
    bd.mkdir(parents=True, exist_ok=True)
    lock_path = bd / ".runall.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock_path
    except FileExistsError:
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = -1
        if pid > 0 and _pid_alive(pid):
            return None
        # OCR 2026-10-03: stale 锁用「原子改名」夺走（unlink 后再建的 TOCTOU 窗口
        # 可能让两个执行器互删新锁双双持锁）。改名同一时刻只可能有一方成功，
        # 失败方（OSError）说明锁已被他人回收/重建 → 放弃本轮。
        stale = lock_path.with_name(lock_path.name + f".stale-{os.getpid()}")
        try:
            lock_path.replace(stale)
        except OSError:
            return None
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return lock_path
        except FileExistsError:
            return None
        finally:
            try:
                stale.unlink()
            except OSError:
                pass


def load_queue(strict: bool = False) -> dict:
    """读回扫队列。strict=True（执行器主流程用）：文件损坏/结构非法时抛错，
    OCR 2026-10-03: 不再静默当空队列——那会把全部 pending 会话无痕丢掉。"""
    if not QUEUE.exists():
        return {"pending": [], "done": []}
    try:
        d = json.loads(QUEUE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        if strict:
            raise ValueError(f"queue.json 损坏或不可读: {e}") from e
        return {"pending": [], "done": []}
    if not isinstance(d, dict):
        if strict:
            raise ValueError("queue.json 结构非法（顶层非 dict）")
        return {"pending": [], "done": []}
    return d


def save_queue(q: dict) -> None:
    # OCR 2026-10-03: 原地覆盖写入遇崩溃/断电会留截断文件（再被当空队列）→ 改原子写
    tmp = QUEUE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(QUEUE)


def work(sid: str, dry: bool) -> tuple[str, int]:
    """处理单个会话（独立输出文件，独立状态文件）。"""
    argv = ["--sessions", sid,
            "--max-cands", "150", "--max-tokens", "8000",
            "--out", str(bf.backfill_dir() / f"cand-{sid}.md")]
    if dry:
        argv.append("--dry-run")
    return sid, bf.main(argv)


def main() -> int:
    from lib.cli_base import bootstrap as _bootstrap  # v2.5.0：编码兜底
    _bootstrap()
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lock = acquire_lock()
    if lock is None:
        print("⚠️ 检测到另一执行器在跑（.runall.lock），退出避免双活（#53 教训）")
        return 2
    try:
        return _run(args)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _run(args) -> int:
    try:
        q = load_queue(strict=True)
    except ValueError as e:
        # OCR 2026-10-03: 队列损坏不再当空队列静默清账——报错退出等人工修复
        print(f"❌ {e}——拒绝按空队列处理（防静默丢弃 pending 会话），请人工修复 {QUEUE}")
        return 2
    pending = q.get("pending", [])
    if not pending:
        print("回扫队列已空（全部会话处理完毕或队列未配置）")
        return 0
    print(f"并行回扫：{len(pending)} 个会话 / {args.workers} 路并发")

    done_ok = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(work, sid, args.dry_run): sid for sid in pending}
        # OCR 2026-10-03: as_completed 取代提交序阻塞——先完成的会话先落账，不吃队头阻塞
        for fut in as_completed(futures):
            sid = futures[fut]
            try:
                s, rc = fut.result()
                if rc == 0 and not args.dry_run:
                    done_ok += 1
                    q = load_queue(strict=True)
                    q["pending"] = [x for x in q.get("pending", []) if x != sid]
                    q.setdefault("done", []).append(sid)
                    save_queue(q)
                    print(f"✅ {sid} 完成（{done_ok}/{len(pending)}）")
                else:
                    print(f"✖ {sid} rc={rc}（{'dry-run 不标记' if args.dry_run else '不标记 done，下次续跑'}）")
            except Exception as e:
                print(f"✖ {sid} 异常: {e}（不标记 done，下次续跑）")
    remaining = load_queue(strict=True).get("pending", [])
    if remaining and not args.dry_run:
        print(f"⚠️ 本批完成 {done_ok}/{len(pending)}，剩余 {len(remaining)} 个留待下次：{remaining}")
        return 1
    if args.dry_run:
        # OCR 2026-10-03: dry-run 永不标记 done → remaining 恒非空，原逻辑恒返回 1
        # 造成健康 dry-run 假报警；dry-run 按预览语义返回 0
        print(f"🏁 dry-run 预览完成：{len(pending)} 个会话均未标记 done（正式运行才会处理）")
        return 0
    print(f"🏁 队列清空，全部 {done_ok} 个会话处理完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
