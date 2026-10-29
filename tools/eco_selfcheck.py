#!/usr/bin/env python3
"""eco_selfcheck — 记忆生态「自观测门」：四条断言（v3.0.0a1 起 A1-A4）。

定位：**不是第六道业务门**。它不治理用户记忆，只治理"系统说真话的能力"。
断言对应历次独立审计里最危险发现，判据全部从文件/数据算出来，
不需要人读代码 —— 审计里人工做的每一步（比三棵树、数截断比例、对账 INDEX、
对账挤出账）在这里固化成脚本，于是它自动属于项目，不再需要"入库"这个动作。

  A1 部署一致性  生产 scripts/*.py + scripts/lib/*.py 与部署源逐文件 SHA256
                 （LF 归一）相等                 → 抓 P0-1「修了没上 / 半量部署」
  A2 沉默截断    detail 正文长度正好落在某个已知上限上、不以终止标点结尾、
                 没有 …[truncated]/truncated_suspected 标记、**且有硬证据**的篇数 == 0
                                                 → 抓 P0-2「200 字硬截断」
  A3 索引一致性  INDEX.md 里的 exp-<日期>-<序号> 集合 == exp-*.md 文件集合
                                                 → 抓 P1-5「索引漂移」
  A4 挤出账实一致 detail 中 origin 含 extrude 的条目 ⊆ gate_log EXTRUDE 行的 target
                                                 → 抓 P0-3「门② 直写无账」（v3.0.0a1）

为什么 A2 不用审计原文的"正文以终止标点结尾的比例 == 100%"：
实测 96/162=59%，其中 74 篇是正常无标点的碎片、不是截断。该判据**永远无法变绿**
（历史正文已丢、补不回来），必然演变成告警疲劳 —— 而告警疲劳本身就是 P1-1
"面板全绿"的另一面。A2 只抓"卡在已知上限且没有标记、且有硬证据"的截断签名
（实测 22 篇），配 `--repair` 一次性补 frontmatter 标记后即可归零并长期有效；
证据不足的只进 `suspects` 报告，绝不据此改写正文。

设计约束：
  · 只读检查，除 `--repair` 与报告/账本外不写任何东西
  · 必须能在**部署前**（旧生产代码）跑起来 —— 因此不 import 生产的 lib
  · 上限解析顺序与 lib/config.gate_max_body 一致（env > configs/eco.toml > 默认），
    但这里自己解析生产那份 eco.toml，不依赖生产已升级
  · SQLite 检查段一律 ?mode=ro（防 WAL 副作用污染数据根）

用法:
  python tools/eco_selfcheck.py              # 四条断言，人读输出
  python tools/eco_selfcheck.py --json       # 机器读
  python tools/eco_selfcheck.py --no-write   # 只读（不写报告/账本）
  python tools/eco_selfcheck.py --repair     # 给有硬证据的存量截断补 frontmatter 标记（先备份，不改正文）
  python tools/eco_selfcheck.py --only deploy,index --dev <部署源> --prod <数据根>

退出码: 0 三条全绿 / 1 有红 / 2 自检自身出错（路径不对、解析炸了）
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

TRUNC_MARK = "…[truncated]"          # 与 lib/config.TRUNC_MARK 一致
DEFAULT_MAX_BODY = 200
LEGACY_LIMITS = (200, 220)           # 历史上出现过的 hardcoded 上限（write_gate 200 / eco_quota 220）
TERMINAL = re.compile(r'[。！？.!?）)】\]"”]$')
EXP_ID = re.compile(r"exp-\d{8}-\d{4}")
REPORT_PREFIX = "eco_selfcheck"

# A2 的"硬证据"阈值（R-4）：
#   · 单篇正文长度恰为上限且无终止标点，只是**可疑**，不是截断（96/162 正常碎片里
#     有 74 篇就是这种形态）。只有能对上的外部证据才允许写标记。
#   · v2.5.6 上线日（2026-10-23）之前落盘、且长度恰为历史硬编码 200/220：旧版
#     write_gate 只可能这么截，属于历史遗留的硬证据。
#   · pending/*.done.md 里能找到"正文是该候选的前缀、且候选更长"：截断的原始候选还在。
#   · 批量签名：同一批 detail 里 ≥20 篇同时卡在已知上限且无终止标点。单篇可能是
#     巧合，整批同时卡上限是 v2.5.6 之前全局硬截断的语料级证据（生产实测 22 篇）。
_LEGACY_MTIME_CUTOFF = datetime.datetime(2026, 10, 23)
BULK_EVIDENCE_MIN = 20
MARK_FIELD = "truncated_suspected"


# ── 基础设施 ────────────────────────────────────────────────────────────────

def sha256_norm(path: Path) -> str:
    """SHA256（CRLF→LF 归一）。跨树比对必须归一：dev 工作树 CRLF、生产 LF。"""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def effective_max_body(prod: Path, default: int = DEFAULT_MAX_BODY) -> int:
    """生产侧生效的正文上限（与 lib/config.gate_max_body 同序，但独立解析）。

    自检要在部署前就能跑，所以不能 import 生产的 lib（那时还没有这个函数）。
    """
    candidates = [os.environ.get("MEMORY_ECOLOGY_GATE_MAX_BODY")]
    toml = prod / "configs" / "eco.toml"
    if toml.is_file():
        try:
            import tomllib
            with open(toml, "rb") as f:
                candidates.append(tomllib.load(f).get("gate", {}).get("max_body"))
        except Exception:
            pass                      # 配置损坏 → 用代码默认值，与 lib 的 fail-open 一致
    for raw in candidates:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        try:
            value = int(text)
        except (TypeError, ValueError):
            continue
        if value >= 1:
            return value
    return default


def read_body(path: Path) -> str:
    """取 frontmatter 之后的正文（detail 契约：两段 `---` 分隔）。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.split("---", 2)[-1].strip()


