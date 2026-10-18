# -*- coding: utf-8 -*-
"""P2 基线对比（RQ2）——四臂同卷事实问答评测。

四臂（同一 30 会话 × 300 候选工作负载、同一 60 事实问题集）：
  full-context  全部候选按会话顺序拼成转录，整段作为上下文
  rag-tfidf     无治理记忆库（300 条候选全部入库），TF-IDF 字符 n-gram 检索 top-5
  mem0          Mem0 OSS 默认管线（LLM=DeepSeek，嵌入=HF 多语言 MiniLM，逐条 add）
  governed      本系统 A2 完整管线终态库（门级排序 top-5 + L1 常驻注入）

判定：LLM 作答后按确定性词面匹配（答案含当前正确 token）；
     另记 stale 泄漏（答案只含已被替代的旧 token）。
API key 只从环境变量 DEEPSEEK_API_KEY 读取，绝不写入任何文件。

用法:
  DEEPSEEK_API_KEY=sk-... python baselines.py --arms full-context,rag-tfidf,governed,mem0
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import ablation  # 同目录：事实宇宙 + 工作负载 + 治理管线

BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"


# ---------------------------------------------------------------------------
# LLM（DeepSeek，OpenAI 兼容）
# ---------------------------------------------------------------------------
def make_client():
    from openai import OpenAI
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("缺少 DEEPSEEK_API_KEY 环境变量（key 不落盘，只走环境变量）")
    return OpenAI(api_key=key, base_url=BASE_URL)


def chat(client, system: str, user: str, max_retry: int = 3) -> str:
    last = None
    for i in range(max_retry):
        try:
            r = client.chat.completions.create(
                model=MODEL, temperature=0.0,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}])
            return r.choices[0].message.content or ""
        except Exception as e:  # 网络抖动重试
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"LLM 调用失败: {last}")


ANSWER_SYS = (
    "你是一个使用长期记忆的助手。只依据下面提供的记忆材料回答问题；"
    "若记忆之间存在冲突或出现『迁移/更新/已改为』等字样，以最新信息为准。"
    "回答必须极简，只给答案本身（名称+版本号），不要解释。"
)


# ---------------------------------------------------------------------------
# 工作负载转写
# ---------------------------------------------------------------------------
def build_workload(seed: int, sessions: int, cands: int):
    events = ablation.plan_workload(seed, sessions, cands)
    transcript_lines = []       # 会话转录（full-context 用）
    candidates = []             # (session, text, kind, fact) 候选流
    introduced, contradicted = {}, set()
    for i, evs in enumerate(events):
        transcript_lines.append(f"[会话 {i + 1}]")
        for kind, payload in evs:
            text, _tok = ablation.candidate_text(kind, payload)
            transcript_lines.append(f"- {text}")
            candidates.append({"session": i, "text": text, "kind": kind, "fact": payload})
            if kind == "novel" and payload["id"] not in introduced:
                introduced[payload["id"]] = i
            if kind == "contra":
                contradicted.add(payload["id"])
    transcript = "\n".join(transcript_lines)
    return transcript, candidates, introduced, contradicted


def question_set(introduced: dict, contradicted: set):
    """每事实一问；期望答案 = v2（若矛盾已注入）否则 v1；stale = 被替代的旧 token。"""
    qs = []
    for f in ablation.FACTS:
        if f["id"] not in introduced:
            continue  # 从未进入工作负载的事实不作答
        if f["id"] in contradicted:
            qs.append({"fact": f["id"], "q": f"{f['query']} 现在是什么？",
                       "expected": f["t2"], "stale": f["t1"]})
        else:
            qs.append({"fact": f["id"], "q": f"{f['query']} 是什么？",
                       "expected": f["t1"], "stale": None})
    return qs


def judge(answer: str, q: dict) -> dict:
    a = answer
    correct = q["expected"] in a
    stale = bool(q["stale"]) and (q["stale"] in a) and not correct
    return {"fact": q["fact"], "correct": correct, "stale": stale, "answer": a.strip()[:120]}


# ---------------------------------------------------------------------------
# 各臂上下文提供者
# ---------------------------------------------------------------------------
def ctx_full_context(transcript: str, qs):
    return {q["fact"]: transcript for q in qs}


def ctx_rag_tfidf(candidates, qs, topn: int = 5):
    """TF-IDF 字符 2/3-gram 检索（numpy 实现，无 sklearn 依赖）。"""
    docs = [c["text"] for c in candidates]
    def ngrams(t: str):
        t = re.sub(r"\s+", "", t)
        return [t[i:i + n] for n in (2, 3) for i in range(len(t) - n + 1)]
    dfs = Counter()
    doc_tf = []
    for d in docs:
        gs = ngrams(d)
        tf = Counter(gs)
        doc_tf.append(tf)
        dfs.update(tf.keys())
    n_docs = len(docs)
    vocab = {g: i for i, g in enumerate(dfs)}
    idf = [math.log(n_docs / dfs[g]) + 1.0 for g in vocab]

    def vec(text: str):
        v = [0.0] * len(vocab)
        for g, c in Counter(ngrams(text)).items():
            if g in vocab:
                v[vocab[g]] = (1.0 + math.log(c)) * idf[vocab[g]]
        nrm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / nrm for x in v]

    dvecs = [vec(d) for d in docs]
    out = {}
    for q in qs:
        qv = vec(q["q"])
        sims = [sum(a * b for a, b in zip(qv, dv)) for dv in dvecs]
        top = sorted(range(len(docs)), key=lambda i: -sims[i])[:topn]
        out[q["fact"]] = "\n".join(f"- {docs[i]}" for i in top)
    return out


def ctx_mem0(candidates, qs, workdir: Path, topn: int = 5):
    from mem0 import Memory
    key = os.environ["DEEPSEEK_API_KEY"]
    config = {
        "llm": {"provider": "openai", "config": {
            "model": MODEL, "api_key": key,
            "openai_base_url": BASE_URL + "/v1", "temperature": 0.0}},
        "embedder": {"provider": "huggingface", "config": {
            "model": "paraphrase-multilingual-MiniLM-L12-v2",
            "embedding_dims": 384}},  # mem0 2.2.1 因上游改名拿不到自动维度，须显式声明
        "vector_store": {"provider": "qdrant", "config": {
            "path": str(workdir / "qdrant"), "collection_name": "bench",
            "embedding_model_dims": 384}},
        # 注：mem0 2.2.1 的 faiss 后端在 300 条规模下检索返回空 memory 字段
        # （疑似其缺陷），改用其默认 qdrant 本地后端后正常。复现记录见交付清单。
    }
    try:
        m = Memory.from_config(config)
    except Exception:
        m = Memory(config)  # 兼容不同 mem0 版本的构造签名
    t0 = time.perf_counter()
    for c in candidates:  # 逐条写入，让 Mem0 自己的写时管线做决策
        m.add(c["text"], user_id="bench")
    build_s = round(time.perf_counter() - t0, 1)
    out = {}
    for q in qs:
        # mem0 2.2.1：user_id 须走 filters 参数（顶层参数已被拒绝）
        hits = m.search(q["q"], filters={"user_id": "bench"}, limit=topn)
        mems = [h["memory"] if isinstance(h, dict) and "memory" in h else str(h)
                for h in (hits if isinstance(hits, list) else hits.get("results", []))]
        out[q["fact"]] = "\n".join(f"- {x}" for x in mems)
    return out, build_s


def ctx_governed(repo: Path, seed: int, sessions: int, cands: int, qs, topn: int = 5):
    """本系统 A2 终态库：L1 常驻（非填充）始终注入 + 门级排序 top-5。"""
    result, root = ablation.run_arm(repo, "full", seed, sessions, cands, keep=True)
    entries = ablation.load_entries(root)
    l1_notes = [x.strip() for x in (root / "memories" / "MEMORY.md")
                .read_text(encoding="utf-8", errors="replace").replace("\r", "").split("§")
                if x.strip() and "预置水位填充" not in x]
    l1_block = "\n".join(f"- {x}" for x in l1_notes)
    out = {}
    for q in qs:
        scored = []
        for e in entries:
            if e["filler"]:
                continue
            nb = ablation.norm(e["body"])
            s = (1.0 if ablation.norm(q["q"]) in nb else 0.0) * 2 + \
                difflib_sim(ablation.norm(q["q"]), nb)
            scored.append((s, e["body"]))
        top = [b for _s, b in sorted(scored, key=lambda x: -x[0])[:topn]]
        out[q["fact"]] = (f"[常驻记忆]\n{l1_block}\n[检索结果]\n"
                          + "\n".join(f"- {b}" for b in top))
    shutil.rmtree(root, ignore_errors=True)
    return out, result


def difflib_sim(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_arm_eval(name: str, ctx_map: dict, qs, client, results: list):
    correct = stale = 0
    ctx_chars = statistics.mean(len(ctx_map[q["fact"]]) for q in qs)
    for k, q in enumerate(qs):
        ans = chat(client, ANSWER_SYS,
                   f"记忆材料：\n{ctx_map[q['fact']]}\n\n问题：{q['q']}")
        j = judge(ans, q)
        correct += j["correct"]
        stale += j["stale"]
        if (k + 1) % 20 == 0:
            print(f"  [{name}] {k + 1}/{len(qs)} 已评", flush=True)
    n = len(qs)
    row = {"arm": name, "questions": n,
           "accuracy": round(correct / n, 4),
           "stale_rate": round(stale / n, 4),
           "avg_context_chars": round(ctx_chars)}
    results.append(row)
    print(f"[done] {row}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=r"<USER_HOME>\memory-ecology-compat")
    ap.add_argument("--out", default=str(Path(__file__).parent / "results"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sessions", type=int, default=30)
    ap.add_argument("--cands", type=int, default=10)
    ap.add_argument("--arms", default="full-context,rag-tfidf,governed,mem0")
    a = ap.parse_args()

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = make_client()
    transcript, candidates, introduced, contradicted = build_workload(a.seed, a.sessions, a.cands)
    qs = question_set(introduced, contradicted)
    print(f"工作负载: {a.sessions} 会话 × {a.cands} = {len(candidates)} 候选；问题 {len(qs)} 个", flush=True)
    results = []
    summary_path = out_dir / "baselines-summary.json"
    if summary_path.exists():  # 增量重跑：保留已有臂的结果
        results = [r for r in json.loads(summary_path.read_text(encoding="utf-8"))
                   if r["arm"] not in a.arms.split(",")]

    if "full-context" in a.arms:
        run_arm_eval("full-context", ctx_full_context(transcript, qs), qs, client, results)
    if "rag-tfidf" in a.arms:
        run_arm_eval("rag-tfidf", ctx_rag_tfidf(candidates, qs), qs, client, results)
    if "governed" in a.arms:
        ctx, r = ctx_governed(a.repo, a.seed, a.sessions, a.cands, qs)
        run_arm_eval("governed", ctx, qs, client, results)
        (out_dir / "baselines-governed-run.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    if "mem0" in a.arms:
        tmp = Path(tempfile.mkdtemp(prefix="eco-mem0-"))
        try:
            ctx, build_s = ctx_mem0(candidates, qs, tmp)
            run_arm_eval("mem0", ctx, qs, client, results)
            results[-1]["mem0_build_s"] = build_s
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    (out_dir / "baselines-summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n==== 汇总（seed {}） ====".format(a.seed))
    print(f"{'arm':14} {'acc':>7} {'stale':>7} {'ctx_chars':>10}")
    for r in results:
        print(f"{r['arm']:14} {r['accuracy']:7.3f} {r['stale_rate']:7.3f} "
              f"{r['avg_context_chars']:10.0f}")


if __name__ == "__main__":
    main()
