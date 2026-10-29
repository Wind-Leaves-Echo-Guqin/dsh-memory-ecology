"""store —— L0 数据面的唯一读写层（frontmatter / L1 / L2 detail / 候选）。

**为什么要有这一层**：旧项目里 `parse_frontmatter` / `load_detail` / `parse_candidates`
分散在 write_gate / eco_quota / collector / eco_note_query 四处各自实现，格式一改就要
改四处（"双树漂移"的老病）。v3.2.0 收编到本模块：**改格式只改这里**。

数据根布局（v3.2.0）：

    <root>/
      .eco/root.json              {root_id, schema_version, created_at}
      .eco/catalog/<scope>.json   物化视图（插件热路径只读它）
      .eco/costs.json             成本快照（GUI G6 只读它）
      memories/MEMORY.md          L1 主记忆（§ 分隔）
      memories/USER.md            L1 画像
      memories/detail/<id>.md     L2，一条一个文件
      memories/pending/<scope>/*  候选（圈由目录天然表达）
      memories/pending/archive/   已消费候选
      memories/quarantine/        隔离区（**永不物理删除**）
      memories/user_candidates/   门③画像候选
      packs/                      只读记忆包挂载点（v3.2.0 起）
      eco.db                      账本（gate_log / quota_log / distill_log /
                                  review_log / inject / hit / event / cost）

状态机（两轴，取自既有库词汇，**不许自造同义词**）：
    status（活性）：active → dormant → archived
    fate（存续）：retained → superseded / merged / archived
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import paths

# ── 词汇表（对齐既有库，防"挂载旧库时每条退化成同一类"）──────────────

TYPES = ("semantic", "episodic", "procedural", "lesson", "map")
TYPE_ALIASES = {
    "fact": "semantic", "note": "semantic", "summary": "semantic",
    "knowledge": "semantic", "preference": "semantic",
    "decision": "procedural", "howto": "procedural", "process": "procedural",
    "event": "episodic", "log": "episodic", "incident": "episodic",
    "error": "lesson", "pattern": "lesson", "pitfall": "lesson",
    "success": "lesson", "negative": "lesson",
}

STATUSES = ("active", "dormant", "archived")
FATES = ("retained", "superseded", "merged", "archived")

# detail 正文上限（默认）。**必须在文件前部**：`candidate_fp` 拿它当默认参数值，
# 定义在后面会在 import 期抛 NameError（实测踩到过）。
BODY_MAX = 800

# L1 配额（与既有 metrics 常量同口径；改这里即改全系统）
MEMORY_QUOTA = 3000
USER_QUOTA = 1500
QUOTA_RATIO = 0.85
MEMORY_TRIGGER = int(MEMORY_QUOTA * QUOTA_RATIO)   # 2550
USER_TRIGGER = int(USER_QUOTA * QUOTA_RATIO)       # 1275


def norm_type(raw: str) -> str:
    """类型归一：别名 → 既有词汇；未知一律落 semantic（不丢条目、不造新词）。"""
    k = str(raw or "").strip().lower()
    k = TYPE_ALIASES.get(k, k)
    return k if k in TYPES else "semantic"


def norm_scope(raw: str | None) -> str:
    """圈名归一：小写、安全字符、≤40；空/非法 → global。与插件侧 JS 同口径。"""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw or "").strip().lower()).strip("-._")
    return s[:40] or "global"


# ── frontmatter ──────────────────────────────────────────────────────

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """宽松版：无 frontmatter → ({}, 全文)。兼容 BOM / CRLF。"""
    if text.startswith("\ufeff"):
        text = text[1:]
    fm: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            block = text[3:end].strip()
            body = text[end + 4:].strip()
            for line in block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip()
    return fm, body


def clean_value(value: str) -> str:
    """极简 YAML 标量清理：去尾注释、剥引号。"""
    s = (value or "").strip()
    idx = s.find(" #")
    if idx != -1:
        s = s[:idx].strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1]
    return s.strip()


def parse_frontmatter_strict(text: str) -> tuple[dict | None, str]:
    """严格版：无/坏 frontmatter → (None, 全文)。

    存在的理由是**门④要区分"无 frontmatter"与"字段为空"**，宽松版表达不了。
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n").strip() != "---":
        return None, text
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n").strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, text
    fields: dict = {}
    for ln in lines[1:end_idx]:
        s = ln.rstrip("\r\n").strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        key, _, val = s.partition(":")
        fields[key.strip()] = clean_value(val)
    body = "".join(lines[end_idx + 1:]).strip()
    return fields, body


