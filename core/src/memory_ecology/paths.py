"""paths —— 数据根解析与 IO 基础设施（stdlib only）。

**这是 v3.2.0 修掉的头号结构缺陷**：旧 `config.py:14` 在 **import 期**把根钉死
（`_HERMES = _P3.parent`），20+ 处 `HERMES = hermes_root()` 固化，运行期再设环境变量无效
（`eco_health_check.py:957` 自陈）。本模块改为**函数式** `resolve_root()`：每次调用
现算，因此运行期可切（B1）。

解析优先级（顺序即契约）：
  1. 显式参数（`--root` / 调用方传入）
  2. 环境变量 `MEMORY_ECOLOGY_ROOT`（**进程级**，不是用户级/全局——红线不碰全局）
  3. 显式配置文件 `~/.dsh/memory-eco-root`（一行路径；给"想固定但不想设 env"的人）
  4. 布局派生：dsh 默认位置
       Windows: %LOCALAPPDATA%/dsh/memory-eco
       POSIX:   ~/.local/share/dsh/memory-eco

**红线**：本包任何地方都不得写入用户级/全局环境变量（`setx` / `[Environment]::SetEnvironmentVariable`）。
根的选择只能来自上面四条，且写前要过 `Root` 自证。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

ENV_ROOT = "MEMORY_ECOLOGY_ROOT"
# 可选覆盖点（与本包自身无关的宿主概念一律不引用）
ENV_CONFIG = "MEMORY_ECOLOGY_CONFIG"


# ── 时间 ──────────────────────────────────────────────────────────────

def now() -> str:
    """本地时间 ISO 串（秒精度）。全包统一口径，便于账本排序。"""
    return datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def stamp() -> str:
    """备份文件名用时间戳（与既有 `.bak-配额前-YYYYMMDD-HHMMSS` 同口径）。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def stamp_ms() -> str:
    """毫秒级时间戳 + 进程号。

    **为什么需要**：秒级时间戳在"同一秒内连写两次"时会生成**同名备份**，后者覆盖前者
    —— 备份看起来做了，实际只剩一份（这正是"备份绝不删除"这条纪律的反面：不是有人删，
    是从来只有一份）。旧项目门③的备份名带毫秒，这里沿用并再加 pid 兜底。
    """
    now = datetime.now()
    return now.strftime("%Y%m%d-%H%M%S-") + "%03d-%d" % (now.microsecond // 1000, os.getpid())



def parse_ts(value: str) -> datetime | None:
    """宽松时间解析：只认 `YYYY-MM-DD`（含可选 T/空格 + 时分秒）。失败返回 None，绝不抛。"""
    if not value:
        return None
    s = str(value).strip().replace("T", " ").replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(s[:26] if fmt.endswith("%f") else s[:19], fmt)
        except ValueError:
            continue
    return None


def days_between(older: str, newer: str | None = None) -> float | None:
    """(newer - older) 的天数；任一解析失败返回 None（调用方据此跳过，而不是当成 0）。"""
    a = parse_ts(older)
    b = parse_ts(newer) if newer else datetime.now()
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400.0


# ── 指纹与归一 ────────────────────────────────────────────────────────

def fingerprint(*parts: str) -> str:
    """内容指纹（sha1 over NUL-separated parts）。同一内容不同排版视为同一条。"""
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def norm_text(text: str) -> str:
    """空白归一（折叠连续空白）。指纹与检索共用同一口径。"""
    import re
    return re.sub(r"\s+", " ", (text or "").strip())


def norm_key(text: str) -> str:
    """规范化键：去空白/标点/下划线（同源判定的口径，与旧 lib.fs.norm 对齐）。"""
    import re
    return re.sub(r"[\s\W_]+", "", str(text or ""))


async def _nothing():  # pragma: no cover - 占位，保持模块无副作用
    return None


# ── 根解析 ────────────────────────────────────────────────────────────

def _config_file_path() -> Path:
    env = os.environ.get(ENV_CONFIG)
    if env:
        return Path(env).expanduser()
    base = os.environ.get("DSH_HOME") or (Path.home() / ".dsh")
    return Path(base) / "memory-eco-root"


def pyproject_root_file() -> Path | None:
    """`~/.dsh/memory-eco-root`（可选的一行路径文件）。读不到返回 None。"""
    p = _config_file_path()
    try:
        if p.is_file():
            line = p.read_text(encoding="utf-8-sig").strip().splitlines()
            if line and line[0].strip() and not line[0].lstrip().startswith("#"):
                return Path(line[0].strip()).expanduser()
    except OSError:
        return None
    return None


def default_root() -> Path:
    """布局派生的默认根。**不读环境变量**——纯函数，供测试与文档说明。"""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "dsh" / "memory-eco"
    return Path.home() / ".local" / "share" / "dsh" / "memory-eco"


def resolve_root(explicit: str | os.PathLike | None = None) -> Path:
    """按优先级解析数据根。**每次调用现算**——这就是 B1 的"运行期可切"。"""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_ROOT)
    if env and env.strip():
        return Path(env.strip()).expanduser()
    cfg = pyproject_root_file()
    if cfg is not None:
        return cfg
    return default_root()


