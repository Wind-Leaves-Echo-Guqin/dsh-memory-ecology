"""measure —— 成本度量（插件/CLI/GUI 共读同一批账本）。

三条原则（都是旧项目踩出来的）：
  1. **数字必须从真实会话日志量**，不能从文档里抄（A2A §5：`tools/measure.py` 与 GUI 的
     数字必须一致，否则"双口径漂移"重演）。
  2. **落一行 `cost` 账**：GUI 的 G6 直接读账本，不自己再算一套。
  3. **KPI 判定在代码里**（≤3 条 / ≤1,200 字符），超标即 `over_kpi=True`，GUI 标红。

会话日志格式（实测，dsh 桌面 v4，**一行一条 NDJSON，zstd 多帧流式压缩**）：
    {"type":"user/message","seq":8,"data":{"source":{"kind":"eco-catalog"},...},
     "surfaceOp":"append"}
    {"type":"request/header","data":{"tools":[...],"systemPrompt":...}}
    {"type":"system/message","data":{"content":[...]}}

**注意**：`ZstdDecompressor().decompress()` 只能解**单帧**，遇到多帧/大文件会截断
（实测 13MB 的会话只解出 1 行）——必须用 `stream_reader`。这是个真实的坑，
`tests/test_measure.py` 用多帧夹具钉住它。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import config, ledger, paths, retrieve

KPI_MSG = config.INJECT_MAX_MESSAGES
KPI_CHARS = config.INJECT_MAX_CHARS

_MEANINGFUL = {"user/message", "system/message", "request/header", "assistant/message",
               "agent/inject"}


def session_dirs() -> list[Path]:
    home = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    return [home / "sessions"]


def iter_session_files(limit: int = 200) -> list[Path]:
    out: list[Path] = []
    for d in session_dirs():
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("session*.jsonl*"), key=lambda x: -x.stat().st_mtime):
            out.append(p)
            if len(out) >= limit:
                return out
    return out


def read_session_lines(p: Path) -> list[str]:
    """读会话日志。**多帧 zstd 必须流式解**；缺 zstandard 模块则 fail-open 返回空。"""
    p = Path(p)
    try:
        if p.suffix == ".zstd":
            try:
                import zstandard  # type: ignore
            except Exception:
                return []
            try:
                with open(p, "rb") as f:
                    raw = zstandard.ZstdDecompressor().stream_reader(f).read()
                return raw.decode("utf-8", "replace").splitlines()
            except Exception:
                return []
        return paths.read_text(p).splitlines()
    except OSError:
        return []


def _source_kind(data: dict) -> str:
    src = data.get("source")
    if isinstance(src, dict):
        return str(src.get("kind") or src.get("name") or "unknown")
    if isinstance(src, str):
        return src[:60] or "unknown"
    return "unknown"


def measure_session(p: Path, needle: str = "记忆目录") -> dict:
    """单会话：工具 schema / 系统提示词 / 追加注入条数与字符（按 source）/ 本插件注入。"""
    lines = read_session_lines(p)
    tools_chars = 0
    sys_prompt = 0
    appended = 0
    appended_chars = 0
    eco_chars = 0
    by_source: dict[str, int] = {}
    for ln in lines:
        if not ln.strip():
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        t = obj.get("type")
        if t == "request/header":
            data = obj.get("data") or {}
            header = data.get("header") or data
            tools = header.get("tools") if isinstance(header, dict) else None
            if isinstance(tools, list):
                tools_chars = max(tools_chars,
                                  len(json.dumps(tools, ensure_ascii=False, separators=(",", ":"))))
            sp = (header.get("systemPrompt") or header.get("system_prompt")
                  if isinstance(header, dict) else None)
            if isinstance(sp, str):
                sys_prompt = max(sys_prompt, len(sp))
            elif sp is not None:
                sys_prompt = max(sys_prompt, len(json.dumps(sp, ensure_ascii=False)))
        elif t == "system/message":
            data = obj.get("data") or {}
            msg = data.get("message") or data
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, list):
                n = sum(len(str(item.get("text") or "")) if isinstance(item, dict) else len(str(item))
                        for item in content)
                sys_prompt = max(sys_prompt, n)
            elif isinstance(content, str):
                sys_prompt = max(sys_prompt, len(content))
        elif t == "user/message":
            data = obj.get("data") or {}
            src = _source_kind(data)
            op = obj.get("surfaceOp") or ""
            if op != "append":
                continue
            # 只有"注入"源才算追加成本；用户自己打字不算（旧 measure 把两者混在一起）
            if src in ("user", "unknown"):
                continue
            appended += 1
            text = json.dumps(data.get("content"), ensure_ascii=False)
            appended_chars += len(text)
            by_source[src] = by_source.get(src, 0) + 1
            if src.startswith("eco-") or needle in text:
                eco_chars += len(text)
    return {"session": p.name, "path": str(p), "lines": len(lines),
            "tools_schema": tools_chars, "system_prompt": sys_prompt,
            "injected_msgs": appended, "injected_chars": appended_chars,
            "eco_chars": eco_chars, "by_source": by_source}


def measure_root(root, record: bool = True, limit: int = 5, needle: str = "记忆目录") -> dict:
    """量最近 N 个有内容的会话 + 本库目录成本；按需落 `cost` 账。

    `catalog` 字段取**物化视图的真实字符数**（不是估算）——GUI G6 与插件注入看到的
    是同一个文件的同一个数，不可能漂移。

    **KPI 只对本插件自己的注入源计**（`eco-*` 前缀 + 含目录关键词的块）。

    这条区分很重要：`time-context` / `skill-catalog` / `subagent-settled` 等来源
    **不属于本项目**，把它们算进 KPI 会让"本插件超标"变成常态误报，进而让告警失去意义
    （实测：同一会话里本插件 1 条 / 368 字符，而全量注入是 19 条 / 92k 字符——
    全算进来等于永远超标）。`total_*` 字段仍然如实报告全量，只是不参与 `over_kpi`。
    """
    sess: list[dict] = []
    for p in iter_session_files(limit=60):
        m = measure_session(p, needle=needle)
        if m["lines"] >= 0 and (m["injected_msgs"] or m["tools_schema"]):
            sess.append(m)
        if len(sess) >= limit:
            break
    cat = retrieve.read_materialized(root, "global")
    cat_chars = len(retrieve.render_catalog_text(cat)) if cat else 0

    def eco_msgs(s: dict) -> int:
        return sum(n for src, n in (s.get("by_source") or {}).items()
                   if str(src).startswith("eco-"))

    worst_eco_msgs = max((eco_msgs(s) for s in sess), default=0)
    worst_eco_chars = max((s["eco_chars"] for s in sess), default=0)
    worst_total_msgs = max((s["injected_msgs"] for s in sess), default=0)
    worst_total_chars = max((s["injected_chars"] for s in sess), default=0)
    out = {
        "ok": True, "root": str(root.path), "sessions": sess,
        "catalog_chars": cat_chars, "catalog_items": (cat or {}).get("count", 0),
        "kpi": {"max_messages": KPI_MSG, "max_chars": KPI_CHARS,
                "applies_to": "本插件自己的注入源（eco-* / 含目录关键词的块）"},
        "worst_messages": worst_eco_msgs, "worst_chars": worst_eco_chars,
        "over_kpi": bool(worst_eco_msgs > KPI_MSG or worst_eco_chars > KPI_CHARS),
        "total_messages": worst_total_msgs, "total_chars": worst_total_chars,
        "tools_schema": max((s["tools_schema"] for s in sess), default=0),
        "system_prompt": max((s["system_prompt"] for s in sess), default=0),
        "eco_chars": worst_eco_chars,
    }
    if record and sess:
        conn = root.connect()
        try:
            for s in sess:
                ledger.log(conn, "cost", {
                    "ts": paths.now(), "session": s["session"],
                    "tools_schema": s["tools_schema"],
                    "system_prompt": s["system_prompt"], "catalog": cat_chars,
                    "injected_msgs": s["injected_msgs"],
                    "injected_chars": s["injected_chars"],
                    # **分来源落账**：KPI 只对 eco_* 成立（见 measure_root 的 docstring）
                    "eco_msgs": eco_msgs(s), "eco_chars": s["eco_chars"]})
            conn.commit()
        finally:
            conn.close()
    return out