def dump_frontmatter(fm: dict, body: str) -> str:
    lines = ["---"] + ["%s: %s" % (k, v) for k, v in fm.items()] + ["---", ""]
    return "\n".join(lines) + str(body).strip() + "\n"


# ── 损坏判定（H2：解析失败必须隔离，不能当成"全局"）─────────────────────
#
# 为什么需要它：宽松版 `parse_frontmatter` 对坏 frontmatter 返回 `({}, 全文)`，
# 于是 detail 的 scope 回落到 `"global"`——**最宽的取值**。一个字节的损坏
# （比如把结束分隔符 `---` 写成 `--x`）就能把一条圈内机密单向放宽成全局可见；
# 紧接着门④ 又拿"全文"当正文回写，落盘变成两个 frontmatter 块、`status`
# 同时出现两个互相矛盾的值（实测复现）。
#
# 判定口径刻意保守：**只认"明确写了 frontmatter 但坏了"**。完全没有 frontmatter
# 的老文件（`nofm.md`）不算损坏——那是既有合法形态，见
# `tests/test_compat.py::test_missing_frontmatter_is_not_fatal`。

def has_frontmatter_block(text: str) -> bool:
    """文件是否**看起来想写** frontmatter：首行非空行以 `---` 开头。"""
    if text.startswith("\ufeff"):
        text = text[1:]
    for line in text.splitlines():
        if not line.strip():
            continue
        return line.strip().startswith("---")
    return False


def damage_reason(text: str) -> str:
    """→ "" 表示正常；否则是损坏原因（供 health / quarantine 用）。"""
    if text.startswith("\ufeff"):
        text = text[1:]
    if not has_frontmatter_block(text):
        return ""
    # 严格版要求"第一行**恰好是** `---`"，宽松版的 startswith("---") 会把
    # `----` / `---x` 也吞掉，导致"看起来没坏其实没解析"。
    first = ""
    for line in text.splitlines():
        if line.strip():
            first = line.rstrip("\r\n").strip()
            break
    if first != "---":
        return "frontmatter 起始行不是独立分隔符：%r" % first[:40]
    fields, _body = parse_frontmatter_strict(text)
    if fields is None:
        return "frontmatter 缺结束分隔符"
    if not any(k == "type" for k in fields):
        # 有块但一个字段都没解析出来（例如全角冒号/缩进错位）→ 不可信
        if not fields:
            return "frontmatter 块内无可解析字段"
    for dup in ("scope", "status", "type", "id"):
        if sum(1 for ln in text.splitlines()
               if ln.split(":", 1)[0].strip() == dup and ":" in ln
               and ln.strip() and not ln.strip().startswith("#")) > 1 \
                and _count_fm_blocks(text) > 1:
            return "存在多个 frontmatter 块（%s 出现多值）" % dup
    return ""


def _count_fm_blocks(text: str) -> int:
    """独立成行的 `---` 个数 → 近似 frontmatter 块数（2 个分隔符 = 1 块）。"""
    n = 0
    for line in text.splitlines():
        if line.strip() == "---":
            n += 1
    return n // 2


# ── L1（§ 分隔）───────────────────────────────────────────────────────

_SECTION_RE = re.compile(r"(?m)^[ \t]*§[ \t]*\r?$")


def parse_l1(raw: str) -> tuple[list[str], str]:
    """→ (条目列表, 换行风格)。分隔符 = 单独一行的 §（LF/CRLF 自动识别）。"""
    nl = "\r\n" if "\r\n" in raw else "\n"
    parts = _SECTION_RE.split(raw or "")
    return [p.strip() for p in parts if p.strip()], nl


def chars_of(entries) -> int:
    """条目的字符数：`\\n§\\n` 连接 + 末尾换行；内部 CRLF 归一为 LF 计 1。

    **跳过 None**：挤出过程中会用 `cur[idx] = None` 标记"已移出"，如果这里不跳过，
    一次排版操作就能让整门崩在 `NoneType.replace`（实测踩到过）。
    """
    real = [e for e in entries if e]
    if not real:
        return 0
    return len("\n§\n".join(e.replace("\r\n", "\n") for e in real) + "\n")