def _read_entry_text(path: Path) -> str:
    """整文件读取（保留 CRLF）——repair 要原样写回，不能静默把 CRLF 改成 LF。"""
    return path.read_bytes().decode("utf-8", errors="replace")


def _is_marked(text: str, body: str) -> bool:
    """是否已带截断标记。

    两种口径都认：正文里的 `…[truncated]`（v2.5.6 write_gate 的可见标记）
    与 frontmatter 的 `truncated_suspected`（存量修复的结构化标记，R-4）。
    """
    if TRUNC_MARK in body:
        return True
    return re.search(rf"^{MARK_FIELD}\s*:\s*(?:true|yes|1)\s*$", text,
                     re.MULTILINE | re.IGNORECASE) is not None


def _pending_candidate_texts(prod: Path) -> list[str]:
    """pending 里已消费的候选原文（*.done.md / *.md.done，eco_extract 列表格式）。"""
    pending = prod / "memories" / "pending"
    if not pending.is_dir():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for f in sorted(list(pending.glob("*.done.md")) + list(pending.glob("*.md.done"))):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = re.match(r"^\s*-\s*\[[^\]]*\]\s*(.+?)\s*$", line)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                out.append(m.group(1))
    return out


def _truncation_evidence(path: Path, body: str, candidates: list[str]) -> str | None:
    """单篇的**硬证据**（R-4）。返回证据标签；None = 只是可疑，不许写标记。

    证据只有两类（外加语料级批量签名，在 scan_truncation 里判）：
      ① pending/*.done.md 能对上原始候选：正文是更长候选的前缀；
      ② 文件 mtime 早于 v2.5.6 上线日（2026-10-23）且长度恰为历史硬编码 200/220。
    """
    for cand in candidates:
        if len(cand) > len(body) and cand.startswith(body):
            return "pending-done"
    try:
        mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        mtime = None
    if mtime is not None and mtime < _LEGACY_MTIME_CUTOFF and len(body) in LEGACY_LIMITS:
        return "legacy-mtime"
    return None