# ── 原子 IO ───────────────────────────────────────────────────────────

def _replace_with_retry(src: Path, dst: Path, attempts: int = 8) -> None:
    """`os.replace` 的 Windows 加固版。

    **这是实测必须的**：Windows 上两个进程同时 `os.replace` 同一个目标，
    后到者会拿到 `PermissionError: [WinError 5] 拒绝访问`（目标正被另一个
    replace 打开）。旧项目的 `lib/fs.py` 就是为此加了三级退避。

    **绝不"先 unlink 再 replace"**：那会在中间窗口里两个文件都消失，
    读者可能读到空文件（比失败更糟）。这里只重试，失败照常抛。
    """
    last: Exception | None = None
    delay = 0.006
    for i in range(max(1, attempts)):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:      # WinError 5 / 32：目标被占用
            last = exc
        except OSError as exc:              # 其它 OSError 也退避一次（杀毒/索引器偶发占用）
            last = exc
        time.sleep(delay)
        delay = min(0.25, delay * 1.8)
    if last is not None:
        raise last


def write_atomic(path: Path, data: str) -> None:
    """原子写。

    临时名**必须**带 pid：固定名在多进程并发时互相踩，实测会写出半个文件
    （设计文档 §9.6 bug 2）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def append_line(path: Path, line: str) -> None:
    """单次追加写。**并发安全**（Windows 需要额外串行化，见下）。

    为什么不能只靠 `O_APPEND`：
    POSIX 上 `O_APPEND` 的 seek+write 是原子的，写得再大也不会互相覆盖。
    **Windows 不是**——MSVCRT 的 `_O_APPEND` 是"每次写之前先 seek 到末尾"，
    seek 与 write 之间没有锁。6 个进程同时写 9 KB 时，两个进程会算出**同一个
    偏移** N，各自写 [N, N+9017)，后写的整个覆盖先写的 → **恰好丢一整行**，
    而且两次 `os.write` 都返回满长度（所以查返回值也发现不了）。

    实测（40 轮 × 6 进程 × 5 行 × 9017 字节）：`{29: 3, 30: 37}` → **8% 丢行**。
    这不是理论风险：投递并发时真的会静默丢用户写下的记忆。

    修法：Windows 上用一个字节的文件锁把"seek+write"串起来（POSIX 不需要，
    它的 O_APPEND 本来就原子，加锁纯属浪费）。锁是**建议锁**，只对走本函数的
    写者有效——项目的不变式是"单一写者"，这里防的是同一写者内的并发线程/进程。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = line.encode("utf-8")

    def _do_write() -> None:
        # 锁 fd 与写 fd **必须分开**：`msvcrt.locking` 锁的是字节区间，
        # 同一个 fd 上再 O_APPEND 写会撞上自己持有的锁（实测 PermissionError 13）。
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND |
                     getattr(os, "O_BINARY", 0), 0o644)
        try:
            # 契约是"可能写得比要求少"，短写必须继续写，否则尾部静默丢失。
            written = 0
            while written < len(data):
                n = os.write(fd, data[written:])
                if n <= 0:
                    raise OSError("append_line: write returned %r for %s"
                                  % (n, path))
                written += n
        finally:
            os.close(fd)

    with _append_lock(path):
        _do_write()


@contextlib.contextmanager
def _append_lock(path: Path):
    """用**旁路锁文件**把并发的"seek+write"串起来（只 Windows 需要）。

    为什么不用目标文件本身加锁：`msvcrt.locking` 锁的是字节区间，同一文件的
    另一个 fd 再做 `O_APPEND` 写会撞上自己持有的锁 → `PermissionError 13`
    （实测：40/40 轮全部写失败）。改成锁 `<file>.appendlock` 才互不干扰。

    锁文件不是"记忆文件"：它以 `.appendlock` 结尾，`iter_candidates` 只认
    `.md`，备份/目录渲染都会跳过它；且它是可安全删除的纯协调物。
    POSIX 不需要这把锁：`O_APPEND` 的 seek+write 本来就原子。
    """
    if os.name != "nt":
        yield
        return
    import msvcrt
    import time as _time

    lockpath = path.with_name(path.name + ".appendlock")
    fd = os.open(str(lockpath), os.O_RDWR | os.O_CREAT |
                 getattr(os, "O_BINARY", 0), 0o644)
    got = False
    try:
        deadline = _time.monotonic() + 30.0   # 与 sqlite busy_timeout 同量级
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                got = True
                break
            except OSError:
                if _time.monotonic() > deadline:
                    # 拿不到锁不能静默继续（那就会丢数据）；直接抛，让调用方 fail-open
                    raise OSError("append_line: 30s 内拿不到 %s 的追加锁" % lockpath)
                _time.sleep(0.002)
        yield
    finally:
        if got:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(fd)
        # **锁文件不能删**。删了会引入比丢行更糟的竞态：
        #   A 解锁→删文件；B 已在**旧 inode** 上等锁；C 创建**新文件**并拿到锁
        #   → B 与 C 同时进入临界区 → 又开始丢行（实测：删了以后丢行率
        #   从 0% 反弹到 10%，且出现 25/26 这种一次丢多行）。
        # 它只是个 1 字节空文件，被 iter_candidates 按 `.md` 后缀跳过，
        # 备份轮转也会忽略；留着是正确取舍。


