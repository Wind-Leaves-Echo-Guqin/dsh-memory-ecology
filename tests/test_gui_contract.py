# -*- coding: utf-8 -*-
"""gui-v3 前端契约测试（README-v3.2.0 §五）。

新增一个视图要动**四处**（lib/ui.tsx 的 ViewId / VIEW_TITLE、components/kit.tsx
的 APPS、components/AppWindow.tsx 的 ViewRouter），漏任何一处都不报错、只静默
失效——这套断言把"四处必须完全一致"钉死。另钉两条实测踩过的坑：

- 主屏不许有重名图标（v3.2.0 起急救箱是真应用后，旧占位曾造成两个「急救箱」）
- 代码里不许出现 ``v32``——它读起来像"版本 32"，与 3.2.0 毫无关联，已误导过一次

这些文件曾经存在但丢失（2026-10-31 重建）；与 README 描述一一对应。
"""
import re
from pathlib import Path

import pytest

GUI_V3_SRC = Path(__file__).resolve().parents[1] / "integrations" / "gui-v3" / "src"


def _read(rel: str) -> str:
    p = GUI_V3_SRC / rel
    assert p.is_file(), f"契约依赖的源文件缺失：{p}"
    return p.read_text(encoding="utf-8")


def _view_ids() -> set[str]:
    """lib/ui.tsx 的 ViewId 联合类型成员。"""
    s = _read("lib/ui.tsx")
    m = re.search(r"export type ViewId\s*=(.*?)(?:\n\n|\nexport )", s, re.S)
    assert m, "ui.tsx 里找不到 ViewId 联合类型"
    return set(re.findall(r"'([A-Za-z0-9_]+)'", m.group(1)))


def _view_titles() -> dict[str, str]:
    s = _read("lib/ui.tsx")
    m = re.search(r"export const VIEW_TITLE: Record<ViewId, string> = \{(.*?)\n\}", s, re.S)
    assert m, "ui.tsx 里找不到 VIEW_TITLE"
    return dict(re.findall(r"(\w+):\s*'([^']*)'", m.group(1)))


def _apps() -> list[dict[str, str]]:
    s = _read("components/kit.tsx")
    m = re.search(r"export const APPS: AppDef\[\] = \[(.*?)\n\]", s, re.S)
    assert m, "kit.tsx 里找不到 APPS 注册表"
    out = []
    for block in re.findall(r"\{([^{}]*)\}", m.group(1)):
        item = dict(re.findall(r"(\w+):\s*'([^']*)'", block))
        if item.get("id"):
            out.append(item)
    return out


def _router_cases() -> set[str]:
    s = _read("components/AppWindow.tsx")
    m = re.search(r"function ViewRouter\(\{ view \}: \{ view: ViewId \}\) \{\s*switch \(view\) \{(.*?)\n  \}", s, re.S)
    assert m, "AppWindow.tsx 里找不到 ViewRouter switch"
    return set(re.findall(r"case '(\w+)':", m.group(1)))


def test_viewid_matches_apps_registry():
    """APPS 注册表必须与 ViewId 完全一致（多一个=主屏多余图标，少一个=视图打不开）。"""
    ids, apps = _view_ids(), {a["id"] for a in _apps()}
    assert apps == ids, f"APPS 与 ViewId 不一致：多 {apps - ids}，少 {ids - apps}"


def test_viewid_matches_view_title():
    """每个 ViewId 都要有中文标题（VIEW_TITLE），且没有幽灵键。"""
    ids, titles = _view_ids(), set(_view_titles())
    assert titles == ids, f"VIEW_TITLE 与 ViewId 不一致：多 {titles - ids}，少 {ids - titles}"


def test_view_router_covers_all_views():
    """ViewRouter 必须能路由每一个 ViewId（dashboard 走 default 分支，允许不在 case 里）。"""
    ids, cases = _view_ids(), _router_cases()
    missing = ids - cases - {"dashboard"}
    assert not missing, f"ViewRouter 缺分支：{missing}"
    assert cases <= ids, f"ViewRouter 出现未知视图：{cases - ids}"


def test_home_screen_has_no_duplicate_app_names():
    """主屏不许有重名图标（v3.2.0 实测踩过：两个「急救箱」）。"""
    names = [a["name"] for a in _apps()]
    dup = {n for n in names if names.count(n) > 1}
    assert not dup, f"APPS 里存在重名图标：{dup}"


def test_dock_apps_exist():
    """DOCK 常驻应用必须是已注册的 ViewId。"""
    s = _read("components/kit.tsx")
    m = re.search(r"export const DOCK: ViewId\[\] = \[([^\]]*)\]", s)
    assert m, "kit.tsx 里找不到 DOCK"
    dock = set(re.findall(r"'(\w+)'", m.group(1)))
    assert dock <= _view_ids(), f"DOCK 引用了未注册视图：{dock - _view_ids()}"


def test_switcher_cruise_order_valid():
    """巡航轮播 ORDER 里的每个视图都必须真实存在（漏一处只静默跳过）。"""
    s = _read("components/Switcher.tsx")
    m = re.search(r"const ORDER: ViewId\[\] = \[([^\]]*)\]", s)
    assert m, "Switcher.tsx 里找不到 ORDER"
    order = set(re.findall(r"'(\w+)'", m.group(1)))
    assert order <= _view_ids(), f"ORDER 引用了未注册视图：{order - _view_ids()}"


def test_goto_shortcut_targets_valid():
    """App.tsx 的 g 前缀跳转表 GOTO 值必须是已注册视图。"""
    s = _read("App.tsx")
    m = re.search(r"const GOTO: Record<string, ViewId> = \{(.*?)\}", s, re.S)
    assert m, "App.tsx 里找不到 GOTO"
    targets = set(re.findall(r":\s*'(\w+)'", m.group(1)))
    assert targets <= _view_ids(), f"GOTO 引用了未注册视图：{targets - _view_ids()}"


def test_every_view_has_nonempty_title():
    """标题不许占位空串（iOS 外壳的导航栏直接用它）。"""
    for vid, title in _view_titles().items():
        assert title.strip(), f"视图 {vid} 的标题是空的"


class TestNoVersionLeak:
    """命名纪律（README §六）：代码里不许出现 `v32`。"""

    def test_no_v32_token_in_source(self):
        offenders = []
        for p in GUI_V3_SRC.rglob("*"):
            if p.suffix not in (".ts", ".tsx", ".css"):
                continue
            if "v32" in p.read_text(encoding="utf-8", errors="replace"):
                offenders.append(str(p.relative_to(GUI_V3_SRC)))
        assert not offenders, f"源码出现 `v32`（应写 v3.2.0 或语义名）：{offenders}"

    def test_no_v32_in_filename(self):
        offenders = [p.name for p in GUI_V3_SRC.rglob("*") if "v32" in p.name]
        assert not offenders, f"文件名出现 `v32`：{offenders}"