def _scan_entries(prod: Path, limit: int):
    """扫描 detail，返回 (全部文件数, 可疑条目, 已标记文件名, 无标点碎片数, 读失败)。"""
    detail = prod / "memories" / "detail"
    files = sorted(detail.glob("*.md"))
    limits = set(LEGACY_LIMITS) | {limit}
    candidates: list[dict] = []
    marked: list[str] = []
    legacy_noterm, unreadable = 0, []
    for f in files:
        try:
            text = _read_entry_text(f)
        except OSError as e:
            unreadable.append(f"{f.name}: {e}")
            continue
        body = text.split("---", 2)[-1].strip()
        if _is_marked(text, body):
            marked.append(f.name)
            continue
        if not TERMINAL.search(body):
            if len(body) in limits:
                candidates.append({"name": f.name, "path": f, "body": body})
            else:
                legacy_noterm += 1          # 正常无标点碎片：只报数，不判红
    return len(files), candidates, marked, legacy_noterm, unreadable


def scan_truncation(prod: Path, limit: int) -> dict:
    """数"沉默截断"：长度卡在已知上限、断在词中、且没有可见标记。

    R-4：这里的 `ok`/`files` 只认**硬证据**。"恰好 200 字且没有终止标点"本身不是
    截断（审计实测 96/162 正文不以标点结尾，其中 74 篇是正常碎片）；证据不足的
    条目放进 `suspects` 仅作报告，不判红、不允许 repair 动文件。
    """
    total, candidates, marked, legacy_noterm, unreadable = _scan_entries(prod, limit)
    pending_cands = _pending_candidate_texts(prod)
    # 语料级硬证据：≥20 篇同时卡在上限且无终止标点。单篇可能是巧合，
    # 整批同时卡上限是 v2.5.6 之前**全局**硬截断的签名（生产实测 22 篇）。
    bulk = len(candidates) >= BULK_EVIDENCE_MIN
    silent, suspects, evidence = [], [], {}
    for c in candidates:
        ev = _truncation_evidence(c["path"], c["body"], pending_cands)
        if ev is None and bulk:
            ev = "bulk-mass"
        if ev is not None:
            evidence[c["name"]] = ev
            silent.append(c["name"])
        else:
            suspects.append(c["name"])
    detail_txt = (f"detail {total} 篇 / 生效上限 {limit} / 已标记 {len(marked)}："
                  f"沉默截断 {len(silent)}（硬证据）")
    if suspects:
        detail_txt += f"；疑似证据不足 {len(suspects)} 篇（仅报告，未改文件）"
    detail_txt += f"（另有无标点非截断碎片 {legacy_noterm} 篇，仅计数）"
    return {
        "id": "truncate",
        "name": "A2 沉默截断",
        "ok": not silent,
        "detail": detail_txt,
        "files": silent[:20],           # 展示清单（[:20]）
        "worklist": list(silent),       # R-5：repair 的工作清单，全量、不截断
        "evidence": evidence,
        "suspects": suspects[:20],
        "marked": len(marked),
        "limit": limit,
        "noterm_other": legacy_noterm,
        "unreadable": unreadable[:5],
    }


def repair_truncation(prod: Path, limit: int) -> list[str]:
    """给**有硬证据**的存量截断正文补结构化标记（先整目录备份到 .legacy-bak-<日期>/）。

    R-4：不靠启发式改正文。标记写进 frontmatter（truncated_suspected /
    truncated_evidence），正文逐字保留（含 CRLF）——旧实现往正文尾追加
    `…[truncated]` 是不可逆的，且会把完整正文写成"已损坏"。
    R-5：用 `scan_truncation` 的 `worklist`（全量）而不是展示用的 `files`（[:20]），
    单次覆盖全部有证据条目。
    """
    scan = scan_truncation(prod, limit)
    todo = list(scan.get("worklist") or [])
    if not todo:
        return []
    evidence = scan.get("evidence") or {}
    detail = prod / "memories" / "detail"
    bak_dir = detail / f".legacy-bak-{datetime.date.today().isoformat()}"
    touched = []
    for name in todo:
        path = detail / name
        try:
            raw = _read_entry_text(path)
        except OSError:
            continue
        parts = raw.split("---", 2)
        if len(parts) != 3:
            continue                     # 格式不认识就不碰（宁可漏，不可误改）
        body = parts[2].strip()
        if _is_marked(raw, body):
            continue
        nl = "\r\n" if "\r\n" in raw else "\n"
        mid = parts[1].rstrip("\r\n")
        flag = (f"{nl}{MARK_FIELD}: true"
                f"{nl}truncated_evidence: {evidence.get(name, 'unknown')}")
        new_text = parts[0] + "---" + mid + flag + nl + "---" + parts[2]
        bak_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, bak_dir / name)
        path.write_bytes(new_text.encode("utf-8"))
        touched.append(name)
    return touched