def read_text(path: Path) -> str:
    """读文本：`utf-8-sig` 兜 BOM（BOM 会让首条静默丢失），失败返回空串。

    **必须 `newline=""`**：`Path.read_text()` 默认做通用换行翻译（CRLF→LF），
    那样读进来的文本**永远不含 `\\r\\n`**，于是 `store.parse_l1` 的换行风格检测
    恒定返回 `\\n`，写回时把整个 MEMORY.md 从 CRLF 翻成 LF —— 一次写就是一整文件 diff，
    而且用户/别的工具看到的是"文件被全改了"。实测踩到。
    """
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            return f.read()
    except OSError:
        return ""


def backup_before_write(path: Path, tag: str = "配额前", keep: int = 8) -> Path | None:
    """写前备份 `<名>.bak-<tag>-<ts>`（与既有命名同口径）。

    **备份绝不删除**是项目红线；但备份本身需要保留策略，否则无界增长（生产根实测
    29 个 `MEMORY.md.bak-配额前-*`）。策略：保留最近 `keep` 份，更早的**移入**
    `<父目录>/_bak-archive/`（**不是删除**——红线 1 在代码层禁用物理删除）。
    `keep<=0` 表示不设上限。

    时间戳必须带毫秒（`stamp_ms`）：秒级在连写时同名覆盖，等于"备份只剩一份"。
    """
    path = Path(path)
    if not path.is_file():
        return None
    bak = path.with_name("%s.bak-%s-%s" % (path.name, tag, stamp_ms()))
    try:
        write_atomic_bytes(bak, path.read_bytes())
    except OSError:
        return None
    if keep > 0:
        prune_backups(path, keep)
    return bak


def write_atomic_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def list_backups(path: Path) -> list[Path]:
    path = Path(path)
    return sorted(path.parent.glob(path.name + ".bak-*"))


def prune_backups(path: Path, keep: int) -> list[Path]:
    """把超期备份**移入** `_bak-archive/`（可逆，绝不 unlink）。返回被移动的列表。"""
    baks = list_backups(path)
    if len(baks) <= keep:
        return []
    moved = []
    arch = Path(path).parent / "_bak-archive"
    for b in baks[: len(baks) - keep]:
        try:
            arch.mkdir(parents=True, exist_ok=True)
            os.replace(b, arch / b.name)
            moved.append(b)
        except OSError:
            continue
    return moved


# ── SQLite 连接（并发三修之三）────────────────────────────────────────

def connect(db_path: Path, timeout: float = 15.0):
    """打开账本库：**WAL + busy_timeout**（缺一个，多进程整合就 `database is locked`）。
    `db_path` 不存在时按创建语义打开（调用方决定是否真的写）。
    """
    import sqlite3
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=%d" % int(timeout * 1000))
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        pass
    return conn


def connect_ro(db_path: Path) -> "object | None":
    """**只读**打开（GUI/体检用）：`mode=ro`，绝不创建、绝不加锁升级。"""
    import sqlite3
    p = Path(db_path)
    if not p.is_file():
        return None
    try:
        return sqlite3.connect("file:%s?mode=ro" % p.as_posix(), uri=True, timeout=5)
    except sqlite3.Error:
        return None


def probe_writable(path: Path) -> bool:
    """写前自证：**探针名必须每进程唯一**。

    固定名探针在 8 并发下互相删，后到的进程被判成"根不可写"→ 静默只读降级 → 丢写
    （设计文档 §9.6 bug 1，实测丢 6/40）。
    """
    probe = Path(path) / ".eco" / (".write-probe.%d" % os.getpid())
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("1", encoding="utf-8")
        return True
    except OSError:
        return False
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def env_python() -> str:
    """当前解释器（子进程调用用绝对路径，避免 PATH 里换了个 python）。"""
    return sys.executable or "python"