def serialize_l1(entries, nl: str = "\n") -> str:
    if not entries:
        return ""
    return (nl + "§" + nl).join(entries) + nl


# ── L2 detail ────────────────────────────────────────────────────────

def load_detail(detail_dir: Path) -> list[dict]:
    """读 detail 目录 → [{path, name, fm, body}]。空正文跳过（不是错误）。

    **损坏的文件不进返回值**（H2）：解析失败时宽松版会给出 `fm={}`，
    而下游把缺失的 `scope` 归一成 `"global"`——损坏于是把"隔离"单向放宽成
    "全局可见"。这里直接把它们排除在目录/检索/门的视野之外，
    只通过 `scan_damaged()` 暴露成可诊断的清单（"不可见但可查"）。
    """
    items = []
    d = Path(detail_dir)
    if not d.is_dir():
        return items
    for f in sorted(d.glob("*.md")):
        text = paths.read_text(f)
        fm, body = parse_frontmatter(text)
        if not body:
            continue
        if damage_reason(text):
            # 已损坏：不参与任何"可见性"计算。修法不是猜它的圈，而是如实报告。
            continue
        items.append({
            "path": f, "name": f.stem, "fm": fm, "body": body,
            "type": norm_type(fm.get("type", "")),
            "status": (fm.get("status", "") or "active").strip().lower(),
            "scope": norm_scope(fm.get("scope")) if fm.get("scope") else "global",
        })
    return items


def scan_damaged(detail_dir: Path) -> list[dict]:
    """损坏的 detail 清单（**只诊断，不猜归属**）。"""
    out = []
    d = Path(detail_dir)
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.md")):
        try:
            text = paths.read_text(f)
        except OSError as exc:
            out.append({"id": f.stem, "path": str(f), "why": "读不了：%s" % str(exc)[:80]})
            continue
        why = damage_reason(text)
        if why:
            out.append({"id": f.stem, "path": str(f), "why": why})
    return out


def detail_prefixes(items: list[dict], n: int = 30) -> dict:
    """detail → {slug: 规范化前 N 字符}；**只有 active 参与**同源判定
    （dormant/superseded 参与会把已降级的条目当成"L1 已有"，从而漏提升）。"""
    return {d["name"]: paths.norm_key(d["body"])[:n]
            for d in items
            if d.get("status") == "active" and d.get("body")}


def is_same_source(detail_body: str, entry_text: str, n: int = 30) -> bool:
    """同源判定：detail 正文规范化前 N 字符被 L1 条目**包含**（单向）。

    单向是刻意的：双向会把「L1 条目是 detail 前缀」误判同源（旧实现在
    2026-10-04 已收紧，这里保持同口径）。
    """
    nd = paths.norm_key(detail_body)[:n]
    ne = paths.norm_key(entry_text)
    return bool(nd) and bool(ne) and nd in ne


# ── 候选（pending）───────────────────────────────────────────────────

PENDING_SKIP_NAMES = ("README.md", ".watermark", "index.json")
PENDING_SKIP_SUFFIXES = (".done.md", ".rejected.md")

_CAND_RE = re.compile(r"^\s*-\s*\[([^\]]+)\]\s*(.+?)\s*$")
_CHECKBOX_RE = re.compile(r"^\s*-\s*\[\s*[xX]?\s*\]\s+")


def is_pending_active(name: str) -> bool:
    return name not in PENDING_SKIP_NAMES and not name.endswith(PENDING_SKIP_SUFFIXES)


