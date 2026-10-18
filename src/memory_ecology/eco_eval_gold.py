#!/usr/bin/env python3
"""伪 gold 构建器（v2.2.0 阶段 C）：注入/命中遥测 → 「实际被使用过的记忆」gold 集。

gold 定义（迭代策略 v2 §3）：命中过 = 实际被使用过（会话文本即证据，解决单用户无 gold 集）。
数据源：
  experiences/.hits.jsonl      verdict=hit 且 entry_id 非 null（Q15 修复后才有数据；
                               此前历史 entry_id 全 null，不回补，gold 自 v2.2.0 起累积）
  experiences/.injected.jsonl  取同会话最近一次注入的 error 摘要作重放查询文本
                               （v2.2.0 起 hook 记录该字段；缺失则该记录不可重放）
输出：experiences/.eval_gold.json {"generated_at", "gold": [{entry_id, error, ts, session}, ...]}
     同 entry_id 保留最近 per-entry 条（重放样本多样性）。

用法: python eco_eval_gold.py [--experiences DIR] [--per-entry 3]（手动/评测前运行）
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

from lib.config import hermes_root


def _read_jsonl(p: Path) -> list:
    if not p.exists():
        return []
    out = []
    bad = 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                out.append(d)
        except ValueError:
            bad += 1
    if bad:
        # OCR 2026-10-03: 损坏行不再无痕消失——计数告警，gold 账本的数据完整性可见
        print(f"⚠️ {p.name}: {bad} 行损坏/非 JSON 已跳过", file=sys.stderr)
    return out


def build_gold(exp_dir: Path, per_entry: int = 3) -> dict:
    """核心构建逻辑（供测试直接调用）：返回 gold dict，不落盘。

    R5（v2.2.0 review）：hit 归因取「时间上早于该 hit 的最近一次注入」，
    原实现取会话最后一条注入——晚于 hit 的第二次注入 error 会错配给第一次的 hit。"""
    hits = _read_jsonl(exp_dir / ".hits.jsonl")
    injected = _read_jsonl(exp_dir / ".injected.jsonl")
    inj_by_session: dict = {}
    for e in injected:
        # OCR 2026-10-03: 保留所有带 ts/session 的注入（含缺 error 的旧格式记录）——
        # 归因取「最近一条」后再判 error；原构建期就滤掉缺 error 记录，会把陈旧
        # error 错配给 hit（docstring 语义：缺失则该记录不可重放）
        if e.get("ts") and e.get("session"):
            inj_by_session.setdefault(e["session"], []).append(e)
    for lst in inj_by_session.values():
        lst.sort(key=lambda x: x["ts"])
    seen: dict = {}
    n_no_ts = 0
    for h in hits:
        if h.get("verdict") != "hit" or not h.get("entry_id"):
            continue
        if not h.get("ts"):
            # OCR 2026-10-03: 缺 ts 的 hit 计数留痕（原先静默以 error="" 进 gold，掩盖上游问题）
            n_no_ts += 1
        cands = [x for x in inj_by_session.get(h.get("session"), [])
                 if x["ts"] <= (h.get("ts") or "")]
        inj = cands[-1] if cands else {}
        seen.setdefault(h["entry_id"], []).append({
            "entry_id": h["entry_id"],
            "error": inj.get("error", ""),
            "ts": h.get("ts"),
            "session": h.get("session"),
        })
    if n_no_ts:
        print(f"⚠️ {n_no_ts} 条 hit 缺 ts，无法归因注入（error 记空、不可重放）", file=sys.stderr)
    if per_entry <= 0:  # R4：[-0:] == 全量，与「保留 N 条」语义相反——显式拒绝
        raise ValueError("per_entry 必须为正整数")
    gold: list = []
    for recs in seen.values():
        # OCR 2026-10-03: 切「最近 N 条」前按 ts 排序——.hits.jsonl 文件行序与 ts
        # 不一致（并发/回拨/手工写）时原实现会挑错样本
        recs.sort(key=lambda r: (r.get("ts") or ""))
        gold.extend(recs[-per_entry:])
    return {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"), "gold": gold}


def main() -> int:
    from lib.cli_base import bootstrap as _bootstrap  # v2.5.0：编码兜底
    _bootstrap()
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiences", type=Path,
                    default=hermes_root() / "experiences")
    ap.add_argument("--per-entry", type=int, default=3)
    args = ap.parse_args()
    if args.per_entry <= 0:
        ap.error("--per-entry 必须为正整数")
    out = build_gold(args.experiences, args.per_entry)
    target = args.experiences / ".eval_gold.json"
    from lib.fs import atomic_write
    args.experiences.mkdir(parents=True, exist_ok=True)  # R6：目录不存在不再崩
    atomic_write(target, json.dumps(out, ensure_ascii=False, indent=1))  # R6：与主题一致的安全写
    n_entries = len({g["entry_id"] for g in out["gold"]})
    n_replayable = sum(1 for g in out["gold"] if g.get("error"))
    print(f"✅ 伪 gold 集: {len(out['gold'])} 条（{n_entries} 个条目，{n_replayable} 条可重放）→ {target}")
    if n_replayable == 0:
        print("ℹ️ 暂无可重放记录（v2.2.0 前的注入无 error 字段）——自今日起随注入/命中累积")
    return 0


if __name__ == "__main__":
    sys.exit(main())