# ── A1 部署一致性 ───────────────────────────────────────────────────────────

def _dev_script_dirs(dev: Path, prod: Path) -> list:
    """探测 "dev 侧脚本目录 → 生产侧对应目录"，支持两种已知布局。

    2026-10-29 的教训：A1 原来只认 `dev/scripts/`。发布树从 v3.2.x 起把同一批
    脚本放在 `dev/src/memory_ecology/`（生成物布局），于是**公开树当 --dev 时
    一个可比文件都找不到 → 永久红**。原注释把"检查跑不起来"当成了门禁结论，
    但一条永远不可能变绿的检查等于没有检查，还会掩盖真实的部署漂移。

    返回 [(dev 侧目录, 生产侧目录)]：
      · 开发树布局：dev/scripts            → prod/scripts
      · 发布树布局：dev/src/memory_ecology → prod/scripts
      · 对应 lib：  dev/**/lib             → prod/scripts/lib
    生产侧结构（scripts/ + scripts/lib/）不跟随发布树内部排布改名——生产是部署
    目标，这条比对的意义正是"发布物内容有没有如实落到生产"。
    """
    pairs = []
    for scripts, lib in ((dev / "scripts", dev / "scripts" / "lib"),
                         (dev / "src" / "memory_ecology",
                          dev / "src" / "memory_ecology" / "lib")):
        if scripts.is_dir() and any(scripts.glob("*.py")):
            pairs.append((scripts, prod / "scripts"))
            if lib.is_dir():
                pairs.append((lib, prod / "scripts" / "lib"))
            break        # 两种布局不叠加：避免同名文件被比两遍
    return pairs


# ── 脱敏改写识别（2026-10-29）────────────────────────────────────────────
#
# A1 的第二个设计缺陷：**发布物是脱敏过的，生产是没脱敏的**。
# 直接拿公开树 src/memory_ecology/ 去比生产 scripts/，必然"永久漂移"，但这些
# 差异全是生成器的既定改写，不是部署事故。实测到的形态：
#   .agent-index          → .memory-ecology     （路径去标识化）
#   memory-ecology-compat  → memory-ecology-compat（目录名去中文）
# 所以正确口径是"源 → 生产"，比较前先把源按同样规则归一。推荐用未脱敏的开发树
# 源当 --dev；拿公开树当 --dev 时这层归一自动生效，避免把"脱敏"误报成"漂移"。
_SANITIZE_EQUIV = (
    (".agent-index", ".memory-ecology"),
    ("memory-ecology-compat", "memory-ecology-compat"),
    ("兼容版", "compat"),
)

# 发布树专属的布局文件：生成器为让它成为可 import 的包而新增，开发源与生产都
# 没有对应物（生产 scripts/ 是扁平脚本目录，不靠 __init__ 也能直跑）。
# 属于"布局差异"而非"部署漂移"，A1 不该报缺失。
_LAYOUT_ONLY = {"__init__.py"}


def _norm_for_deploy(text: str) -> str:
    """把源文本归一到"其脱敏形态"，再与生产比对（只影响比较，不改文件）。"""
    text = text.replace("\r\n", "\n")
    for old, new in _SANITIZE_EQUIV:
        text = text.replace(old, new)
    return text