def iter_candidates(pending_dir: Path, scope: str | None = None):
    """产出候选行 (file, lineno, type, text, scope, session)。

    **圈由目录天然表达**：`pending/<scope>/*.md`，顶层文件 = global。
    这样不需要改候选行格式，也不需要额外索引（A2A §5 理论二）。

    `session` 从**文件名**推断（`cand-20261027-<session>.md`）——这是 deposit 的
    命名契约。它是 `session_count` / 交叉印证的唯一来源；拿不到就返回空串
    （匿名投递不算"一次会话"，不能凭空增加 count）。
    """
    p = Path(pending_dir)
    if not p.is_dir():
        return
    for f in sorted(p.rglob("*.md")):
        try:
            rel = f.relative_to(p)
        except ValueError:
            continue
        if "archive" in rel.parts or "quarantine" in rel.parts:
            continue
        if not is_pending_active(f.name):
            continue
        sc = norm_scope(rel.parts[0]) if len(rel.parts) > 1 else "global"
        if scope and sc not in (scope, "global"):
            continue
        sess = session_of_file(f)
        for i, raw in enumerate(paths.read_text(f).splitlines(), 1):
            line = _CHECKBOX_RE.sub("- ", raw)   # 剥上游泄漏的 checkbox 前缀
            m = _CAND_RE.match(line)
            if not m:
                continue
            ctype = m.group(1).strip()
            text = m.group(2).strip()
            if not text:
                continue
            yield f, i, norm_type(ctype), text, sc, sess


_SESS_RE = re.compile(r"^cand-\d{8}-(.+?)\.md(?:\.\d{4,})?\.done\.md$")


def session_of_file(f: Path) -> str:
    """`cand-YYYYMMDD-<session>.md` → session（不含扩展名与 `.done` 后缀）。"""
    name = Path(f).name
    stem = name
    for suf in (".done.md", ".rejected.md"):
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
            break
    if stem.endswith(".md"):
        stem = stem[:-3]
    parts = stem.split("-")
    # cand-YYYYMMDD-<session...>；session 里可能有 '-'（demo 模式）
    if len(parts) >= 3 and parts[0] == "cand" and parts[1].isdigit() and len(parts[1]) == 8:
        return "-".join(parts[2:])
    return ""


def candidate_lines(f: Path) -> list[str]:
    """候选文件里的候选正文列表（剥 checkbox 前缀后的 group(2)）。

    **判重与"是否消费"必须共用这一个口径**：`iter_candidates`（整合时判重）
    与 `unconsumed_in_file`（判文件消费完没有）各自拼正则，一旦有偏差就会出现
    "整合了但文件永远判未消费"（pending 无限堆积）或反过来"候选静默丢失"。
    """
    out = []
    try:
        text = paths.read_text(f)
    except OSError:
        return out
    for raw in text.splitlines():
        m = _CAND_RE.match(_CHECKBOX_RE.sub("- ", raw))
        if m:
            out.append(m.group(2))
    return out


def candidate_fp(scope: str, text: str, body_max: int = BODY_MAX) -> tuple[str, str]:
    """候选指纹的**唯一实现**。→ (指纹, 归一后的正文)

    为什么必须单源：指纹在三处被用到（整合时判重、`unconsumed_in_file` 判"这个文件
    消费完没有"、老条目的 fallback 指纹），三处口径一旦不同就有两种恶果：
      · 文件永远判"未消费" → pending 无限堆积（实测：配置把 body_max 调小后复现）；
      · 反过来把未整合的候选当已消费 → 候选静默丢失。
    `scope` **必须参与**：同一句话在两个项目里是两条独立记忆（旧实现跨圈合并过）。
    """
    body = paths.norm_text(text)[:body_max]
    return paths.fingerprint("cand", scope, body), body


def unconsumed_in_file(f: Path, known: set, body_max: int = BODY_MAX,
                       pending_root: Path | None = None) -> int:
    """该候选文件里尚未被整合的条目数（与 integrate 同口径，**含 scope**）。

    `known` 必须是**整合结束后的**指纹集合：其中要包含
    · detail/candidate 里已有的指纹（按 `it["fm"]["fp"]`，而不是重新算正文指纹）
    · 本轮 `merged` 分支命中的候选指纹
    否则相似合并掉的文件会被判成"未消费"，pending 永远不空（实测复现）。
    """
    scope = scope_of_file(f, pending_root)
    n = 0
    for text in candidate_lines(f):
        fp, _ = candidate_fp(scope, text, body_max)
        if fp not in known:
            n += 1
    return n


