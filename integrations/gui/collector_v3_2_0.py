#!/usr/bin/env python3
"""collector_v3_2_0 —— 观测舱 v2.4 的 v3.2.0 数据层（**只调核心 CLI，不自己实现领域逻辑**）。

为什么单独一个文件：
`collector.py` 是旧 Hermes 线（%LOCALAPPDATA%\\hermes）的采集层，29KB、几十个函数，
直接往里塞会让它失控；而 v3.2.0 的数据在 **dsh 根**（`%LOCALAPPDATA%\\dsh\\memory-eco`
或 `--eco-root` 指定的目录），是另一套账本与目录。

所以：**双根并存**。
  · 旧根（hermes）：记忆库/经验/技能/体检/时间线 —— v2.3 已有的九个视图
  · 新根（dsh）  ：圈 / 四门 / 注入 / 成本 / 记忆包 —— v3.2.0 新能力
每个新视图都显式标注口径（`source` 字段 + 前端角标），**绝不混算**。

两条硬规则（A2A §6 Stage G）：
  ① 所有数字来自 `python -m memory_ecology <cmd>` 的 JSON —— GUI 不许自己解析
     frontmatter、自己算圈、自己判门死活。否则 GUI 与插件口径必然漂移。
  ② 读的是**同一份物化视图**（`.eco/catalog/*.json`）+ 同一批账本。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_ARG_ECO_ROOT: str | None = None
_ARG_CORE: str | None = None
_CACHE: dict = {}
_TTL = {"meta": 3.0, "circles": 4.0, "gates": 3.0, "inject": 5.0, "cost": 25.0,
        "packs": 5.0, "firstaid": 8.0, "stats": 3.0}


def set_root(eco_root: str | None, core: str | None = None) -> None:
    """启动时注入（**只能在起服务之前调**，不要在热路径 per-request 切根）。"""
    global _ARG_ECO_ROOT, _ARG_CORE
    if eco_root:
        _ARG_ECO_ROOT = eco_root
    if core:
        _ARG_CORE = core
    bust()


def find_eco_core() -> Path | None:
    """自动探测 **v3.2.0** 核心包目录。

    **必须验版本，不能只验存在**：本仓库里也有 `src/memory_ecology/`（v3.1 的
    平移镜像，没有四门/圈/包），先命中它就会得到"能调但全是旧命令"的假成功
    —— 界面显示"核心可达"，实际每个新视图都 404（实测踩到）。
    所以：候选目录必须能被 `python -m memory_ecology version` 认出是 ≥3.2，
    才算找到了核心。

    探测顺序：显式参数 > 环境变量 > 同仓库兄弟目录 > 跨仓库
    `dsh-memory-ecology`（旧名 `eco-dsh` 一并认）> 已 pip 安装。
    """
    cands: list[Path] = []
    if _ARG_CORE:
        cands.append(Path(_ARG_CORE))
    env = os.environ.get("ECO_CORE")
    if env:
        cands.append(Path(env))
    here = Path(__file__).resolve()
    for rel in ("../../src", "../../../src"):
        cands.append((here.parent / rel).resolve())
    # **同仓库 `core/`**：v3.2.1 起核心引擎并入主仓库这个子目录。
    # 不加这条，"clone 下来就能用"是假的——GUI 会探测不到引擎，
    # 新视图全部 404（真核心在 core/src，不在 src）。
    for rel in ("../../core/src", "../../../core/src"):
        cands.append((here.parent / rel).resolve())
    for base in (Path("D:/work"), Path.home()):
        # 新名字优先；旧名字留着兼容（老机器上目录可能还没改名）
        for name in ("dsh-memory-ecology", "eco-dsh"):
            cands.append(base / name / "src")
            cands.append(base / name)
            cands.append(base / name / "core" / "src")
    for c in cands:
        try:
            if (c / "memory_ecology" / "cli.py").is_file() and _is_v3_2_0(c):
                return c
        except OSError:
            continue
    # 已 pip 安装（且版本够）：返回 None 表示"不用设 PYTHONPATH"
    try:
        import importlib.util
        if importlib.util.find_spec("memory_ecology"):
            return None
    except Exception:  # noqa: BLE001
        pass
    return None


_VERSION_OK: dict[str, bool] = {}


def _is_v3_2_0(src: Path) -> bool:
    """该目录里的 memory_ecology 是不是 v3.2+（有 gates/packs/circles 那套）。

    只看**模块存在**还不够：v3.1 镜像也有 cli.py 但没有 `gate` 子命令。
    这里读 `__init__.py` 的 `__version__`（零子进程，快），语义是"版本 ≥ 3.2"。
    """
    key = str(src)
    if key in _VERSION_OK:
        return _VERSION_OK[key]
    ok = False
    try:
        init = (src / "memory_ecology" / "__init__.py").read_text(
            encoding="utf-8", errors="replace")
        import re
        m = re.search(r'__version__\s*=\s*"([^"]+)"', init)
        if m:
            parts = [int(x) for x in re.split(r"[.\-+]", m.group(1))[:3]
                     if x.isdigit()]
            ok = len(parts) >= 2 and (parts[0], parts[1]) >= (3, 2)
        if not ok:
            # 没有 __version__ 的旧镜像：以"有没有 packs.py / gates.py"为判据
            ok = ((src / "memory_ecology" / "gates.py").is_file()
                  and (src / "memory_ecology" / "packs.py").is_file())
    except OSError:
        ok = False
    _VERSION_OK[key] = ok
    return ok


def ecoroot() -> Path:
    """v3.2.0 数据根：--eco-root > MEMORY_ECOLOGY_ROOT > ~/.dsh/memory-eco-root > 布局默认。"""
    if _ARG_ECO_ROOT:
        return Path(_ARG_ECO_ROOT)
    env = os.environ.get("MEMORY_ECOLOGY_ROOT")
    if env and env.strip():
        return Path(env.strip())
    cfg = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh")) / "memory-eco-root"
    try:
        if cfg.is_file():
            line = cfg.read_text(encoding="utf-8-sig").strip().splitlines()
            if line and line[0].strip():
                return Path(line[0].strip())
    except OSError:
        pass
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "dsh" / "memory-eco"
    return Path.home() / ".local" / "share" / "dsh" / "memory-eco"


def srcdir() -> Path | None:
    """核心包目录（`python -m memory_ecology` 的 PYTHONPATH）。每次调用现算。"""
    return find_eco_core()


def _env() -> dict:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    src = srcdir()
    if src is not None:
        env["PYTHONPATH"] = str(src)
    return env


def core_call(args: list[str], timeout: float = 30.0) -> dict:
    """调一次核心 CLI。**永不抛**：失败返回 `{ok:False, error}`，GUI fail-open。"""
    if srcdir() is None:
        try:
            import importlib.util
            if not importlib.util.find_spec("memory_ecology"):
                return {"ok": False, "error": "core-not-found",
                        "detail": "找不到 memory-ecology 核心包；设 ECO_CORE 指向 src/"}
        except Exception:  # noqa: BLE001
            pass
    argv = [sys.executable, "-m", "memory_ecology", "--root", str(ecoroot()), *args]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=_env(),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "detail": ">%.0fs" % timeout}
    except OSError as exc:
        return {"ok": False, "error": "spawn-failed", "detail": str(exc)[:200]}
    for line in reversed((r.stdout or "").strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            data["_code"] = r.returncode
            return data
    return {"ok": False, "error": "no-json", "detail": (r.stderr or "")[:400],
            "_code": r.returncode}


def _cached(key: str, fn):
    ttl = _TTL.get(key, 5.0)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    data = fn()
    _CACHE[key] = (now, data)
    return data


def bust() -> None:
    _CACHE.clear()


# ── 视图数据源 ────────────────────────────────────────────────────────

def meta() -> dict:
    """顶栏/锁屏：v3.2.0 根与核心可达性。与旧 collector.meta 分开（双根）。"""
    root = ecoroot()
    st = core_call(["init"], timeout=20)   # init 幂等；同时给出 root_id/version/readonly

    def build():
        return {
            "ok": True,
            "root": str(root),
            "root_exists": root.is_dir(),
            "root_kind": "dsh" if (root / ".eco" / "root.json").is_file() else "unknown",
            "version": st.get("version") or "?",
            "root_id": st.get("root_id") or "",
            "readonly": bool(st.get("readonly")),
            "readonly_reason": st.get("reason") or "",
            "core_src": str(srcdir() or "(installed)"),
            "core_ok": bool(st.get("ok")),
            "core_error": "" if st.get("ok") else (st.get("error") or st.get("reason") or ""),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    return _cached("meta", build)


def stats() -> dict:
    return _cached("stats", lambda: core_call(["stats"]))


def circles() -> dict:
    """G1：圈维度（含每圈目录预算占用）。与核心 `circles` 逐值一致。"""
    def build():
        data = core_call(["circles"])
        if not data.get("ok"):
            return {"ok": False, "error": data.get("error"), "items": [],
                    "source": "core-cli"}
        data["source"] = "core-cli"
        return data
    return _cached("circles", build)


def gates() -> dict:
    """G2：四门实况 —— **v3.2.0 最重要的一个视图**。

    每条给出：累计有效动作、期望动作计数、最后一次真实动作、距今多久、是否空转、原因。
    判据由核心 `ledger.gate_status()` 决定，GUI 只负责画红。
    """
    def build():
        h = core_call(["health"])
        if not h.get("ok"):
            return {"ok": False, "error": h.get("error"), "gates": []}
        g = h.get("gates") or {}
        return {"ok": True, "gates": g.get("gates") or [],
                "dead_count": g.get("dead_count", 0),
                "stale_days": g.get("stale_days"),
                "status": h.get("status"), "notes": h.get("notes") or [],
                "materialized": h.get("materialized"), "detail": h.get("detail"),
                "source": "core-cli health"}
    return _cached("gates", build)


def inject() -> dict:
    """G3：注入观测（按 source 聚合 + dead_rate/reload_rate，无样本时为 null）。"""
    def build():
        h = core_call(["health"])
        if not h.get("ok"):
            return {"ok": False, "error": h.get("error")}
        inj = h.get("inject") or {}
        return {"ok": True, "sources": inj.get("sources") or [],
                "total_events": inj.get("total_events", 0),
                "dead_rate": inj.get("dead_rate"), "reload_rate": inj.get("reload_rate"),
                "recent": inj.get("recent") or [],
                "kpi": {"max_messages": 3, "max_chars": 1200},
                "source": "core-cli health/inject"}
    return _cached("inject", build)


def cost() -> dict:
    """G6：成本面板。`eco_*` 只算本插件注入（全量注入单列，不参与 KPI）。"""
    def build():
        h = core_call(["health"], timeout=45)
        rows = ((h.get("cost") or {}).get("rows") or []) if h.get("ok") else []
        has_split = bool(rows) and any(r.get("eco_msgs") is not None for r in rows)
        worst_msg = max([r.get("eco_msgs") or 0 for r in rows], default=0) if has_split else 0
        worst_chars = max([r.get("eco_chars") or 0 for r in rows], default=0) if has_split else 0
        return {"ok": True, "rows": rows, "has_split": has_split,
                "catalog_chars": max([r.get("catalog") or 0 for r in rows], default=0),
                "tools_schema": max([r.get("tools_schema") or 0 for r in rows], default=0),
                "system_prompt": max([r.get("system_prompt") or 0 for r in rows], default=0),
                "worst_messages": worst_msg, "worst_chars": worst_chars,
                "total_messages": max([r.get("injected_msgs") or 0 for r in rows], default=0),
                "total_chars": max([r.get("injected_chars") or 0 for r in rows], default=0),
                "kpi": {"max_messages": 3, "max_chars": 1200,
                        "applies_to": "本插件注入源（eco-*）"},
                "over_kpi": bool(has_split and (worst_msg > 3 or worst_chars > 1200)),
                "kpi_known": has_split,
                "source": "core-cli health/cost"}
    return _cached("cost", build)


def packs() -> dict:
    """G5：记忆包（mount/integrate 状态 + 哈希校验）。"""
    def build():
        data = core_call(["pack", "list"])
        data["source"] = "core-cli pack list"
        return data
    return _cached("packs", build)


def circles_config() -> dict:
    """G4 只读：圈配置原文（写必须走核心 CLI）。"""
    p = ecoroot() / "circles.json"
    if not p.is_file():
        return {"ok": True, "circles": [], "global_budget_chars": 600,
                "path": str(p), "exists": False}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": "corrupt", "detail": str(exc)[:200], "path": str(p)}
    data["ok"] = True
    data["path"] = str(p)
    data["exists"] = True
    try:
        data["mtime"] = time.strftime("%Y-%m-%dT%H:%M:%S",
                                      time.localtime(p.stat().st_mtime))
    except OSError:
        data["mtime"] = ""
    return data


def firstaid() -> dict:
    """G7：急救箱 —— 根自证 / 物化完整性 / 账本一致性 / 四门活性。只诊断不修。"""
    checks = []
    root = ecoroot()
    root_schema, cat_schema = _core_schema()

    # 1) 根自证
    meta_p = root / ".eco" / "root.json"
    if not root.is_dir():
        checks.append({"id": "root-selfproof", "name": "根自证", "status": "fail",
                       "detail": "数据根不存在：%s" % root,
                       "action": "跑 `python -m memory_ecology init` 建根（会新建目录）"})
    elif not meta_p.is_file():
        checks.append({"id": "root-selfproof", "name": "根自证", "status": "fail",
                       "detail": "缺 .eco/root.json（根未初始化）",
                       "action": "跑 `python -m memory_ecology init`"})
    else:
        try:
            m = json.loads(meta_p.read_text(encoding="utf-8-sig"))
            sv = m.get("schema_version")
            rid = m.get("root_id") or ""
            expect = root_schema if root_schema is not None else sv
            if sv != expect:
                checks.append({"id": "root-selfproof", "name": "根自证", "status": "fail",
                               "detail": "schema_version=%s（核心期望 %s）—— 只读降级中"
                                         % (sv, expect),
                               "action": "核对核心版本与数据根版本，勿手改 root.json"})
            elif not rid:
                checks.append({"id": "root-selfproof", "name": "根自证", "status": "warn",
                               "detail": "root_id 为空（可运行但溯源会缺锚点）",
                               "action": "备份后重新 init"})
            else:
                checks.append({"id": "root-selfproof", "name": "根自证", "status": "ok",
                               "detail": "root_id=%s schema=%s" % (rid, sv), "action": ""})
        except (OSError, ValueError) as exc:
            checks.append({"id": "root-selfproof", "name": "根自证", "status": "fail",
                           "detail": "root.json 无法解析：%s" % str(exc)[:120],
                           "action": "从 .bak 恢复或重新 init（会生成新 root_id）"})

    # 2) 物化视图
    cdir = root / ".eco" / "catalog"
    idx = cdir / "index.json"
    if not cdir.is_dir():
        checks.append({"id": "catalog", "name": "物化视图", "status": "warn",
                       "detail": "还没物化过（插件会注入不到目录）",
                       "action": "跑 `python -m memory_ecology materialize`"})
    else:
        bad, stale = [], []
        try:
            scopes = (json.loads(idx.read_text(encoding="utf-8-sig")).get("scopes") or []
                      if idx.is_file() else [])
        except (OSError, ValueError):
            scopes = []
            bad.append("index.json 无法解析")
        if not idx.is_file():
            bad.append("缺 index.json")
        age_h = None
        for sc in scopes:
            f = cdir / ("%s.json" % sc)
            if not f.is_file():
                bad.append("%s.json 缺失" % sc)
                continue
            try:
                payload = json.loads(f.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                bad.append("%s.json 无法解析" % sc)
                continue
            if not payload.get("ok"):
                bad.append("%s.json 内容非 ok" % sc)
            if cat_schema is not None and payload.get("schema") != cat_schema:
                stale.append("%s schema=%s（期望 %s）"
                             % (sc, payload.get("schema"), cat_schema))
            try:
                age_h = max(age_h or 0.0, (time.time() - f.stat().st_mtime) / 3600.0)
            except OSError:
                pass
        if bad:
            checks.append({"id": "catalog", "name": "物化视图", "status": "fail",
                           "detail": "；".join(bad[:4]),
                           "action": "跑 `materialize`（幂等，可反复跑）"})
        elif stale:
            checks.append({"id": "catalog", "name": "物化视图", "status": "warn",
                           "detail": "schema 过期：" + "；".join(stale[:3]),
                           "action": "跑 `materialize`"})
        elif age_h is not None and age_h > 24:
            checks.append({"id": "catalog", "name": "物化视图", "status": "warn",
                           "detail": "最后物化在 %.1f 小时前（>24h）" % age_h,
                           "action": "下次会话自动刷新；也可手动 materialize"})
        else:
            checks.append({"id": "catalog", "name": "物化视图", "status": "ok",
                           "detail": "%d 个圈的目录，最近 %.1f 小时前"
                                     % (len(scopes), age_h or 0.0), "action": ""})

    # 3) 账本一致性（只报告，绝不自动抹除）
    st = core_call(["stats"])
    if not st.get("ok"):
        checks.append({"id": "ledger", "name": "账本一致性", "status": "warn",
                       "detail": "账本不可读：%s" % (st.get("error") or st.get("reason")),
                       "action": "确认 eco.db 存在且未被其他进程独占"})
    else:
        note = "detail %d 条（active %s）· 账本 %s" % (
            st.get("detail", 0), (st.get("by_status") or {}).get("active", 0),
            {k: st.get(k) for k in ("gate1", "gate2", "gate3", "gate4")})
        g = gates()
        if g.get("ok") and not g.get("materialized") and (st.get("detail") or 0) > 0:
            checks.append({"id": "ledger", "name": "账本一致性", "status": "fail",
                           "detail": note + "；但物化视图缺失 —— 插件注入不到目录",
                           "action": "跑 `materialize`"})
        else:
            checks.append({"id": "ledger", "name": "账本一致性", "status": "ok",
                           "detail": note, "action": ""})

    # 4) 四门活性
    g = gates()
    if g.get("ok"):
        dead = [x for x in g.get("gates", []) if x.get("dead")]
        if dead:
            checks.append({"id": "gates-live", "name": "四门活性", "status": "fail",
                           "detail": "；".join("%s：%s" % (d["name"], d["reason"])
                                              for d in dead[:3]),
                           "action": "看「四门实况」；门②③ 空转多为判据无输入"})
        else:
            checks.append({"id": "gates-live", "name": "四门活性", "status": "ok",
                           "detail": "四门都有近期有效动作", "action": ""})

    fails = [c for c in checks if c["status"] == "fail"]
    return {"ok": True, "checks": checks, "root": str(root),
            "all_ok": not fails, "fail_count": len(fails),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": "core-cli + 本地只读"}


def _core_schema() -> tuple[int | None, int | None]:
    """从核心读 schema 版本号（**不在 GUI 里硬编码**）。

    硬编码的代价：核心改了 schema，GUI 还在按老数字校验，于是"明明重新物化了
    却一直报过期"——而且要在三处同时改（正是本项目要消灭的"改格式要改四处"）。

    实现：把核心目录挂上 sys.path 再 import（**只读、零子进程**）。
    import 成功即留在进程里（核心是纯 stdlib 包，常驻无成本）；
    失败就返回 (None, None)，调用方跳过该校验而不是猜一个数字。
    """
    src = find_eco_core()
    if src is not None:
        s = str(src)
        if s not in sys.path:
            sys.path.insert(0, s)
    try:
        import importlib
        mod_root = importlib.import_module("memory_ecology.root")
        mod_ret = importlib.import_module("memory_ecology.retrieve")
        # 防御：本机可能同时存在 v3.1 镜像，确认拿到的是有 CATALOG_SCHEMA 的版本
        if not hasattr(mod_ret, "CATALOG_SCHEMA"):
            return None, None
        return int(mod_root.SCHEMA_VERSION), int(mod_ret.CATALOG_SCHEMA)
    except Exception:  # noqa: BLE001
        return None, None


# ── 写动作（全部转发给核心 CLI；GUI 不碰任何文件）──────────────────────

def run_action(name: str, params: dict, stage: str) -> dict:
    """G4/G5 写动作。每个都必须能被 `python -m memory_ecology` 一行复现。"""
    if name == "circle_write":
        payload = params.get("json") or ""
        if not payload:
            return {"ok": False, "error": "缺 json 参数（圈配置全文）"}
        if stage == "dry":
            try:
                data = json.loads(payload)
            except ValueError as exc:
                return {"ok": False, "error": "JSON 非法：%s" % str(exc)[:120]}
            errs = _validate_circles(data)
            return {"ok": not errs, "stage": "dry", "errors": errs,
                    "cmd": "python -m memory_ecology circles-write --json <…>",
                    "output": "校验通过（未写入）" if not errs else "；".join(errs)}
        return _do(["circles-write", "--json", payload], "circles-write")

    if name in ("pack_enable", "pack_disable"):
        pid = str(params.get("id") or "").strip()
        if not _safe_id(pid):
            return {"ok": False, "error": "pack id 非法（只允许字母数字 ._- ，≤64）"}
        verb = "enable" if name == "pack_enable" else "disable"
        if stage == "dry":
            return {"ok": True, "stage": "dry",
                    "output": "将执行：pack %s --id %s" % (verb, pid),
                    "cmd": "python -m memory_ecology pack %s --id %s" % (verb, pid)}
        return _do(["pack", verb, "--id", pid], "pack %s" % verb)

    if name == "maintain":
        if stage == "dry":
            return {"ok": True, "stage": "dry",
                    "output": "将顺跑四门（幂等，写前自动备份）",
                    "cmd": "python -m memory_ecology gate all"}
        return _do(["gate", "all"], "gate all", timeout=120)

    if name == "materialize":
        if stage == "dry":
            return {"ok": True, "stage": "dry", "output": "将重建物化视图（幂等）",
                    "cmd": "python -m memory_ecology materialize"}
        return _do(["materialize"], "materialize")

    return {"ok": False, "error": "未知动作 %s" % name}


def _do(argv: list[str], label: str, timeout: float = 90.0) -> dict:
    res = core_call(argv, timeout=timeout)
    bust()
    return {"ok": bool(res.get("ok")), "stage": "run", "action": label,
            "cmd": "python -m memory_ecology " + " ".join(argv),
            "output": json.dumps(res, ensure_ascii=False)[:4000], "result": res}


def _validate_circles(data) -> list[str]:
    """与核心 `store.validate_circles` 同口径的最小副本（dry 阶段不能写盘）。"""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["配置必须是 JSON 对象"]
    try:
        b = int(data.get("global_budget_chars", 600))
        if not (60 <= b <= 20000):
            errs.append("global_budget_chars 须在 60..20000")
    except (TypeError, ValueError):
        errs.append("global_budget_chars 必须是整数")
    circles = data.get("circles", [])
    if not isinstance(circles, list):
        return errs + ["circles 必须是数组"]
    seen = set()
    for i, c in enumerate(circles):
        if not isinstance(c, dict):
            errs.append("circles[%d] 不是对象" % i)
            continue
        cid = str(c.get("id") or "").strip().lower()
        if not _safe_id(cid):
            errs.append("circles[%d].id 非法：%r" % (i, c.get("id")))
        if cid == "global":
            errs.append("circles[%d].id 不能是 global" % i)
        if cid in seen:
            errs.append("circles[%d].id 重复：%s" % (i, cid))
        seen.add(cid)
        pre = (c.get("match") or {}).get("cwd_prefix")
        if pre is not None and not isinstance(pre, list):
            errs.append("circles[%d].match.cwd_prefix 必须是数组" % i)
        try:
            bb = int(c.get("budget_chars") or 600)
            if not (60 <= bb <= 20000):
                errs.append("circles[%d].budget_chars 须在 60..20000" % i)
        except (TypeError, ValueError):
            errs.append("circles[%d].budget_chars 必须是整数" % i)
    return errs


def _safe_id(value: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{2,64}", value or ""))
