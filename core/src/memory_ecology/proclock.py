"""proclock —— 四门/维护的进程级锁（「唯一写者」的跨进程防线）。

背景（v17 对照 · 缺口 3）：门可能同时被多个触发源拉起——hermes 内部 cron、
插件 `agent/turn-stopping` 维护、人手 CLI。四门各自幂等（指纹 + 文件状态），
但**并发写同一个 root**（backup → write_atomic → ledger commit）没有跨进程
互斥时仍可能交错出脏状态。

## 互斥机制（质量门②重设计，为什么换了基座）

文件锁（pid+ts+30 分钟判死）被两轮对抗测试证明**修不干净**：
  - 第一轮：check-then-act 双获锁 → O_EXCL 修掉；
  - 第二轮：接管路径 write+回读校验仍有 TOCTOU（W1.verify 与 W2.write 的
    交错窗口），5 轮复现 4 轮双获锁。文件协议要正确需要原子的多步状态机，
    而 Windows 上没有可移植的办法把"判断+接管"做成一个原子动作。

真正的答案是**让操作系统当裁判**：`<name>.guard` 旁路文件上的**字节范围
建议锁**（Windows `msvcrt.locking` / POSIX `fcntl.flock`，与 paths._append_lock
同一套已实测的模式）：
  - 非阻塞 try-lock：拿到 = 本进程持有互斥；拿不到 = 另一班在跑 → locked；
  - **进程死锁自解**（OS 关闭句柄即释放）——30 分钟判死从"正确性机制"降级
    为不需要的东西；半写锁/损坏锁/未来时间戳全部不再是威胁（它们只是证据）；
  - `memories/MEMORY.md.lock` 证据文件照写（pid/ts，health/status 的观测面），
    但**只有持锁者写它**，不存在竞争。

权限类环境故障（guard 打不开/锁目录只读）仍 fail-open 放行并带 `degraded`
标注——锁是保险不是闸门，锁坏了不让四门全停，但必须可见（落账）。

`release` 由调用方在 finally 调：写 released 证据 → 解字节锁 → 关句柄。
pid 复用无绕过路径（release 只解除本进程自己持有的句柄）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import paths

STALE_MINUTES = 30   # 仅作 status() 的证据新鲜度口径（互斥本身不依赖它）

# 进程内句柄表：guard 路径 → fd。互斥句柄必须活到 release。
_HELD: dict[str, int] = {}


def lock_dir(root_path) -> Path:
    return Path(root_path) / ".eco" / "locks"


def lock_path(root_path, name: str) -> Path:
    return lock_dir(root_path) / (str(name or "gate").strip() + ".lock")


def guard_path(root_path, name: str) -> Path:
    """互斥旁路文件（字节范围锁的载体）。纯协调物，1 字节，永不删除。"""
    return lock_dir(root_path) / (str(name or "gate").strip() + ".guard")


def _read_holder(p: Path) -> dict:
    try:
        data = json.loads(paths.read_text(p) or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError, OSError):
        return {}


def _payload() -> dict:
    return {"pid": os.getpid(), "ts": paths.now(),
            "host": os.getenv("COMPUTERNAME", "")}


def _mutex_try_lock(g: Path) -> int | None:
    """打开 guard 并尝试非阻塞独占字节锁。→ fd（持有）或 None（被占/打不开）。

    与 paths._append_lock 同款：锁 fd 与证据写 fd 分开（同一 fd 上再写会撞上
    自己的锁）；锁文件永不删除（删了会引入 inode 竞态，见 _append_lock 注释）。
    """
    try:
        fd = os.open(str(g), os.O_RDWR | os.O_CREAT |
                     getattr(os, "O_BINARY", 0), 0o644)
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def acquire(root_path, name: str, stale_minutes: float = STALE_MINUTES) -> dict:
    """拿锁（非阻塞）。→ {ok: bool, locked?/degraded?, holder?}。

    **绝不抛**——锁机制的任何故障都不能让四门崩掉。
    `stale_minutes` 保留参数位（v1 文件协议的 30 分钟判死已被 OS 互斥取代，
    只影响 status() 的证据新鲜度口径）。
    """
    key = str(guard_path(root_path, name))
    try:
        lock_path(root_path, name).parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": True, "degraded": "lock-dir-unwritable:%s" % str(exc)[:120]}
    if key in _HELD:
        # 同进程重复 acquire：互斥字节锁按句柄生效，这里直接拒绝
        return {"ok": False, "locked": True, "holder": {"pid": os.getpid()},
                "reason": "already-held-in-process"}
    fd = _mutex_try_lock(Path(key))
    if fd is None:
        # 被占（正常并发）或打不开（权限类）。区分二者：能打开（os.open 返回的
        # 是 int fd，必须用 os.close）= 被占 → locked；打不开 = 权限类 fail-open。
        probe = None
        try:
            probe = os.open(key, os.O_RDWR | os.O_CREAT |
                            getattr(os, "O_BINARY", 0), 0o644)
        except OSError as exc:
            return {"ok": True, "degraded": "guard-unopenable:%s" % str(exc)[:120]}
        finally:
            if probe is not None:
                try:
                    os.close(probe)
                except OSError:
                    pass
        holder = _read_holder(lock_path(root_path, name))
        return {"ok": False, "locked": True,
                "holder": holder or {"detail": "mutex-held"},
                "reason": "another-gate-run-in-progress"}
    # 持有互斥：写证据锁文件（只有持锁者写，无竞争；写不了不挡门）
    try:
        paths.write_atomic(lock_path(root_path, name),
                           json.dumps(_payload(), ensure_ascii=False))
    except OSError:
        pass
    _HELD[key] = fd
    return {"ok": True}


def release(root_path, name: str) -> None:
    """放锁：写 released 证据 → 解字节锁 → 关句柄。**只解除本进程持有的句柄**。"""
    key = str(guard_path(root_path, name))
    fd = _HELD.pop(key, None)
    if fd is None:
        return   # 不是持有者（或已释放）——pid 复用/误调用都到不了这里
    try:
        try:
            paths.write_atomic(lock_path(root_path, name), json.dumps(
                {"pid": os.getpid(), "ts": paths.now(), "released": True,
                 "host": os.getenv("COMPUTERNAME", "")}, ensure_ascii=False))
        except OSError:
            pass   # 证据写不了不挡放锁
        finally:
            if os.name == "nt":
                import msvcrt
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def status(root_path, name: str) -> dict:
    """锁现状（health 只读用，**证据口径**）。

    证据文件显示 held 且 mtime 在 30 分钟内 → 报 held（真实的并发挡门
    以 `gate.lock-blocked` 事件为准）；证据过期/缺失/released → 未持有。
    """
    try:
        p = lock_path(root_path, name)
        if not p.exists():
            return {"held": False}
        holder = _read_holder(p)
        if holder.get("released"):
            return {"held": False}
        age = None
        days = paths.days_between(str(holder.get("ts") or ""))
        if days is not None:
            age = days * 1440.0
        else:
            try:
                import datetime
                age = (datetime.datetime.now().timestamp() - p.stat().st_mtime) / 60.0
            except OSError:
                age = None
        if age is None or age >= STALE_MINUTES:
            return {"held": False}   # 证据过期 = 持有者已死（OS 已释互斥）
        return {"held": True, "pid": holder.get("pid"), "ts": holder.get("ts"),
                "age_min": round(age, 1)}
    except OSError:
        return {"held": False}