def check_deploy(dev: Path, prod: Path) -> dict:
    """逐文件比对 dev 的脚本目录与生产同名文件（LF 归一 + 脱敏归一）。

    比"只比 write_gate 一个文件"更强：能抓到"部署跑了一半"和"某次手工改回旧版"。
    只比对 dev 侧存在的文件 —— 生产独有的宿主胶水（dispatch/furina/index_daily…）
    本来就不在生态树里，不是漂移。

    fail-closed：一个可比对文件都没有时**不能判绿**。"检查无法执行"与"部署一致"
    是两件事；后者是门禁唯一有资格输出的绿票。但**两种已知布局都探测不到**才算
    "无法执行"；只要一种能对上，就必须给出真实漂移结论（见 `_dev_script_dirs`）。
    """
    drift, missing, total = [], [], 0
    for src_dir, dst_dir in _dev_script_dirs(dev, prod):
        for src in sorted(src_dir.glob("*.py")):
            if src.name in _LAYOUT_ONLY:
                continue        # 布局文件不参与一致性（见 _LAYOUT_ONLY）
            total += 1
            dst = dst_dir / src.name
            rel = f"lib/{src.name}" if src_dir.name == "lib" else f"scripts/{src.name}"
            if not dst.is_file():
                missing.append(rel)
            elif _norm_for_deploy(src.read_text(encoding="utf-8", errors="replace")) \
                    != _norm_for_deploy(dst.read_text(encoding="utf-8", errors="replace")):
                drift.append(rel)
    if total == 0:
        # 两种已知布局都没有可比的 .py：红。total 记 1 表示"这一条检查本身失败"，
        # 保持旧字段语义（总检查项）且让调用方无法把 0 误读成"零漂移"。
        return {
            "id": "deploy",
            "name": "A1 部署一致性",
            "ok": False,
            "detail": ("部署源没有可比的脚本（已探测 scripts/ 与 src/memory_ecology/ "
                       "两种布局）——检查无法执行 ≠ 部署一致"),
            "drift": [],
            "missing": [],
            "total": 1,
        }
    ok = not drift and not missing
    return {
        "id": "deploy",
        "name": "A1 部署一致性",
        "ok": ok,
        "detail": f"{total} 个共有文件：漂移 {len(drift)}、缺失 {len(missing)}",
        "drift": drift[:20],
        "missing": missing[:20],
        "total": total,
    }


# ── A3 索引一致性 ───────────────────────────────────────────────────────────

def check_index(prod: Path) -> dict:
    """INDEX.md 的 id 集合 vs 实际 exp-*.md 文件集合（集合比较，不比行数）。

    为什么用集合而不是"行数相等"：行数相等可以同时藏着"漏 2 条 + 多 2 条幽灵行"。
    """
    exp_dir = prod / "experiences"
    index = exp_dir / "INDEX.md"
    files = {p.stem for p in exp_dir.glob("exp-*.md")}
    if not index.is_file():
        return {"id": "index", "name": "A3 索引一致性", "ok": False,
                "detail": f"{index} 不存在", "missing": sorted(files)[:20], "extra": []}
    ids = set(EXP_ID.findall(index.read_text(encoding="utf-8", errors="replace")))
    missing = sorted(files - ids)
    extra = sorted(ids - files)
    return {
        "id": "index",
        "name": "A3 索引一致性",
        "ok": not missing and not extra,
        "detail": f"INDEX {len(ids)} 条 / 文件 {len(files)} 个：缺失 {len(missing)}、幽灵 {len(extra)}",
        "missing": missing[:20],
        "extra": extra[:20],
    }


# ── A4 挤出账实一致（v3.0.0a1，审计 P0-3）───────────────────────────────────

_EXTRUDE_ORIGIN_RE = re.compile(r"extrude", re.IGNORECASE)
_FM_ORIGIN_RE = re.compile(r"^origin_session_id[ \t]*:[ \t]*(.*)$", re.MULTILINE)


