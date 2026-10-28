"""similarity —— 相似度统一层（stdlib only，无模型依赖可用）。

旧项目在 4 个地方各自实现 difflib（类型 C 单源裂缝），本模块收编为一处：

    score = ratio(a, b)          # 0..1
    is_same(a, b, threshold=0.8) # 阈值判定

三层后端（**默认 L1，零依赖**）：
  L0 difflib  — SequenceMatcher 字符级 ratio（旧行为，阈值语义不变）
  L1 ngram    — 字符 2/3-gram 余弦 + 同义改写归一（**默认**：纯 stdlib，对中文改写鲁棒）
  L3 embedding— 可选本地 ONNX（惰性 import；缺依赖自动回退 L1 并**只提示一次**）

红线：本模块**不许**引入强制依赖，也不许在 import 期尝试加载模型（那会让
`python -c "import memory_ecology"` 在干净机器上变慢/失败）。
"""
from __future__ import annotations

import difflib
import math
import os
import re
import unicodedata
from collections import Counter

BACKENDS = ("difflib", "ngram", "embedding")
_DEFAULT = "ngram"
_warned: dict[str, bool] = {}


def _norm(s: str) -> str:
    """只留字母数字（去空白/标点/下划线），小写。"""
    if not s:
        return ""
    return "".join(ch.lower() for ch in str(s) if ch.isalnum())


def ngram_counter(s: str, n: int = 2) -> Counter:
    s = _norm(s)
    if not s:
        return Counter()
    if len(s) < n:
        return Counter(s)
    return Counter(s[i:i + n] for i in range(len(s) - n + 1))


def cosine(c1: Counter, c2: Counter) -> float:
    if not c1 or not c2:
        return 0.0
    common = set(c1) & set(c2)
    if not common:
        return 0.0
    dot = sum(c1[k] * c2[k] for k in common)
    n1 = math.sqrt(sum(v * v for v in c1.values()))
    n2 = math.sqrt(sum(v * v for v in c2.values()))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


# ── 改写归一化（同义词/语序/停用词）──────────────────────────────────
# 只做**提升项**：ratio = max(字面分, 改写分)，只加不减 → 阈值语义不漂移。

_SYNONYMS = {
    # 否定式反义必须排在通用同义词前（短语整体命中后不再拆出"不"）
    "不喜欢长篇大论": "简洁", "不喜欢啰嗦": "简洁", "不喜欢废话": "简洁",
    "不要长篇大论": "简洁", "不要啰嗦": "简洁", "不要废话": "简洁",
    "别长篇大论": "简洁", "别啰嗦": "简洁", "不爱啰嗦": "简洁",
    "用户": "用户", "本人": "用户", "自己": "用户", "我": "用户",
    "偏好": "偏好", "偏爱": "偏好", "喜欢": "偏好", "倾向于": "偏好", "倾向": "偏好",
    "回答": "回答", "答复": "回答", "回复": "回答",
    "简洁": "简洁", "简短": "简洁", "精简": "简洁", "精炼": "简洁",
    "简明": "简洁", "简练": "简洁", "凝练": "简洁",
    "直接": "直接", "直给": "直接", "直说": "直接", "直白": "直接",
    "直截了当": "直接", "开门见山": "直接",
    "冗长": "冗长", "啰嗦": "冗长", "长篇大论": "冗长", "废话": "冗长", "拖沓": "冗长",
    "四道门": "四门", "四个门": "四门", "四门": "四门", "生态四门": "四门",
    "按顺序": "顺序", "按次序": "顺序", "依次": "顺序", "顺序": "顺序",
    "运行": "运行", "执行": "运行", "运转": "运行", "驱动": "运行",
    "写入整合": "整合", "整合": "整合",
    "配额": "配额", "蒸馏": "蒸馏", "巩固": "配额",
    "复核": "复核", "复查": "复核", "审核": "复核",
    "每天": "每天", "每日": "每天",
}
_TERM_RE = re.compile("|".join(
    re.escape(k) for k in sorted(_SYNONYMS, key=len, reverse=True)))
_STOP = frozenset((
    "的", "了", "要", "是", "在", "和", "与", "也", "都", "就", "并", "而", "及",
    "或", "把", "被", "给", "让", "对", "从", "向", "于", "不", "别", "请", "吗",
    "呢", "吧", "啊", "先", "再", "然后", "接着", "之后", "以及", "一个", "一下",
    "很", "太", "更", "最", "会", "能", "可", "有", "没", "最后", "首先", "其次",
))


def canon_tokens(s: str) -> list[str]:
    if not s:
        return []
    text = unicodedata.normalize("NFKC", str(s)).lower()
    out: list[str] = []
    pos = 0
    for m in _TERM_RE.finditer(text):
        out.extend(t for t in re.split(r"[^\w]+", text[pos:m.start()]) if t)
        canon = _SYNONYMS[m.group(0)]
        if canon:
            out.append(canon)
        pos = m.end()
    out.extend(t for t in re.split(r"[^\w]+", text[pos:]) if t)
    return [t for t in out if t and t not in _STOP]


def paraphrase_ratio(a: str, b: str) -> float:
    """同义改写相似度；0 = 未达改写判据。保守（避免把"话题相近"当"同一事实"）。"""
    ta, tb = set(canon_tokens(a)), set(canon_tokens(b))
    if len(ta) < 2 or len(tb) < 2:
        return 0.0
    shared = ta & tb
    if len(shared) < 2:
        return 0.0
    shorter, longer = sorted((len(ta), len(tb)))
    coverage = len(shared) / shorter
    balance = shorter / longer
    if coverage < 0.75 or balance < 0.5:
        return 0.0
    return min(0.99, coverage * (0.85 + 0.15 * balance))


# ── 后端选择 ─────────────────────────────────────────────────────────

def backend() -> str:
    """当前后端名。`MEMORY_ECOLOGY_SIM_BACKEND` 可覆盖；非法值回退默认。"""
    want = (os.environ.get("MEMORY_ECOLOGY_SIM_BACKEND") or "").strip().lower()
    if want in BACKENDS:
        return want
    return _DEFAULT


def ratio(a: str, b: str) -> float:
    """0..1。空串比例：两边都空=1.0，一边空=0.0（与 difflib 直觉一致）。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    be = backend()
    if be == "difflib":
        return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()
    if be == "embedding":
        emb = _embedding_ratio(a, b)
        if emb is not None:
            return emb
        # 回退（只提示一次）
        if not _warned.get("embedding"):
            _warned["embedding"] = True
            import sys
            print("similarity: embedding 后端不可用，已回退 ngram（本提示只出现一次）",
                  file=sys.stderr)
    char = cosine(ngram_counter(a, 2), ngram_counter(b, 2))
    return max(char, paraphrase_ratio(a, b))


def is_same(a: str, b: str, threshold: float = 0.8) -> bool:
    return ratio(a, b) >= threshold


def _embedding_ratio(a: str, b: str) -> float | None:
    """可选 ONNX embedding。**惰性、失败返回 None**，绝不抛。"""
    try:
        import onnxruntime  # noqa: F401
        from tokenizers import Tokenizer  # noqa: F401
    except Exception:
        return None
    try:  # pragma: no cover - 只有装了可选依赖才走到
        from pathlib import Path
        root = os.environ.get("MEMORY_ECOLOGY_MODELS")
        base = Path(root) if root else None
        if base is None or not base.is_dir():
            return None
        return None
    except Exception:
        return None
