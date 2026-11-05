#!/usr/bin/env python3
"""健康评分模型 v4 回归（2026-11-05 重做）。

v3 的实测症状（来自 5 份历史体检报告的真实分布，不是推测）：
  1. **分数长期卡死**：2026-10-23/10-30/11-04/11-05 四连 75 分不动，
     而这期间 cron 报错从 1 个涨到 5 个又回落——分数对这个变化完全不敏感。
  2. **悬崖效应**：cron 报错 1 个和 25 个都扣 10 分；必需字段缺失 11 项和
     100 项都扣 10 分。
  3. **无正向维度**：缓存命中率 98.6%（掉到 50% 成本 ×8）根本不进分。
  4. **量纲不统一**：重复副本 1 组扣 15，cron 报错 1 个也扣 10——前者几乎不影响
     使用，后者意味着生产任务没跑。

本文件钉住 v4 的四条契约：连续性 / 单调性 / 正向维度与封顶 / 不猜。
运行：python -m pytest tests/test_health_score_v4.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# **不要把 `REPO/src` 永久压进 sys.path**（2026-11-05 踩到）：
# 本仓库的 `src/memory_ecology/` 是 **v3.1 历史线镜像**（没有 root.py），
# 一旦它抢到 `memory_ecology` 包名，同进程里后面的测试
# （test_gui_v3_2_0 的 `_root_readonly` → `import memory_ecology.root`）就会
# 导入失败并被静默回落，导致"只读判定"整组失效——表现为**单独跑绿、一起跑红**。
# 正确做法：用 importlib 按文件路径直接加载体检脚本，绕开包名竞争。
_PROD_SCRIPTS = Path.home() / "AppData" / "Local" / "hermes" / "scripts"
if not (_PROD_SCRIPTS / "lib" / "config.py").is_file():
    pytest.skip("需要 Hermes 树的 lib/ 才可导入体检脚本", allow_module_level=True)
# lib.* 是平级包（`from lib.config import ...`），必须让它在 sys.path 上；
# 但它与 memory_ecology 包名不冲突，加进去是安全的。
if str(_PROD_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PROD_SCRIPTS))


def _load_health_module():
    """按路径加载 `src/memory_ecology/eco_health_check.py`，不污染包名。

    脚本里的 `from lib.config import hermes_root` 依赖上面那条 sys.path，
    其余依赖（json/math/pathlib）都在标准库。
    """
    import importlib.util
    path = REPO / "src" / "memory_ecology" / "eco_health_check.py"
    spec = importlib.util.spec_from_file_location("_eco_health_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


h = _load_health_module()


def _base(**over):
    """健康的基线：176 技能、无重复、无悬空、覆盖率达标、无缺失。"""
    kw = dict(skills=[{}] * 176, dups=[], dangling=[], ver_rate=0.96,
              missing={}, cron_errs=[], mem_over=False, backup_flags=[])
    kw.update(over)
    return kw


def _score(**kw):
    return h.health_score(**kw)[0]


class TestContinuity:
    """① 连续性：缺陷数量变化必须反映到分数上（v3 的死穴）。"""

    def test_cron_errors_are_monotonic_and_distinct(self):
        """cron 报错 1/2/3/5 个必须给出**严格递减**的不同分数。

        v3 在这里给 1 个和 25 个完全一样的 75 分，于是"病情加重"看不出来。
        """
        scores = [_score(**_base(cron_errs=["x"] * n)) for n in (0, 1, 2, 3, 5)]
        for a, b in zip(scores, scores[1:]):
            assert b < a, f"cron 报错增加但分数没降：{scores}"
        assert len(set(scores)) == len(scores), f"存在相同分数（不连续）：{scores}"

    def test_field_missing_is_monotonic(self):
        scores = [_score(**_base(missing={"v": n})) for n in (0, 10, 43, 100)]
        for a, b in zip(scores, scores[1:]):
            assert b < a, f"字段缺失增加但分数没降：{scores}"

    def test_no_cliff_at_v3_boundary(self):
        """v3 在字段缺失 10→11 处有 5→10 的断崖；v4 必须平滑（单步跌幅 < 3）。"""
        s10 = _score(**_base(missing={"v": 10}))
        s11 = _score(**_base(missing={"v": 11}))
        assert s10 - s11 < 3, f"10→11 项仍有断崖：{s10} → {s11}"

    def test_saturation_never_exceeds_weight(self):
        """饱和函数必须收敛：无穷多缺陷也不能把该项扣分扣到超过设定权重。"""
        w = h.SCORE_V4_WEIGHTS["cron_errs"]["weight"]
        huge = h._sat(10 ** 6, w, h.SCORE_V4_WEIGHTS["cron_errs"]["scale"])
        assert huge <= w + 1e-9, f"饱和扣分越界：{huge} > {w}"
        assert huge > w * 0.99, "饱和值应逼近权重上限"


class TestPositiveDimensions:
    """② 正向维度：v3 只罚不奖，导致"躺平的 75"。"""

    def test_cache_hit_adds_points(self):
        low = _score(**_base(missing={"v": 43}), cache_hit_rate=0.50)
        high = _score(**_base(missing={"v": 43}), cache_hit_rate=0.98)
        assert high > low, f"缓存命中率更高但分数没更高：{low} vs {high}"

    def test_cache_hit_below_floor_gives_nothing(self):
        """50% 及以下不加分（成本已经 ×8，不该奖励）。"""
        a = _score(**_base(missing={"v": 43}))
        b = _score(**_base(missing={"v": 43}), cache_hit_rate=0.30)
        assert b == a, f"低于下限仍加了分：{a} → {b}"

    def test_bonus_is_capped(self):
        """加分封顶：再健康也不能靠刷正向项盖过真实缺陷。"""
        many = _score(**_base(missing={"v": 43}), cache_hit_rate=1.0,
                      search_ready=True, memory_fresh=True)
        none = _score(**_base(missing={"v": 43}))
        assert many - none <= h.SCORE_V4_BONUS_CAP + 1, \
            f"加分超过封顶 {h.SCORE_V4_BONUS_CAP}：{none} → {many}"

    def test_bonus_cap_does_not_exceed_100(self):
        s = _score(**_base(), cache_hit_rate=1.0, search_ready=True, memory_fresh=True)
        assert s <= 100, f"总分越界：{s}"


class TestNoGuessing:
    """③ 不猜：采集不到的维度不参与打分（传 None），不按 0 或满分臆断。"""

    def test_none_positive_inputs_are_ignored(self):
        a = _score(**_base(missing={"v": 43}))
        b = _score(**_base(missing={"v": 43}), cache_hit_rate=None,
                   search_ready=None, memory_fresh=None)
        assert a == b, "None 与「未传」应等价"

    def test_empty_skill_library_not_penalized_for_ver_rate(self):
        """空库 0/0 不该因"版本覆盖率 0%"扣分（v3 的 P2 修正，v4 必须保留）。"""
        with_skills = _score(**_base(skills=[{}], ver_rate=0.5))
        without = _score(**_base(skills=[], ver_rate=0.0))
        assert without > with_skills, "空库被版本覆盖率误罚了"


class TestDetailReadability:
    """④ 明细可解释：加分带 +、扣分带 -，封顶要注明。"""

    def test_detail_has_signed_values(self):
        _, _, _, detail = h.health_score(**_base(missing={"v": 43}),
                                         cache_hit_rate=0.9861, search_ready=True)
        assert "(-" in detail and "(+" in detail, detail

    def test_detail_notes_cap_when_exceeded(self):
        _, _, _, detail = h.health_score(**_base(), cache_hit_rate=1.0,
                                        search_ready=True, memory_fresh=True)
        assert "封顶" in detail, f"加分被封顶却没说明：{detail}"

    def test_score_range(self):
        for kw in (_base(), _base(dups=[1] * 50, dangling=[1] * 50, missing={"v": 999},
                                 cron_errs=["x"] * 99, backup_flags=["b"] * 9, mem_over=True),
                   _base(missing={"v": 43})):
            s, light, verdict, _ = h.health_score(**kw)
            assert 0 <= s <= 100, f"分数越界：{s}"
            assert light in ("🟢 健康", "🟡 亚健康", "🔴 欠健康")


class TestModelVersionSeparated:
    """⑤ 模型版本：v4 与 v3 的历史不能混算趋势。"""

    def test_model_constant_is_v4(self):
        assert h.SCORE_V4_VERSION == "v4"

    def test_weights_table_is_complete(self):
        """权重表必须覆盖 v3 的全部八个扣分维度（不能漏项）。"""
        expected = {"dups", "dangling", "cron_errs", "backup_flags",
                    "field_miss", "mem_over", "ver_rate"}
        assert expected <= set(h.SCORE_V4_WEIGHTS), \
            f"权重表缺维度：{expected - set(h.SCORE_V4_WEIGHTS)}"
        for k, v in h.SCORE_V4_WEIGHTS.items():
            assert v["weight"] > 0 and v["scale"] > 0, k
