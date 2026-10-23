"""redact — 输出面 PII 脱敏（v2.5.7，注入面首用；后续落库/检索/导出复用）。

定位：**显示/注入面**的兜底脱敏，不改变真源。条目正文仍按用户意愿保存，
调用方在把内容交给模型/界面/日志之前过一遍本模块——规则单源，避免每处一套正则。

覆盖：邮箱、18 位身份证、中国大陆手机号、16-19 位银行卡号、中文住址。
顺序敏感：先长后短（身份证 18 位先于银行卡 16-19 位；手机 11 位先于银行卡），
否则长号码会被短规则截断，留下残片。

v2.5.8：正则与「身份证/银行卡判别」上收到 lib/pii.py（与落库/索引/检索共用
同一套分类），本模块只保留人读占位文案，避免两套规则各自漂移。

零依赖（stdlib only）。纯函数，无 IO。
"""
from __future__ import annotations

from . import pii

# 输出顺序：邮箱 → 住址 → 身份证 → 银行卡 → 手机；与 pii.mask 同序
_PLACEHOLDERS = (
    ("email", "[邮箱已脱敏]"),
    ("address", "[住址已脱敏]"),
    ("id_card", "[身份证已脱敏]"),
    ("bank_card", "[银行卡已脱敏]"),
    ("phone", "[手机号已脱敏]"),
)


def redact_pii(text) -> str:
    """返回脱敏后的文本；None/空串原样返回（错误信息不因脱敏二次抛错）。"""
    if text is None:
        return ""
    s = str(text)
    if not s:
        return s
    for kind, repl in _PLACEHOLDERS:
        s = pii.redact_by_kind(s, kind, repl)
    return s


def contains_pii(text) -> bool:
    """是否含本模块可识别的 PII（供入口/测试快速判定；不替代脱敏）。"""
    return bool(pii.detect(text))