def scope_of_file(f: Path, pending_root: Path | None = None) -> str:
    """候选文件 → 圈名。`pending/<scope>/x.md` → scope；直接放 `pending/` → global。

    **必须显式传 pending 根**（不要从路径反推）：圈名可以合法地叫 `pending`
    （`norm_scope` 允许），那种情况下任何"往上找 pending 目录名"的启发式都会算错，
    于是指纹对不上、文件永远不被消费（实测复现过）。
    """
    f = Path(f)
    if pending_root is None:
        return "global"
    try:
        rel = f.relative_to(Path(pending_root))
    except ValueError:
        return "global"
    return norm_scope(rel.parts[0]) if len(rel.parts) > 1 else "global"


def mark_consumed(pending_dir: Path, only: set[str] | None = None) -> int:
    """消费后改名 `.done.md`。`only=None` 消费全部。

    **`only` 必须是"相对 pending 的 posix 路径"集合**（不是文件名）。
    只按文件名匹配会串圈：`pending/a/cand-X.md` 与 `pending/proj-beta/cand-X.md`
    同名，只整合了前者却把后者也改名 → **后者永远不再参与整合**（实测：返回
    `new=1 consumed_files=2`，另一圈那条从未入库却已成 .done）。
    截断保护也要靠它（只消费本轮真正整合完的文件）。
    """
    n = 0
    p = Path(pending_dir)
    if not p.is_dir():
        return 0
    for f in sorted(p.rglob("*.md")):
        rel = f.relative_to(p)
        if "archive" in rel.parts or "quarantine" in rel.parts:
            continue
        if not is_pending_active(f.name):
            continue
        if only is not None and rel.as_posix() not in only:
            continue
        target = f.with_name(f.name + ".done.md")
        if target.exists():
            target = f.with_name("%s.%s.done.md" % (f.name, paths.stamp_ms()))
        try:
            f.rename(target)
            n += 1
        except OSError:
            continue
    return n




# ── 圈配置（circle.json）──────────────────────────────────────────────

CIRCLE_DEFAULT_BUDGET = 600
CIRCLE_DEFAULT_LIMIT = 20
CIRCLE_SCHEMA = 1


def circle_file(root: Path) -> Path:
    return Path(root) / "circles.json"


def load_circles(root: Path) -> dict:
    """读 `<root>/circles.json`；缺失/损坏 → 空配置（fail-open，不抛）。"""
    p = circle_file(root)
    if not p.is_file():
        return {"schema_version": CIRCLE_SCHEMA, "global_budget_chars": CIRCLE_DEFAULT_BUDGET,
                "circles": []}
    try:
        data = json.loads(paths.read_text(p) or "{}")
    except (ValueError, TypeError):
        return {"schema_version": CIRCLE_SCHEMA, "global_budget_chars": CIRCLE_DEFAULT_BUDGET,
                "circles": [], "error": "corrupt"}
    if not isinstance(data, dict):
        return {"schema_version": CIRCLE_SCHEMA, "circles": []}
    data.setdefault("schema_version", CIRCLE_SCHEMA)
    data.setdefault("global_budget_chars", CIRCLE_DEFAULT_BUDGET)
    circles = data.get("circles")
    data["circles"] = circles if isinstance(circles, list) else []
    return data


