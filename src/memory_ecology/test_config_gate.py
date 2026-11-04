# -*- coding: utf-8 -*-
"""gate_max_output 配置解析单测（2026-10-31，配 MAX_OUTPUT 配置化）。

覆盖三级优先级与安全阀语义：非法值/空值/0 一律回默认，不许把吞吐上限配没。
路径装配沿用本目录测试约定（test_p0_fixes.py）：模块目录 + lib 入 sys.path。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))

import lib.config as cfgmod  # noqa: E402
import write_gate as wg  # noqa: E402
from lib.config import gate_max_output  # noqa: E402


class TestGateMaxOutput(unittest.TestCase):
    def test_default_is_8(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMORY_ECOLOGY_GATE_MAX_OUTPUT", None)
            with mock.patch.object(cfgmod, "cfg", lambda s, k, d: d):
                self.assertEqual(gate_max_output(8), 8)
                self.assertEqual(wg.MAX_OUTPUT, 8)

    def test_env_wins_over_toml(self):
        with mock.patch.dict(os.environ, {"MEMORY_ECOLOGY_GATE_MAX_OUTPUT": "32"}):
            with mock.patch.object(cfgmod, "cfg", lambda s, k, d: 99):
                self.assertEqual(gate_max_output(8), 32)

    def test_toml_used_when_no_env(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMORY_ECOLOGY_GATE_MAX_OUTPUT", None)
            with mock.patch.object(cfgmod, "cfg", lambda s, k, d: 24):
                self.assertEqual(gate_max_output(8), 24)

    def test_invalid_values_fall_back_to_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMORY_ECOLOGY_GATE_MAX_OUTPUT", None)
            for bad in ("0", "-3", "abc", "", None):
                with mock.patch.object(cfgmod, "cfg", lambda s, k, d: bad):
                    self.assertEqual(gate_max_output(8), 8, f"bad={bad!r} 应回默认")


if __name__ == "__main__":
    unittest.main()
