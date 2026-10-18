#!/usr/bin/env python3
"""经验笔记本 · INDEX.md 生成器（正式版 v1.0 组件）

扫描 experiences/ 目录条目（<id>.md），生成 INDEX.md 摘要表。
索引字段：id / type / status / trigger（标题）/ last_hit / 有无证据。
由 cron 每日重建（同 eco.db 模式）——维护负担=零的前提。

用法: python eco_note_index.py [--dir <experiences路径>]
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

from lib.config import hermes_root

HERMES = hermes_root()
EXP_DIR = HERMES / "experiences"

# OCR 2026-10-03: frontmatter 里的假性「有证据」值（evidence: false/no/0/无 等）
EV_FALSE = {"false", "no", "0", "无", "none", "null", "否", "n/a"}


def _truthy(v: str) -> bool:
    return bool(v and v.strip().lower() not in EV_FALSE)


def _cell(s) -> str:
    """OCR 2026-10-03: 表格单元格转义——值里的 | 或换行曾静默撑破 INDEX 表列。"""
    return str(s).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def parse_entry(path: Path) -> dict | None:
    """解析单条目文件的 frontmatter + trigger。非法文件返回 None（不拖垮全表）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        # OCR 2026-10-03: 读取失败不再无痕吞掉——持续失败会让索引悄悄缩水
        print(f"⚠️ 条目读取失败，跳过 {path.name}: {e}", file=sys.stderr)
        return None
    fm = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            kv = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
            if kv:
                fm[kv.group(1)] = kv.group(2).strip()
    bm = re.search(r"^title:\s*(.+)$", text, re.M) or re.search(r"^trigger:\s*(.+)$", text, re.M)
    title = (bm.group(1).strip().strip("\"'") if bm else "") or path.stem
    # 证据可能在前置字段（frontmatter 或正文行），两处都查（dsh 评审：frontmatter 查不到
    # 而 evidence 在正文 → 全表恒 "—" 的 bug）；OCR 2026-10-03: 值按语义归一（false/无 ≠ 有证据）
    evidence = ""
    ev_fm = fm.get("evidence", "")
    ev_m = re.search(r"^evidence:\s*(\S.*)$", text, re.M)
    if _truthy(ev_fm):
        evidence = ev_fm
    elif ev_m and _truthy(ev_m.group(1)):
        evidence = "有"
    return {
        "id": fm.get("id", path.stem),
        "type": fm.get("type", "?"),
        "status": fm.get("status", "?"),
        "title": title[:60],
        "last_hit": fm.get("last_hit", "")[:10],
        "evidence": evidence,
    }


def build_index(exp_dir: Path) -> str:
    lines = ["# 经验索引（自动生成，勿手改）", "",
             "| id | type | status | trigger | last_hit | 证据 |",
             "|---|---|---|---|---|---|"]
    entries = []
    for p in sorted(exp_dir.glob("exp-*.md")):
        e = parse_entry(p)
        if e:
            entries.append(e)
    for e in entries:
        lines.append(
            f"| {_cell(e['id'])} | {_cell(e['type'])} | {_cell(e['status'])} | {_cell(e['title'])} | "
            f"{_cell(e['last_hit'] or '—')} | {'✓' if e['evidence'] else '—'} |"
        )
    if not entries:
        lines.append("| （暂无条目） | | | | | |")
    lines += [
        "",
        f"生成时间: {datetime.now().isoformat(timespec='seconds')} | 条目数: {len(entries)}",
    ]
    return "\n".join(lines)


def main() -> int:
    from lib.cli_base import bootstrap as _bootstrap  # v2.5.0：编码兜底
    _bootstrap()
    # OCR 2026-10-03: --dir 参数校验——原先缺参 IndexError、非法路径写盘时才裸栈崩溃
    if "--dir" in sys.argv:
        i = sys.argv.index("--dir")
        if i + 1 >= len(sys.argv):
            print("错误：--dir 需要一个目录参数")
            return 2
        exp = Path(sys.argv[i + 1])
        if not exp.is_dir():
            print(f"错误：--dir 不是有效目录: {exp}")
            return 2
    else:
        exp = EXP_DIR
    out = exp / "INDEX.md"
    txt = build_index(exp)
    # 原子写（tmp + replace），防止并发读半写
    tmp = out.with_suffix(".md.tmp")
    tmp.write_text(txt, encoding="utf-8")
    tmp.replace(out)
    print(f"✅ INDEX.md 已重建（{out}，{out.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
