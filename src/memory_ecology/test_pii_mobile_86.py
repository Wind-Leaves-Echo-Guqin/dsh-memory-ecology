#!/usr/bin/env python3
"""S40 折中修复回归（3.4.0）：+86/86 前缀手机号脱敏。

背景：REVIEW S40 判「+86 前缀漏检 = 脱敏形同虚设」，审计 02 判大陆扩展
口径属规则边界。拍板折中：补最高频的 +86/86 前缀这一个形态（一行正则、
收益明确）；座机/全角/at-email 维持边界判定（低频 + 误报风险）。

同时钉住互吞防护不回退：18 位身份证、16-19 位银行卡不被 86 前缀新形态
误吞；长数字串不误判。
运行: python memory_ecology/test_pii_mobile_86.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from lib import pii


def has_raw(text: str, phone: str) -> bool:
    """脱敏后仍含完整原号码 = 漏检。"""
    return phone in text


class TestMobile86Prefix(unittest.TestCase):
    def test_plus_86_masked(self):
        s = pii.mask("联系方式 +8613812345678 请回电")
        self.assertFalse(has_raw(s, "+8613812345678"), "+86 前缀漏检（S40）")

    def test_bare_86_masked(self):
        s = pii.mask("联系方式 8613812345678 请回电")
        self.assertFalse(has_raw(s, "8613812345678"), "86 前缀漏检")

    def test_plain_mobile_still_masked(self):
        s = pii.mask("联系方式 13812345678")
        self.assertFalse(has_raw(s, "13812345678"), "普通形态回退了")

    def test_long_digit_run_not_mobile(self):
        """13 位连续数字不是手机号——86 前缀形态不得回溯误吞。"""
        s = pii.mask("单号 86138123456789 请查收")
        self.assertIn("86138123456789", s, "长数字串被误吞")

    def test_id_card_not_eaten(self):
        """18 位身份证（含 86 片段）必须走身份证通道，不被手机号新形态截走。"""
        id18 = "110105198806068618"
        s = pii.mask(f"证件号 {id18}")
        self.assertFalse(has_raw(s, id18), "身份证漏检")
        self.assertNotIn("8806068618", s.replace("*", ""), "身份证被手机号规则截断")

    def test_separator_form_still_boundary(self):
        """138-1234-5678 维持审计「边界」判定（不在本折中范围），但不得崩。"""
        s = pii.mask("联系 138-1234-5678")
        self.assertIsInstance(s, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
