# -*- coding: utf-8 -*-
"""治理消融 harness v1.1（RQ1 全量）——记忆生态论文实验。

三臂：A0-raw（无门直写）/ A1-gate1（仅门①）/ A2-full（完整生命周期）。
要点：
- 程序化事实宇宙 60 事实（6 领域 × 10 项目 × 唯一 token 对）；
- 会话日历每场 +3 天（30 场 = 90 天跨度）；
- L1 预置水位 ≈2400 字 + 宿主逐场注入一条常驻注记（三臂同款）；
- 门④复核检查点前，把条目 last_verified 恢复为创建日期（write_gate 盖真实
  今天，需模拟日历推进）；事实被复述/矛盾命中时 touch（命中刷新验证日期，
  生产语义）；
- 门②每 5 场、门③门④每 10 场 + 末场运行。
全程零 LLM 零网络；确定性种子。

用法:
  python ablation.py --repo "<USER_HOME>/memory-ecology-compat" \
      --out results --seeds 42,43,44,45,46 --sessions 30 --cands 10 --arms raw,g1,full
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

BASE = date(2026, 1, 1)
DAY_STEP = 3
L1_SEED_CHARS = 2400
L1_FILLER = "预置水位填充条目 #{n}：本条为实验预置内容，无事实价值，仅用于接近配额触发线，不应进入任何指标。"

PROJS = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota", "Kappa"]
DOMAINS = [
    ("主数据库", [("PostgreSQL 15", "PostgreSQL 16"), ("MySQL 8.0", "MySQL 8.4"),
                 ("MongoDB 6", "MongoDB 7"), ("MariaDB 10", "MariaDB 11"),
                 ("SQLite 3.40", "SQLite 3.45"), ("CockroachDB 23", "CockroachDB 24"),
                 ("TiDB 7", "TiDB 8"), ("ClickHouse 23", "ClickHouse 24"),
                 ("DuckDB 0.9", "DuckDB 1.0"), ("Oracle 19", "Oracle 21")]),
    ("缓存层", [("Redis 6", "Redis 7"), ("Memcached 1.6", "Memcached 1.7"),
               ("KeyDB 6", "KeyDB 7"), ("Dragonfly 1.0", "Dragonfly 1.1"),
               ("Valkey 7", "Valkey 8"), ("Aerospike 6", "Aerospike 7"),
               ("Hazelcast 5", "Hazelcast 6"), ("Garnet 0.1", "Garnet 1.0"),
               ("Tarantool 2.11", "Tarantool 3.0"), ("ArangoDB 3.11", "ArangoDB 3.12")]),
    ("消息队列", [("Kafka 3.5", "Kafka 4.0"), ("RabbitMQ 3.12", "RabbitMQ 4.0"),
                ("NATS 2.9", "NATS 2.10"), ("Pulsar 2.11", "Pulsar 3.0"),
                ("RocketMQ 5.0", "RocketMQ 5.1"), ("Redpanda 23", "Redpanda 24"),
                ("ActiveMQ 5.18", "ActiveMQ 6.0"), ("NSQ 1.2", "NSQ 1.3"),
                ("Kestrel 6", "Kestrel 7"), ("EventStore 23", "EventStore 24")]),
    ("流水线", [("GitHub Actions", "GitLab CI"), ("Jenkins 2.4", "Jenkins 2.5"),
               ("CircleCI 2", "CircleCI 3"), ("Travis CI", "Drone CI"),
               ("Buildkite", "TeamCity"), ("Azure Pipelines", "Gitea Actions"),
               ("Woodpecker 2", "Woodpecker 3"), ("Concourse 7", "Concourse 8"),
               ("Buddy", "Cirrus CI"), ("Werf", "Argo Workflows")]),
    ("终端环境", [("Git Bash", "Windows Terminal"), ("pwsh 7.3", "pwsh 7.4"),
                ("Cygwin", "MSYS2"), ("cmd 批处理", "PowerShell 脚本"),
                ("Zsh 5.9", "Zsh 5.10"), ("Fish 3.6", "Fish 4.0"),
                ("Nushell 0.88", "Nushell 0.90"), ("Xonsh 14", "Xonsh 15"),
                ("Alacritty", "Kitty"), ("Warp", "WezTerm")]),
    ("日志格式", [("JSON 行", "logfmt"), ("纯文本", "结构化 JSON"),
                ("syslog", "journalctl"), ("Loki 标签", "OTLP 导出"),
                ("Fluentd", "Vector"), ("Logstash", "Filebeat"),
                ("Splunk HEC", "OpenTelemetry"), ("按天滚动", "按大小滚动"),
                ("gzip 归档", "zstd 归档"), ("本地磁盘", "对象存储")]),
]


def build_fact_universe():
    facts = []
    for comp, pairs in DOMAINS:
        for i, (v1, v2) in enumerate(pairs):
            proj = PROJS[i]
            facts.append({
                "id": f"{comp}-{proj}",
                "v1": f"项目 {proj} 的{comp}是 {v1}",
                "v2": f"项目 {proj} 的{comp}已迁移到 {v2}",
                "query": f"{proj} 的{comp}",
                "t1": v1, "t2": v2,
            })
    return facts


FACTS = build_fact_universe()
assert len(FACTS) == 60

NOISE = [
    "今天的会话主要在调试前端样式。", "用户提到下午要开会，勿扰。", "临时笔记：需要买一根 USB-C 线。",
    "会话中讨论了天气对训练时间的影响。", "用户偏好简短的回复风格。", "记录：咖啡豆快用完了。",
    "本次会话未产生新的坑。", "用户最近在看一部纪录片。", "键盘空格键手感变差了。",
    "讨论了周末的爬山计划。", "截图工具换了一个。", "提醒：周五要交周报。",
]
PARA_PREFIX = ["备注：", "补充：", "另外，", "顺带确认：", "记录一下：", "再次确认："]
PARA_SWAP = [("统一", "一律"), ("使用", "用"), ("是", "为"), ("必须", "需要"), ("所有", "全部")]


def norm(t: str) -> str:
    return re.sub(r"[\s，。：:;；、（）()\-—·.！!？?\"'「」]+", "", t).lower()


def paraphrase(t: str) -> str:
    out = t
    for a, b in PARA_SWAP:
        if a in out:
            out = out.replace(a, b, 1)
            break
    return PARA_PREFIX[hash(t) % len(PARA_PREFIX)] + out + "（已确认）。"


def session_date(i: int) -> str:
    return (BASE + timedelta(days=i * DAY_STEP)).isoformat()


def plan_workload(seed: int, sessions: int, cands: int):
    import random
    rng = random.Random(seed)
    events, introduced = [], {}
    for i in range(sessions):
        evs = []
        for _ in range(cands):
            r = rng.random()
            if r < 0.40:
                unseen = [f for f in FACTS if f["id"] not in introduced]
                if unseen:
                    f = unseen[rng.randrange(len(unseen))]
                    introduced[f["id"]] = i
                    evs.append(("novel", f))
                else:
                    evs.append(("dup", FACTS[rng.randrange(len(FACTS))]))
            elif r < 0.70:
                cand = [f for f in FACTS if f["id"] in introduced and introduced[f["id"]] < i]
                if cand:
                    evs.append(("dup", cand[rng.randrange(len(cand))]))
                else:
                    evs.append(("noise", NOISE[rng.randrange(len(NOISE))]))
            elif r < 0.80:
                cand = [f for f in FACTS if f["id"] in introduced and i - introduced[f["id"]] >= 5]
                if cand:
                    evs.append(("contra", cand[rng.randrange(len(cand))]))
                else:
                    evs.append(("noise", NOISE[rng.randrange(len(NOISE))]))
            else:
                evs.append(("noise", NOISE[rng.randrange(len(NOISE))]))
        events.append(evs)
    return events


def candidate_text(kind, payload):
    if kind == "novel":
        return payload["v1"], payload["t1"]
    if kind == "dup":
        return paraphrase(payload["v1"]), payload["t1"]
    if kind == "contra":
        return payload["v2"], payload["t2"]
    return payload, None


def write_pending(root: Path, session_idx: int, items):
    lines = [f"# 捕获候选 {session_date(session_idx)}", ""]
    for text, _tok in items:
        lines.append(f"- [semantic] {text}")
    (root / "memories" / "pending" / f"bench-{session_idx:03d}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def write_raw_entry(root: Path, session_idx: int, j: int, text: str):
    d = session_date(session_idx)
    fm = {
        "type": "semantic", "status": "active", "occurrences": "1", "session_count": "1",
        "first_seen": d, "last_seen": d, "valid_time": d, "transaction_time": d + "T00:00:00",
        "last_verified": d, "origin_session_id": f"sess-{session_idx:03d}", "superseded_by": "",
    }
    body = "\n".join(f"{k}: {v}" for k, v in fm.items())
    (root / "memories" / "detail" / f"raw-{session_idx:03d}-{j:03d}.md").write_text(
        f"---\n{body}\n---\n\n{text}\n", encoding="utf-8")


def seed_l1(root: Path):
    mem = root / "memories" / "MEMORY.md"
    text = ""
    n = 0
    while len(text) < L1_SEED_CHARS and n < 60:
        text += L1_FILLER.format(n=n) + "\n§\n"
        n += 1
    mem.write_text(mem.read_text(encoding="utf-8").rstrip() + "\n§\n" + text, encoding="utf-8")


def host_l1_append(root: Path, session_idx: int, text: str):
    mem = root / "memories" / "MEMORY.md"
    cur = mem.read_text(encoding="utf-8").rstrip()
    mem.write_text(cur + f"\n§\n会话 sess-{session_idx:03d} 常驻注记：{text}\n", encoding="utf-8")


def run_gate(repo: Path, root: Path, script: str, timeout: int = 120):
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["MEMORY_ECOLOGY_ROOT"] = str(root)
    cmd = [sys.executable, f"scripts/{script}"]
    if script == "eco_review.py":
        # eco_review 的默认路径是脚本目录相对解析、不读 MEMORY_ECOLOGY_ROOT（产品不
        # 一致点，复现笔记记录）——必须显式传路径，否则会落到仓库本体路径上。
        cmd += ["--detail", str(root / "memories" / "detail"),
                "--quarantine", str(root / "memories" / "quarantine"),
                "--archive", str(root / "memories" / "archive"),
                "--db", str(root / "eco.db"),
                "--logdir", str(root / "memories" / "gate_log")]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, env=env, cwd=str(repo), timeout=timeout)
    dt = time.perf_counter() - t0
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode != 0:
        raise RuntimeError(f"{script} exit {p.returncode}: {p.stderr.decode('utf-8','replace')[:500]}")
    return out, dt


def count_decisions(out: str) -> dict:
    d = {"ADD": 0, "UPDATE": 0, "NOOP": 0, "CONFLICT": 0}
    for line in out.splitlines():
        m = re.match(r"^(ADD|UPDATE|NOOP|CONFLICT)\b", line.strip())
        if m:
            d[m.group(1)] += 1
    return d


def load_entries(root: Path):
    out = []
    for p in sorted((root / "memories" / "detail").glob("*.md")):
        raw = p.read_text(encoding="utf-8", errors="replace")
        body, fm = raw, {}
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        fm[k.strip()] = v.strip()
                body = parts[2]
        out.append({"path": p.name, "body": body.strip(), "fm": fm,
                    "filler": "预置水位填充" in body})
    return out


def detail_slugs(root: Path):
    return {p.name for p in (root / "memories" / "detail").glob("*.md")}


def backdate_entry(root: Path, slug: str, d: str):
    """把条目的时间字段恢复为其创建会话日期（write_gate 盖真实今天，需模拟日历）。"""
    p = root / "memories" / "detail" / slug
    raw = p.read_text(encoding="utf-8", errors="replace")
    for k in ("last_verified", "last_seen", "first_seen"):
        raw = re.sub(rf"^{k}: .*$", f"{k}: {d}", raw, flags=re.M)
    p.write_text(raw, encoding="utf-8")


def touch_fact_entries(root: Path, fact: dict, session_idx: int):
    """事实被复述/矛盾命中 → 刷新相关条目的验证日期（生产语义：命中即刷新）。"""
    d = session_date(session_idx)
    for p in (root / "memories" / "detail").glob("*.md"):
        raw = p.read_text(encoding="utf-8", errors="replace")
        if fact["t1"] in raw:
            for k in ("last_verified", "last_seen"):
                raw = re.sub(rf"^{k}: .*$", f"{k}: {d}", raw, flags=re.M)
            p.write_text(raw, encoding="utf-8")


def dup_ratio(entries):
    texts = [norm(e["body"]) for e in entries if not e["filler"]]
    n = len(texts)
    if n < 2:
        return 0.0, 0
    pairs = total = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += 1
            if texts[i] and texts[j] and difflib.SequenceMatcher(None, texts[i], texts[j]).ratio() >= 0.75:
                pairs += 1
    return pairs / total, pairs


def contradictions_unresolved(entries, facts):
    total = unresolved = 0
    for f in facts:
        if not any(e["body"].find(f["t2"]) >= 0 for e in entries):
            continue
        total += 1
        stale_active = any(
            (e["body"].find(f["t1"]) >= 0 and e["fm"].get("superseded_by", "") == ""
             and e["fm"].get("status", "active") == "active")
            for e in entries)
        if stale_active:
            unresolved += 1
    return total, unresolved


def precision_at_3(facts, entries):
    """门级相似度口径（关键词包含 + difflib，与 lib.similarity 同源），只评估已注入事实。"""
    hits_total = correct = 0
    for f in facts:
        has_v1 = any(e["body"].find(f["t1"]) >= 0 for e in entries)
        has_v2 = any(e["body"].find(f["t2"]) >= 0 for e in entries)
        if not (has_v1 or has_v2):
            continue
        expected = f["t2"] if has_v2 else f["t1"]
        scored = []
        for e in entries:
            if e["filler"]:
                continue
            nb = norm(e["body"])
            s = (1.0 if norm(f["query"]) in nb else 0.0) * 2 + difflib.SequenceMatcher(None, norm(f["query"]), nb).ratio()
            scored.append((s, e))
        top = [e["body"] for _s, e in sorted(scored, key=lambda x: -x[0])[:3]]
        hits_total += 1
        if any(expected in h for h in top):
            correct += 1
    return round(correct / hits_total, 4) if hits_total else 0.0


def run_arm(repo: Path, arm: str, seed: int, sessions: int, cands: int, keep: bool = False):
    sys.path.insert(0, str(repo))
    from tests.fixtures.eco import create_eco  # noqa: E402

    root = Path(tempfile.mkdtemp(prefix=f"eco-ab-{arm}-{seed}-"))
    create_eco(root, seed=seed)
    for f in (root / "memories" / "pending").glob("*.md"):
        f.unlink()
    seed_l1(root)
    events = plan_workload(seed, sessions, cands)
    gate_time = 0.0
    decisions = {"ADD": 0, "UPDATE": 0, "NOOP": 0, "CONFLICT": 0}
    gate_errors = []
    curve = []
    created_at = {}  # slug -> 创建会话（write_gate 会盖真实日期，供回填）

    for i in range(sessions):
        items = [candidate_text(k, p) for k, p in events[i]]
        host_l1_append(root, i, items[0][0])
        before = detail_slugs(root)
        if arm == "raw":
            for j, (text, _t) in enumerate(items):
                write_raw_entry(root, i, j, text)
        else:
            write_pending(root, i, items)
            # 门①按「清空 pending」语义运行：MAX_OUTPUT=8/轮 < 本负载 10 条/场的
            # 流入速率，单轮会积压（稳态治理假设下门必须跟上流入）。循环至全部
            # pending 文件消费完（.done 标记），上限 12 轮防死循环。
            for _round in range(12):
                live = [p.name for p in (root / "memories" / "pending").glob("*.md")
                        if not p.name.endswith(".done.md")]
                if not live:
                    break
                out, dt = run_gate(repo, root, "write_gate.py")
                gate_time += dt
                for k, v in count_decisions(out).items():
                    decisions[k] += v
        # 记录新条目的创建会话
        for slug in detail_slugs(root) - before:
            created_at[slug] = i
        # 命中刷新：本会话的复述/矛盾事件 touch 对应事实条目
        for kind, payload in events[i]:
            if kind in ("dup", "contra"):
                touch_fact_entries(root, payload, i)
        if arm == "full":
            if (i + 1) % 5 == 0:
                try:
                    _o, dt = run_gate(repo, root, "eco_quota.py")
                    gate_time += dt
                except RuntimeError as e:
                    gate_errors.append(f"quota@{i}: {e}")
            if (i + 1) % 10 == 0 or i == sessions - 1:
                for slug in detail_slugs(root):
                    backdate_entry(root, slug, session_date(created_at.get(slug, i)))
                for s in ("distill_stage.py", "eco_review.py"):
                    try:
                        _o, dt = run_gate(repo, root, s)
                        gate_time += dt
                    except RuntimeError as e:
                        gate_errors.append(f"{s}@{i}: {e}")
        entries = load_entries(root)
        real = [e for e in entries if not e["filler"]]
        l1_text = (root / "memories" / "MEMORY.md").read_text(encoding="utf-8", errors="replace").replace("\r", "")
        curve.append({"session": i + 1, "size": len(real),
                      "l1_chars": len(l1_text),
                      "l1_entries": len([x for x in l1_text.split("§") if x.strip()])})

    entries = load_entries(root)
    real = [e for e in entries if not e["filler"]]
    ratio, pairs = dup_ratio(entries)
    total_c, unresolved = contradictions_unresolved(entries, FACTS)
    prec = precision_at_3(FACTS, entries)
    dormant = sum(1 for e in real if e["fm"].get("status") == "dormant")
    archived = len(list((root / "memories" / "archive").glob("*.md"))) if (root / "memories" / "archive").exists() else 0
    l1_text = (root / "memories" / "MEMORY.md").read_text(encoding="utf-8", errors="replace").replace("\r", "")
    result = {
        "arm": arm, "seed": seed, "sessions": sessions, "cands_per_session": cands,
        "final_size": len(real), "dup_ratio": round(ratio, 4), "dup_pairs": pairs,
        "contradictions_injected": total_c, "contradictions_unresolved": unresolved,
        "precision_at_3": prec, "dormant": dormant, "archived": archived,
        "l1_chars_final": len(l1_text),
        "l1_entries_final": len([x for x in l1_text.split("§") if x.strip()]),
        "total_items": len(real) + len([x for x in l1_text.split("§") if x.strip()]),
        "decisions": decisions, "gate_errors": gate_errors,
        "gate_wall_time_s": round(gate_time, 2), "curve": curve,
    }
    if keep:
        # baselines.py 等下游评测需要终态库（调用方负责清理）
        return result, root
    shutil.rmtree(root, ignore_errors=True)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=r"<USER_HOME>\memory-ecology-compat")
    ap.add_argument("--out", default=str(Path(__file__).parent / "results"))
    ap.add_argument("--seeds", default="42,43,44,45,46")
    ap.add_argument("--sessions", type=int, default=30)
    ap.add_argument("--cands", type=int, default=10)
    ap.add_argument("--arms", default="raw,g1,full")
    a = ap.parse_args()

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    repo = Path(a.repo)
    all_results = []
    for seed in [int(s) for s in a.seeds.split(",")]:
        for arm in a.arms.split(","):
            t0 = time.perf_counter()
            r = run_arm(repo, arm, seed, a.sessions, a.cands)
            r["total_wall_s"] = round(time.perf_counter() - t0, 2)
            all_results.append(r)
            (out_dir / f"ab-{arm}-seed{seed}.json").write_text(
                json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[done] arm={arm} seed={seed} size={r['final_size']} dup={r['dup_ratio']} "
                  f"contra={r['contradictions_unresolved']}/{r['contradictions_injected']} "
                  f"p@3={r['precision_at_3']} dormant={r['dormant']} arch={r['archived']} "
                  f"l1={r['l1_chars_final']} ({r['total_wall_s']}s)", flush=True)

    print("\n==== 汇总（均值） ====")
    print(f"{'arm':6} {'size':>7} {'dup_ratio':>10} {'contra_unres':>13} {'p@3':>6} "
          f"{'dormant':>8} {'arch':>5} {'l1_chars':>9} {'total':>7} {'gate_s':>7}")
    for arm in a.arms.split(","):
        rs = [r for r in all_results if r["arm"] == arm]
        if not rs:
            continue
        print(f"{arm:6} {statistics.mean(r['final_size'] for r in rs):7.1f} "
              f"{statistics.mean(r['dup_ratio'] for r in rs):10.4f} "
              f"{statistics.mean(r['contradictions_unresolved'] for r in rs):13.2f} "
              f"{statistics.mean(r['precision_at_3'] for r in rs):6.3f} "
              f"{statistics.mean(r['dormant'] for r in rs):8.1f} "
              f"{statistics.mean(r['archived'] for r in rs):5.1f} "
              f"{statistics.mean(r['l1_chars_final'] for r in rs):9.0f} "
              f"{statistics.mean(r['total_items'] for r in rs):7.1f} "
              f"{statistics.mean(r['gate_wall_time_s'] for r in rs):7.2f}")
    (out_dir / "summary.json").write_text(json.dumps(all_results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
