"""sqlindex — O7 SQL 派生索引层（FTS5+BM25 检索 + 事件账，设计稿 v0.1-20261003）。

定位（设计稿 §2-O7）：**派生索引，不是真源**——Markdown 文件仍是唯一真源，
索引可随时从文件全量重建（rebuild 幂等）。因此允许的激进策略：
  - 全量 rebuild 代替增量同步（条目量级几百，秒级）
  - 查询时 dirty 自愈：文件计数/最大 mtime 与索引快照不一致 → 自动重建
  - 写路径挂点：write_gate 末尾 rebuild（每日）；inject hook 改状态后 sync_entry（单条）
失败语义：FTS5 不可用/库损坏 → 抛 IndexUnavailable，调用方回退旧子串路径
（fail-open，绝不阻塞宿主——与 agent-hooks 同纪律）。

时间衰减复用 eco_review 阈值（口径单源，设计稿 §O2）：≤30 天 ×1.0 / ≤90 天
×0.5 / >90 天 ×0.25；status=superseded/archived 不参与，dormant/stale ×0.5。
importance 权重：high ×1.2 / mid（默认）×1.0 / low ×0.8。

索引与事件表都落在生态既有 eco.db（gate_log 等日志表的延伸）；WAL + busy_timeout
支持「cron 门 + gateway hook」双进程写入。索引/事件表均为生成物：不进 git 快照、
不进发布物（发布器白名单制天然排除数据根）。
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import sys
import time
from pathlib import Path

from . import config
from . import memstore

SCHEMA_VERSION = 1
DECAY_FRESH_DAYS = 30    # eco_review episodic 阈值镜像
DECAY_MID_DAYS = 90      # eco_review semantic/procedural/lesson 阈值镜像
DECAY_FRESH = 1.0
DECAY_MID = 0.5
DECAY_OLD = 0.25
IMPORTANCE_WEIGHT = {"high": 1.2, "mid": 1.0, "low": 0.8}
DEFAULT_IMPORTANCE = "mid"
STATUS_WEIGHT = {"dormant": 0.5, "stale": 0.5}
STATUS_EXCLUDED = {"superseded", "archived"}
DIRTY_TTL_S = 5.0        # 进程内 dirty 检查节流（长驻 hook 热路径；CLI 单次进程无感）
MATCH_LIMIT = 200        # 单次查询最多取回的候选行（再由 Python 打分截断）

_DDL = (
    """CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
        slug UNINDEXED, store UNINDEXED, type UNINDEXED, status UNINDEXED,
        importance UNINDEXED, updated UNINDEXED,
        title, body, keywords,
        tokenize='trigram')""",
    "CREATE TABLE IF NOT EXISTS mem_fts_meta(key TEXT PRIMARY KEY, value TEXT)",
)
_DDL_INJECT_EVENTS = """CREATE TABLE IF NOT EXISTS inject_events(
    ts TEXT, kind TEXT, session TEXT, detail TEXT)"""

# 进程内 dirty 检查缓存：db 路径 → (monotonic 时刻, 结论)
# 键必须含库身份：同进程多沙盒（测试）/多实例各自独立，互不串缓存
_dirty_cache: dict[str, tuple[float, bool]] = {}


class IndexUnavailable(Exception):
    """FTS5 不可用或索引库损坏——调用方应回退旧子串路径。"""


def _fts5_ok() -> bool:
    c = None
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        return True
    except sqlite3.Error:
        return False
    finally:
        if c is not None:
            c.close()  # OCR 二审 2026-10-03：抛错路径也释放连接


def _connect(db: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db or config.db_path(), timeout=15)
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass  # WAL 不可用（如网络盘）→ 沿用默认日志模式，功能不受影响
    return conn


def _ensure(conn: sqlite3.Connection) -> None:
    for ddl in _DDL:
        try:
            conn.execute(ddl)
        except sqlite3.Error as e:
            raise IndexUnavailable(f"索引建表失败: {e}") from e


# ── 条目采集（真源 = Markdown 文件，解析单源 memstore）────────────────

def _store_dirs(root: Path | None = None, detail_dir: Path | None = None,
                exp_dir: Path | None = None) -> tuple[Path, Path]:
    r = Path(root) if root else config.hermes_root()
    return (Path(detail_dir) if detail_dir else r / "memories" / "detail",
            Path(exp_dir) if exp_dir else r / "experiences")


def _detail_rows(detail_dir: Path) -> list[tuple]:
    """memories/detail/*.md → FTS 行（slug, store, type, status, importance, updated, title, body, keywords）。"""
    rows = []
    for p in sorted(detail_dir.glob("*.md")):
        try:
            fm, body = memstore.parse_frontmatter(
                p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if not body:
            continue
        rows.append((
            p.stem, "detail", str(fm.get("type", "")),
            str(fm.get("status", "active")),
            str(fm.get("importance", DEFAULT_IMPORTANCE)),
            str(fm.get("last_seen") or fm.get("last_hit") or fm.get("first_seen")
                or fm.get("transaction_time") or ""),
            body[:80], body, str(fm.get("keywords", "")),
        ))
    return rows


def _exp_rows(exp_dir: Path) -> list[tuple]:
    """experiences/exp-*.md → FTS 行（正文五字段整体进 body，BM25 天然覆盖）。"""
    rows = []
    for p in sorted(exp_dir.glob("exp-*.md")):
        try:
            entry = memstore.parse_experience(
                p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if entry is None:
            continue
        meta, fields = entry["meta"], entry["fields"]
        rows.append((
            p.stem, "exp", str(meta.get("type", "")),
            str(meta.get("status", "draft")),
            str(meta.get("importance", DEFAULT_IMPORTANCE)),
            str(meta.get("last_hit") or meta.get("created") or ""),
            str(fields.get("title", ""))[:80], entry["full"], "",
        ))
    return rows


def _file_snapshot(detail_d: Path, exp_d: Path) -> tuple[int, int]:
    """与行采集同一 glob 口径的（文件数, 最大 mtime_ns）——dirty 比较基准。"""
    n, mt = 0, 0
    for d, pat in ((detail_d, "*.md"), (exp_d, "exp-*.md")):
        for p in d.glob(pat):
            n += 1
            try:
                mt = max(mt, p.stat().st_mtime_ns)
            except OSError:
                pass
    return n, mt


# ── 建库 / 重建 / 自愈 ────────────────────────────────────────────────

def _rebuild_conn(conn: sqlite3.Connection, detail_d: Path, exp_d: Path,
                  db: Path) -> dict:
    rows = _detail_rows(detail_d) + _exp_rows(exp_d)
    conn.execute("DELETE FROM mem_fts")
    conn.executemany(
        "INSERT INTO mem_fts(slug, store, type, status, importance, updated, "
        "title, body, keywords) VALUES(?,?,?,?,?,?,?,?,?)", rows)
    n, mt = _file_snapshot(detail_d, exp_d)
    conn.executemany(
        "INSERT OR REPLACE INTO mem_fts_meta(key, value) VALUES(?,?)",
        [("snapshot", json.dumps({"files": n, "mtime": mt})),
         ("built_at", datetime.datetime.now().isoformat(timespec="seconds")),
         ("schema_version", str(SCHEMA_VERSION))])
    conn.commit()
    _dirty_cache[str(db)] = (time.monotonic(), False)  # 刚重建完，TTL 内不再重查
    return {"detail": sum(1 for r in rows if r[1] == "detail"),
            "exp": sum(1 for r in rows if r[1] == "exp"),
            "files": n}


def rebuild(detail_dir: Path | None = None, exp_dir: Path | None = None,
            root: Path | None = None) -> dict:
    """全量重建（幂等：同内容两次重建行集一致）。返回 {"detail": n, "exp": m, "files": k}。

    root：数据根覆盖（测试/多实例）——同时决定库位置与默认目录派生。"""
    if not _fts5_ok():
        raise IndexUnavailable("当前 SQLite 无 FTS5/trigram")
    detail_d, exp_d = _store_dirs(root=root, detail_dir=detail_dir, exp_dir=exp_dir)
    db = (Path(root) / "eco.db") if root else config.db_path()
    conn = _connect(db)
    try:
        _ensure(conn)
        return _rebuild_conn(conn, detail_d, exp_d, db)
    finally:
        conn.close()


def _dirty(conn: sqlite3.Connection, detail_d: Path, exp_d: Path,
           db: Path) -> bool:
    global _dirty_cache
    now = time.monotonic()
    cached = _dirty_cache.get(str(db))
    if cached is not None and (now - cached[0]) < DIRTY_TTL_S:
        return cached[1]
    try:
        vrow = conn.execute(
            "SELECT value FROM mem_fts_meta WHERE key='schema_version'").fetchone()
        srow = conn.execute(
            "SELECT value FROM mem_fts_meta WHERE key='snapshot'").fetchone()
    except sqlite3.Error:
        result = True
    else:
        if (vrow is None or srow is None
                or vrow[0] != str(SCHEMA_VERSION)):
            result = True  # 无快照/旧 schema 版本 → 重建（版本号从死字段变成判据）
        else:
            try:
                n, mt = _file_snapshot(detail_d, exp_d)
                result = json.loads(srow[0]) != {"files": n, "mtime": mt}
            except (ValueError, TypeError):
                result = True
    _dirty_cache[str(db)] = (now, result)
    return result


# ── 检索 ──────────────────────────────────────────────────────────────

def _days_since(dateish: str) -> int | None:
    """'2026-09-30' 或 ISO 时间戳 → 距今天数；解析失败 None（fail-open 不衰减）。"""
    s = (dateish or "").strip()[:10]
    try:
        d = datetime.date.fromisoformat(s)
    except ValueError:
        return None
    return (datetime.date.today() - d).days


def search(keyword: str, *, stores: tuple = ("exp",), top: int = 20,
           detail_dir: Path | None = None, exp_dir: Path | None = None,
           root: Path | None = None) -> list[dict]:
    """BM25 检索：score = bm25 相对质量 × 时间衰减 × importance/status 权重。

    返回 [{slug, store, type, status, score}]（score 降序）。
    约定：keyword 归一后 <3 字符（trigram 下限）或零命中 → 返回 []，
    由调用方回退旧子串路径保证召回；索引不可用/库损坏/建表失败等情况
    可能抛 sqlite3.Error 或 IndexUnavailable——调用方须整体 try/except 回退
    （eco_note_query.search 即此用法）。"""
    kw = (keyword or "").strip().lower()
    if len(kw) < 3 or not stores:
        return []  # 短词（trigram 下限）/空 store 集合 → 调用方回退旧路径
    detail_d, exp_d = _store_dirs(root=root, detail_dir=detail_dir, exp_dir=exp_dir)
    db = (Path(root) / "eco.db") if root else config.db_path()
    conn = _connect(db)
    try:
        _ensure(conn)
        if _dirty(conn, detail_d, exp_d, db):
            _rebuild_conn(conn, detail_d, exp_d, db)
        q = '"' + kw.replace('"', " ") + '"'
        rows = conn.execute(
            "SELECT slug, store, type, status, importance, updated, "
            f"bm25(mem_fts) AS b FROM mem_fts WHERE mem_fts MATCH ? "
            f"AND store IN ({','.join('?' * len(stores))}) LIMIT ?",
            (q, *stores, MATCH_LIMIT),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    # 先剔除排除态再算质量分布（OCR 二审 2026-10-03：被排除行会拉大 span、
    # 压缩剩余条目的 quality 区分度）
    rows = [r for r in rows if r[3] not in STATUS_EXCLUDED]
    if not rows:
        return []
    best = min(r[6] for r in rows)   # FTS5 约定：bm25 越小（越负）匹配越好
    worst = max(r[6] for r in rows)
    span = (worst - best) or 1.0
    out = []
    for slug, store, typ, status, imp, updated, b in rows:
        w = IMPORTANCE_WEIGHT.get(imp, 1.0) * STATUS_WEIGHT.get(status, 1.0)
        quality = 1.0 - (b - best) / span       # 1.0 = 本查询最佳匹配
        days = _days_since(updated)
        decay = (DECAY_FRESH if days is None or days <= DECAY_FRESH_DAYS
                 else DECAY_MID if days <= DECAY_MID_DAYS else DECAY_OLD)
        out.append({"slug": slug, "store": store, "type": typ, "status": status,
                    "score": round(w * decay * quality, 4)})
    out.sort(key=lambda r: -r["score"])
    return out[:top]


def sync_entry(path: Path, store: str, root: Path | None = None) -> bool:
    """单条重索引（inject hook 改状态后调用）。索引不存在/任何失败 → False（不建库、不抛）。"""
    db = (Path(root) / "eco.db") if root else config.db_path()
    conn = None
    try:
        conn = _connect(db)
        conn.execute("SELECT 1 FROM mem_fts LIMIT 1")
    except sqlite3.Error:
        if conn is not None:
            conn.close()
        return False
    try:
        slug = path.stem
        conn.execute("DELETE FROM mem_fts WHERE slug=? AND store=?", (slug, store))
        rows = (_detail_rows(path.parent) if store == "detail"
                else _exp_rows(path.parent))
        row = next((r for r in rows if r[0] == slug), None)
        if row:
            conn.execute(
                "INSERT INTO mem_fts(slug, store, type, status, importance, "
                "updated, title, body, keywords) VALUES(?,?,?,?,?,?,?,?,?)", row)
        # OCR 复核 2026-10-03（high）：快照必须按**同一对目录**计算——原推导
        # root_r 层级错误导致恒为 {files:0,mtime:0} 伪快照，下次 search 必误判脏。
        # 直接复用 _store_dirs（与 rebuild/search 完全同源）。
        det_d, exp_d = _store_dirs(root=root)
        n, mt = _file_snapshot(det_d, exp_d)
        conn.execute("INSERT OR REPLACE INTO mem_fts_meta(key, value) VALUES('snapshot', ?)",
                     (json.dumps({"files": n, "mtime": mt}),))
        conn.commit()
        _dirty_cache[str(db)] = (time.monotonic(), False)
        return True
    except Exception as e:
        # fail-open 已由文档明确；留 stderr 信号保可观测（不影响调用方主流程）
        print(f"⚠️ sqlindex.sync_entry 失败: {type(e).__name__}: {e}",
              file=sys.stderr)
        return False
    finally:
        conn.close()


# ── O7b 事件账 + O7c 体检面 ───────────────────────────────────────────

def log_event(kind: str, session: str, detail: dict,
              root: Path | None = None) -> bool:
    """注入/命中事件 SQL 镜像账（jsonl 仍是主账；SQL 供记分卡与观测舱聚合）。

    root：数据根覆盖（inject 测试沙盒用）。失败静默 False——绝不影响注入主流程。"""
    try:
        db = (Path(root) / "eco.db") if root else config.db_path()
        conn = _connect(db)
        try:
            # 只建事件表——绝不顺手建 FTS 索引（OCR 2026-10-03：事件-only 库
            # 不应带出空索引，防下游「有库必有索引快照」假设被打破）
            conn.execute(_DDL_INJECT_EVENTS)
            conn.execute(
                "INSERT INTO inject_events(ts, kind, session, detail) VALUES(?,?,?,?)",
                (datetime.datetime.now().isoformat(timespec="seconds"),
                 kind, session, json.dumps(detail, ensure_ascii=False)))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def consistency(detail_dir: Path | None = None,
                exp_dir: Path | None = None,
                root: Path | None = None) -> dict:
    """O7c 体检面（只读，不重建）：索引在不在、与文件快照是否一致。"""
    detail_d, exp_d = _store_dirs(root=root, detail_dir=detail_dir, exp_dir=exp_dir)
    db = (Path(root) / "eco.db") if root else config.db_path()
    out = {"present": False, "dirty": False, "index_rows": 0,
           "file_count": 0, "built_at": ""}
    try:
        conn = _connect(db)
    except sqlite3.Error:
        return out
    try:
        row = conn.execute(
            "SELECT value FROM mem_fts_meta WHERE key='snapshot'").fetchone()
        if row is None:
            return out
        built = conn.execute(
            "SELECT value FROM mem_fts_meta WHERE key='built_at'").fetchone()
        out["built_at"] = built[0] if built else ""
        out["index_rows"] = conn.execute(
            "SELECT count(*) FROM mem_fts").fetchone()[0]
        out["present"] = True
    except sqlite3.Error:
        out["dirty"] = True  # 表残缺（有 meta 无 fts 等）按不一致报告
        return out
    finally:
        conn.close()
    n, mt = _file_snapshot(detail_d, exp_d)
    out["file_count"] = n
    try:
        out["dirty"] = json.loads(row[0]) != {"files": n, "mtime": mt}
    except (ValueError, TypeError):
        out["dirty"] = True
    return out
