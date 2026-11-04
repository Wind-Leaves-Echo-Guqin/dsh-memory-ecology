# -*- coding: utf-8 -*-
"""lib/llm 端点/模型解析回归（2026-10-31，401 断流根因）。

历史 bug：complete_json 的 model/base_url 默认值是 DEFAULT_* 常量（truthy），
resolve_* 只对 None 生效 → 不传参的调用方永远打到 api.deepseek.com 官方，
第三方网关（faro relay）的 key 在官方端点必然 401。本文件钉死：
不传参时必须发生解析（env > eco.toml > DEFAULT），显式参数仍最高优先。
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))

import lib.llm as llm  # noqa: E402
import urllib.request  # noqa: E402

_ENV_BASE = "https://relay.example.com/v1/chat/completions"
_ENV_MODEL = "test-model-from-env"

_FAKE_PAYLOAD = {"choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}]}


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Capture:
    """替身 urlopen：记录 Request，回固定 200 JSON。"""

    def __init__(self):
        self.requests: list[urllib.request.Request] = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        return _FakeResp(json.dumps(_FAKE_PAYLOAD).encode("utf-8"))

    @property
    def last(self):
        return self.requests[-1]


def _capture_call(fn, *args, **kwargs):
    cap = _Capture()
    with mock.patch.object(llm.config, "env_key", return_value="test-key"), \
         mock.patch.object(urllib.request, "urlopen", cap):
        out = fn(*args, **kwargs)
    return out, cap


class TestCompleteJsonResolves(unittest.TestCase):
    def test_no_args_resolves_from_env(self):
        """回归主用例：不传 model/base_url 也必须解析（旧 bug 永远打官方端点）。"""
        env = {"MEMORY_ECOLOGY_API_BASE": _ENV_BASE, "MEMORY_ECOLOGY_API_MODEL": _ENV_MODEL}
        with mock.patch.dict(os.environ, env):
            out, cap = _capture_call(llm.complete_json, "说 hi", max_tokens=8, require=list)
        self.assertEqual(out, [])
        self.assertEqual(cap.last.full_url, _ENV_BASE)
        body = json.loads(cap.last.data.decode("utf-8"))
        self.assertEqual(body["model"], _ENV_MODEL)
        self.assertEqual(cap.last.headers.get("Authorization"), "Bearer test-key")

    def test_env_base_wins_over_default_not_official(self):
        """旧 bug 的直接后果是打到 DEFAULT_BASE_URL（官方）——这里反向钉死。"""
        with mock.patch.dict(os.environ, {"MEMORY_ECOLOGY_API_BASE": _ENV_BASE}):
            _, cap = _capture_call(llm.complete_json, "说 hi", max_tokens=8, require=list)
        self.assertNotEqual(cap.last.full_url, llm.DEFAULT_BASE_URL)
        self.assertEqual(cap.last.full_url, _ENV_BASE)

    def test_explicit_args_still_win(self):
        _, cap = _capture_call(
            llm.complete_json, "说 hi", max_tokens=8, require=list,
            model="explicit-model", base_url="https://explicit.example/v1/chat/completions")
        self.assertEqual(cap.last.full_url, "https://explicit.example/v1/chat/completions")
        self.assertEqual(json.loads(cap.last.data.decode())["model"], "explicit-model")

    def test_complete_resolves_from_env_too(self):
        with mock.patch.dict(os.environ, {"MEMORY_ECOLOGY_API_BASE": _ENV_BASE}):
            _, cap = _capture_call(llm.complete, "说 hi", max_tokens=8)
        self.assertEqual(cap.last.full_url, _ENV_BASE)


if __name__ == "__main__":
    unittest.main()