def save_circles(root: Path, data: dict) -> Path:
    """写 circles.json（唯一写入口；GUI 也必须经核心 CLI 走这里）。

    写前备份 + 原子写 + schema 归一，**并且先校验**：任何非法项整份拒绝，
    不写入"半对"的配置（否则下次启动路由全错）。
    """
    errs = validate_circles(data)
    if errs:
        raise ValueError("; ".join(errs))
    out = {
        "schema_version": CIRCLE_SCHEMA,
        "global_budget_chars": int(data.get("global_budget_chars", CIRCLE_DEFAULT_BUDGET)),
        "circles": [],
    }
    for c in data.get("circles", []):
        out["circles"].append({
            "id": norm_scope(c.get("id")),
            "display_name": str(c.get("display_name") or c.get("id") or "")[:80],
            "match": {"cwd_prefix": [str(x) for x in (c.get("match", {}) or {}).get("cwd_prefix", [])]},
            "budget_chars": int(c.get("budget_chars") or CIRCLE_DEFAULT_BUDGET),
            "catalog_limit": int(c.get("catalog_limit") or CIRCLE_DEFAULT_LIMIT),
            "readonly": bool(c.get("readonly", False)),
            # 3.3.0 的只读挂载源声明（本轮只存不用，A2A 决策 D1）
            "sources": [str(x) for x in (c.get("sources") or [])],
        })
    p = circle_file(root)
    if p.is_file():
        paths.backup_before_write(p, tag="改圈前")
    paths.write_atomic(p, json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return p


def validate_circles(data: dict) -> list[str]:
    """校验圈配置。返回错误列表（空 = 合法）。"""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["circles.json 必须是对象"]
    try:
        budget = int(data.get("global_budget_chars", CIRCLE_DEFAULT_BUDGET))
        if budget < 60 or budget > 20000:
            errs.append("global_budget_chars 须在 60..20000")
    except (TypeError, ValueError):
        errs.append("global_budget_chars 必须是整数")
    circles = data.get("circles", [])
    if not isinstance(circles, list):
        return errs + ["circles 必须是数组"]
    seen = set()
    for i, c in enumerate(circles):
        if not isinstance(c, dict):
            errs.append("circles[%d] 不是对象" % i)
            continue
        cid = norm_scope(c.get("id"))
        if cid == "global":
            errs.append("circles[%d] id 不能是 global（全局圈是隐含的）" % i)
        if cid in seen:
            errs.append("circles[%d] id 重复：%s" % (i, cid))
        seen.add(cid)
        prefixes = (c.get("match") or {}).get("cwd_prefix")
        if prefixes is not None:
            if not isinstance(prefixes, list):
                errs.append("circles[%d].match.cwd_prefix 必须是数组" % i)
            else:
                for pre in prefixes:
                    if not isinstance(pre, str) or not pre.strip():
                        errs.append("circles[%d] cwd_prefix 含空项" % i)
        try:
            b = int(c.get("budget_chars") or CIRCLE_DEFAULT_BUDGET)
            if b < 60 or b > 20000:
                errs.append("circles[%d].budget_chars 须在 60..20000" % i)
        except (TypeError, ValueError):
            errs.append("circles[%d].budget_chars 必须是整数" % i)
    return errs


def route_circle(root: Path, cwd: str | None) -> str:
    """cwd → 圈名。**前缀最长匹配**（最近祖先圈）；都不中 → global。

    前缀比较前把分隔符统一成 `/`、去尾分隔符、Windows 下忽略大小写
    （`D:\\Work` 与 `d:\\work` 必须是同一个圈，否则同一项目会因为 shell 起法不同
     分裂成两个圈——这是实测踩过的）。
    """
    data = load_circles(root)
    target = _norm_cwd(cwd)
    best, best_len = "global", -1
    for c in data.get("circles", []):
        for pre in (c.get("match", {}) or {}).get("cwd_prefix", []) or []:
            p = _norm_cwd(pre)
            if not p:
                continue
            if target == p or target.startswith(p.rstrip("/") + "/"):
                if len(p) > best_len:
                    best, best_len = norm_scope(c.get("id")), len(p)
    return best


def _norm_cwd(value: str | None) -> str:
    s = str(value or "").strip().replace("\\", "/")
    s = re.sub(r"/+", "/", s)
    if len(s) > 1:
        s = s.rstrip("/")
    return s.lower() if _is_windows() else s


def _is_windows() -> bool:
    import os
    return os.name == "nt"


def circle_budget(root: Path, scope: str) -> tuple[int, int]:
    """→ (字符预算, 条数上限)。圈自己没配就吃 global_budget_chars + 默认条数。"""
    data = load_circles(root)
    for c in data.get("circles", []):
        if norm_scope(c.get("id")) == scope:
            return (int(c.get("budget_chars") or data.get("global_budget_chars",
                                                         CIRCLE_DEFAULT_BUDGET)),
                    int(c.get("catalog_limit") or CIRCLE_DEFAULT_LIMIT))
    return int(data.get("global_budget_chars", CIRCLE_DEFAULT_BUDGET)), CIRCLE_DEFAULT_LIMIT


def circle_readonly(root: Path, scope: str) -> bool:
    for c in load_circles(root).get("circles", []):
        if norm_scope(c.get("id")) == scope:
            return bool(c.get("readonly", False))
    return False