def check_extrude_ledger(prod: Path) -> dict:
    """带挤出占位符 origin 的 detail 条目，必须人人有 gate_log EXTRUDE 行。

    抓 P0-3「门② 挤出直写、绕过门①、账实背离」：origin 含 extrude 的条目由
    门② write_detail 落盘——前向（v3.0.0a1 起 eco_quota 逐条记账）与补账
    （tools/eco_backfill_origin.py）之后，缺行 = 又出现了无账直写。
    孤儿行（有账无条目）仅报告不判红：条目可能被人工清理，账是事实、不该删。
    SQLite 一律 ?mode=ro 打开——检查段零写入；SELFCHECK_OK 记账仍走
    write_report 的既有读写连接。
    """
    detail = prod / "memories" / "detail"
    stems: set[str] = set()
    unparsable: list[str] = []
    for f in sorted(detail.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            unparsable.append(f"{f.name}: {e}")
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end < 0:
            continue
        fm_text = text[3:end]
        m = _FM_ORIGIN_RE.search(fm_text)
        origin = (m.group(1).strip() if m else "")
        if _EXTRUDE_ORIGIN_RE.search(origin):
            stems.add(f.stem)

    db = prod / "eco.db"
    if not db.is_file():
        return {"id": "extrude", "name": "A4 挤出账实一致", "ok": not stems,
                "detail": f"eco.db 不存在；挤出 origin 条目 {len(stems)} 篇",
                "missing": sorted(stems)[:20], "extra": []}
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = {r[0] for r in con.execute(
            "SELECT target FROM gate_log WHERE action='EXTRUDE'")}
    finally:
        con.close()
    missing = sorted(stems - rows)
    orphans = sorted(rows - stems)
    ok = not missing
    orphan_note = f"、孤儿行 {len(orphans)}（仅报告）" if orphans else ""
    return {
        "id": "extrude",
        "name": "A4 挤出账实一致",
        "ok": ok,
        "detail": (f"挤出 origin 条目 {len(stems)} 篇 / EXTRUDE 行 {len(rows)} 行："
                   f"缺账 {len(missing)}{orphan_note}"),
        "missing": missing[:20],
        "extra": orphans[:20],
    }


# ── A5 scope 覆盖（v3.0.1，联邦化数据地基）────────────────────────────────

_SCOPE_CUTOFF = datetime.datetime(2026, 10, 25)   # v3.0.1 部署次日起新条目必须带 scope 键
_FM_SCOPE_RE = re.compile(r"^scope[ \t]*:[ \t]*(.*)$", re.MULTILINE)
_FM_TX_RE = re.compile(r"^transaction_time[ \t]*:[ \t]*(.*)$", re.MULTILINE)


def check_scope_coverage(prod: Path) -> dict:
    """v3.0.1 起新写条目必须有 scope 键（捕获侧来源文件，甲案数据地基）。

    断言只管**新条目**（transaction_time ≥ 部署次日；挤出直写豁免——scope=""
    是诚实值）。存量覆盖率（非空 scope / 非挤出条目）只报告不判红，
    回填目标线 70%（tools/eco_backfill_scope.py）。
    """
    detail = prod / "memories" / "detail"
    missing: list[str] = []
    total = non_extrude = scoped = 0
    for f in sorted(detail.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end < 0:
            continue
        fm_text = text[3:end]
        total += 1
        m_o = re.search(r"^origin_session_id[ \t]*:[ \t]*(.*)$", fm_text, re.MULTILINE)
        origin = (m_o.group(1).strip() if m_o else "")
        is_extrude = bool(re.search(r"extrude", origin, re.IGNORECASE))
        m_s = _FM_SCOPE_RE.search(fm_text)
        scope_val = (m_s.group(1).strip() if m_s else None)
        if not is_extrude:
            non_extrude += 1
            if scope_val:
                scoped += 1
        m_t = _FM_TX_RE.search(fm_text)
        try:
            tx = datetime.datetime.fromisoformat(m_t.group(1).strip()) if m_t else None
        except ValueError:
            tx = None
        if (not is_extrude and tx is not None and tx >= _SCOPE_CUTOFF
                and scope_val is None):
            missing.append(f.name)
    coverage = (scoped / non_extrude) if non_extrude else 0.0
    return {
        "id": "scope",
        "name": "A5 scope 覆盖",
        "ok": not missing,
        "detail": (f"detail {total} 篇：新条目缺 scope 键 {len(missing)}"
                   f"（≥{_SCOPE_CUTOFF:%Y-%m-%d} 部署线）；存量覆盖率 "
                   f"{coverage:.0%}（回填目标 ≥70%，仅报告）"),
        "missing": missing[:20],
        "extra": [],
    }


# ── A6 配置不变量（v3.1.0 Guard 件，动机=2026-10-24 eco.toml 整文件重写丢 max_body 事故）──

# (section, key, why)：只断言「键在场」；值由实例自由覆盖（不锁死）。
# 与 lib.config.REQUIRED_CONFIG 同步维护（selfcheck 零依赖，故各自持有副本）。
REQUIRED_KEYS = (
    ("quota", "memory_limit", "L1 MEMORY 配额"),
    ("quota", "memory_trigger", "L1 触发线"),
    ("gate", "max_body", "正文上限（审计 P0-2）"),
)


def check_config_invariants(prod: Path) -> dict:
    """生产实例 eco.toml 的关键键必须在场（丢键=红，直指整文件重写/误删事故）。

    值不判（实例可覆盖）；只判「在场」——2026-10-24 事故中 max_body 丢失后
    生效上限静默回落 200，靠 A2 的截断证据只能间接暴露，A6 把它变成当场断言。
    """
    toml = prod / "configs" / "eco.toml"
    missing = []
    data = {}
    if toml.is_file():
        try:
            import tomllib
            with open(toml, "rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            return {"id": "config", "name": "A6 配置不变量", "ok": False,
                    "detail": f"eco.toml 解析失败（{type(e).__name__}）——按缺键处理",
                    "missing": [str(e)[:80]], "extra": []}
    for sec, key, why in REQUIRED_KEYS:
        if key not in (data.get(sec) or {}):
            missing.append(f"[{sec}].{key}（{why}）")
    ok = not missing
    detail = (f"eco.toml 关键键 {len(REQUIRED_KEYS)} 项全在场"
              if ok else f"缺 {len(missing)} 个关键键：" + "；".join(missing[:3]))
    return {"id": "config", "name": "A6 配置不变量", "ok": ok, "detail": detail,
            "missing": missing[:10], "extra": []}


# ── 落盘：报告 + 账本 ───────────────────────────────────────────────────────

def write_report(prod: Path, results: list[dict], hard: bool) -> Path | None:
    """写人读报告 + 追加一行日志 +（可选）往 eco.db 的 gate_log 记一行。"""
    logs = prod / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    verdict = "全绿" if hard else "有红"
    lines = [f"# 生态自检报告 {today}", "",
             f"- 判定：**{verdict}**（{sum(1 for r in results if r['ok'])}/{len(results)} 绿）",
             f"- 生成时间：{now}", ""]
    for r in results:
        lines.append(f"## {'✅' if r['ok'] else '❌'} {r['name']}")
        lines.append(f"- {r['detail']}")
        for key, label in (("drift", "漂移"), ("missing", "缺失"), ("files", "问题文件"),
                           ("suspects", "疑似（仅报告）"), ("extra", "幽灵条目"),
                           ("unreadable", "读失败")):
            if r.get(key):
                lines.append(f"- {label}：{', '.join(str(x) for x in r[key])}")
        lines.append("")
    report = logs / f"{REPORT_PREFIX}-{today}.md"
    report.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    with open(logs / f"{REPORT_PREFIX}.log", "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{now}\t{verdict}\t" + "; ".join(
            f"{r['id']}={'ok' if r['ok'] else 'FAIL'}" for r in results) + "\n")

    db = prod / "eco.db"
    if db.is_file():
        try:
            con = sqlite3.connect(db, timeout=10)
            con.execute("""CREATE TABLE IF NOT EXISTS gate_log(
                ts TEXT, fingerprint TEXT PRIMARY KEY, action TEXT, target TEXT, note TEXT)""")
            for r in results:
                fp = hashlib.sha1(f"{today}|selfcheck|{r['id']}".encode()).hexdigest()
                con.execute(
                    "INSERT OR REPLACE INTO gate_log(ts, fingerprint, action, target, note) "
                    "VALUES(?,?,?,?,?)",
                    (now, fp, "SELFCHECK_OK" if r["ok"] else "SELFCHECK_FAIL",
                     r["id"], r["detail"][:300]))
            con.commit()
            con.close()
        except sqlite3.Error as e:          # 账本写不进不影响本次判定
            print(f"⚠️ gate_log 记账失败（不影响判定）: {e}", file=sys.stderr)
    return report


# ── 入口 ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="记忆生态自观测门（四条断言）")
    ap.add_argument("--dev", type=Path, default=repo, help="部署源（默认=本工具所在仓库）")
    ap.add_argument("--prod", type=Path,
                    default=Path(os.environ.get("MEMORY_ECOLOGY_PROD_ROOT")
                                 or (Path.home() / "AppData" / "Local" / "hermes")),
                    help="生产数据根/脚本根（默认 %%LOCALAPPDATA%%\\hermes）")
    ap.add_argument("--only", default="deploy,truncate,index,extrude,scope,config",
                    help="逗号分隔：deploy,truncate,index,extrude,scope,config")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--no-write", action="store_true", help="不写报告/账本")
    ap.add_argument("--repair", action="store_true",
                    help="给有硬证据的存量截断补 frontmatter 标记（先备份，不改正文）")
    args = ap.parse_args(argv)

    dev, prod = args.dev.resolve(), args.prod.resolve()
    if not (prod / "memories").is_dir():
        print(f"❌ 生产数据根不可用: {prod}", file=sys.stderr)
        return 2
    wanted = {x.strip() for x in args.only.split(",") if x.strip()}
    known = {"deploy", "truncate", "index", "extrude", "scope", "config"}
    # R-3：--only 拼错/留空 → 零检查执行。0 条检查不等于"全绿"，
    # docstring 约定 2 = 自检自身出错；未知检查名属于这一类。
    unknown = wanted - known
    if not wanted or unknown:
        print(f"❌ --only 无效：{args.only!r}"
              f"（未知：{', '.join(sorted(unknown)) or '空集合'}；"
              f"可用：deploy,truncate,index,extrude,scope,config）", file=sys.stderr)
        return 2
    limit = effective_max_body(prod)

    try:
        results: list[dict] = []
        if "deploy" in wanted:
            results.append(check_deploy(dev, prod))
        if "truncate" in wanted:
            if args.repair:
                touched = repair_truncation(prod, limit)
                print(f"🔧 已为 {len(touched)} 篇有硬证据的存量截断补 frontmatter 标记"
                      f"（正文未改；备份在 detail/.legacy-bak-*/）")
            results.append(scan_truncation(prod, limit))
        if "index" in wanted:
            results.append(check_index(prod))
        if "extrude" in wanted:
            results.append(check_extrude_ledger(prod))
        if "scope" in wanted:
            results.append(check_scope_coverage(prod))
        if "config" in wanted:
            results.append(check_config_invariants(prod))
    except Exception as e:                  # 自检自己坏了要和"检查不通过"区分开
        print(f"❌ 自检执行出错: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    hard = all(r["ok"] for r in results)
    if args.json:
        print(json.dumps({"ok": hard, "limit": limit,
                          "checks": results,
                          "report": None if args.no_write else str(write_report(prod, results, hard))},
                         ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"{'✅' if r['ok'] else '❌'} {r['name']}：{r['detail']}")
            for key, label in (("drift", "  漂移"), ("missing", "  缺失"),
                               ("files", "  问题文件"), ("suspects", "  疑似（仅报告）"),
                               ("extra", "  幽灵条目")):
                for item in (r.get(key) or [])[:10]:
                    print(f"{label}: {item}")
        if not args.no_write:
            print(f"📄 报告: {write_report(prod, results, hard)}")
        print("结论：" + ("全绿" if hard else "有红（详见上方）"))
    return 0 if hard else 1


if __name__ == "__main__":
    sys.exit(main())
