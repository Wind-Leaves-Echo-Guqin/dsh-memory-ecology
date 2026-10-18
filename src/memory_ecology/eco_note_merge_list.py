#!/usr/bin/env python3
"""经验笔记本 · 近重复合并清单生成器（v2.1.1 ⑥，2026-09-05）

只读：扫描正式层 101 条 + 回扫候选池（backfill/*.md），输出近似对清单（n-gram
Jaccard ≥ 0.75），附每对合并建议（保留信息更全/更新者，另一条 absorbed_into 留痕）。
**只生成清单，绝不自动落标**（cc 裁决：内容级合并误合有代价，人工勾选后才执行）。

用法: python eco_note_merge_list.py [--min-sim 0.75] [--out <路径>]
输出: experiences/pending/backfill/.merge-candidates-<ts>.md（默认）
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import eco_note_query as qy
import eco_note_backfill as bf

SIM_THRESHOLD = 0.75


def _norm(s: str) -> str:
    return re.sub(r"[\s\W]+", "", s.lower())


def sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    ga = {na[i:i + 2] for i in range(len(na) - 1)}
    gb = {nb[i:i + 2] for i in range(len(nb) - 1)}
    return len(ga & gb) / max(1, len(ga | gb))


def collect_items() -> list[dict]:
    """收集全部条目（正式层 + 候选池），零写入。"""
    items: list[dict] = []
    for p in qy.EXP_DIR.glob("exp-*.md"):
        e = qy._parse_entry(p)
        if e:
            # OCR 2026-10-03: 带上更新时间（last_hit/created 优先），供保留建议比较
            updated = e["fields"].get("last_hit") or e["fields"].get("created") or ""
            items.append({"id": p.stem, "title": e["fields"].get("title", ""),
                          "zone": "正式层", "updated": updated})
    for f in bf.backfill_dir().glob("*.md"):
        for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = re.match(r"^-\s*\[(\w+)\]\s*\*\*(.+?)\*\*", line)
            if m:
                # OCR 2026-10-03: 候选 id 不再用 40 字符截断标题——两个共享前缀的
                # 不同候选原先会被当成同一节点而漏比
                items.append({"id": f"{f.name}#{lineno}", "title": m.group(2),
                              "zone": f"候选({f.name[:20]})", "updated": ""})
    return items


def _keep(a: dict, b: dict) -> dict:
    """OCR 2026-10-03: 保留建议改为「更新者优先」（updated 可比时），退化才比标题长度——
    标题长 ≠ 信息更全/更新，原文档语义照此兑现。"""
    ua, ub = a.get("updated") or "", b.get("updated") or ""
    if ua and ub and ua != ub:
        return a if ua >= ub else b
    return a if len(a["title"]) >= len(b["title"]) else b


def find_pairs(items: list[dict], threshold: float) -> list[dict]:
    """近似对（每对只列一次，按无序对去重；已吸收节点不再与他人配对）。"""
    pairs = []
    seen = set()
    pair_seen = set()
    for i in range(len(items)):
        a = items[i]
        if a["id"] in seen:
            continue
        for j in range(i + 1, len(items)):
            b = items[j]
            if b["id"] in seen:
                continue
            s = sim(a["title"], b["title"])
            if s >= threshold:
                # OCR 2026-10-03: 去掉首对即 break——同一节点可有多个近重复
                #（a≈b 与 a≈c），原先第二对被静默丢弃
                key = frozenset((a["id"], b["id"]))
                if key in pair_seen:
                    continue
                pair_seen.add(key)
                keep = _keep(a, b)
                absorbed = b if keep is a else a
                # OCR 复核 2026-10-03（high）：keep 侧也须登记——否则同一节点可能
                # 先作为保留方、又在后续对里被判为吸收方，产出自相矛盾指令。
                seen.add(keep["id"])
                seen.add(absorbed["id"])
                pairs.append({"sim": round(s, 2), "keep": keep, "absorb": absorbed})
                # 若被吸收方是外层 a，内层循环仍在以 a 为基准继续比对，会产生
                # 「a 已被吸收却又作为保留方」的矛盾指令——吸收 a 时终止本外层循环。
                if absorbed is a:
                    break
    return sorted(pairs, key=lambda x: -x["sim"])


def main() -> int:
    from lib.cli_base import bootstrap as _bootstrap  # v2.5.0：编码兜底
    _bootstrap()
    ap = argparse.ArgumentParser(description="近重复合并清单生成器（只读）")
    ap.add_argument("--min-sim", type=float, default=SIM_THRESHOLD)
    ap.add_argument("--out", default=None, help="输出清单路径（默认 backfill/.merge-candidates-<ts>.md）")
    args = ap.parse_args()
    items = collect_items()
    print(f"扫描 {len(items)} 条（正式层+候选池）")
    pairs = find_pairs(items, args.min_sim)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) if args.out else bf.backfill_dir() / f".merge-candidates-{ts}.md"
    lines = [f"# 近重复合并清单（v2.1.1 ⑥，{ts}，只读生成）", "",
             f"阈值 ≥{args.min_sim}；**共 {len(pairs)} 对**；动作：人工勾选后执行合并（保留 keep，吸收 absorb→absorbed_into）", ""]
    for i, p in enumerate(pairs, 1):
        lines.append(f"### 对 {i}（相似度 {p['sim']}）")
        lines.append(f"- 保留: [{p['keep']['zone']}] {p['keep']['id']} — {p['keep']['title'][:60]}")
        lines.append(f"- 吸收: [{p['absorb']['zone']}] {p['absorb']['id']} — {p['absorb']['title'][:60]}")
        lines.append(f"- 勾选: [ ] 合并此对（勾选后执行 eco_note_merge.py）")
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"清单 {len(pairs)} 对 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
