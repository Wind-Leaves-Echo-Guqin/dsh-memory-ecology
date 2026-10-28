#!/usr/bin/env python3
"""记忆生态 · 观测舱 GUI 服务 v0.3（可操作驾驶舱 · 纯离线 · 零依赖）。

用法:  python eco_gui.py [--port 8788] [--root DIR] [--scripts-dir DIR]
                         [--no-browser] [--no-window]
- 默认绑定 127.0.0.1（仅本机回环），端口占用自动上扫
- 装有 pywebview 则开原生窗口；否则开系统浏览器；--no-window 仅起服务
- 读通道：文件读取 / SQLite mode=ro / 检索 CLI 子进程（fail-open）
- v0.3 写通道：POST /api/action/<name> —— 每个动作=子进程调用既有生产 CLI
  （GUI 不自己实现写逻辑，天然继承生产审计），仅本机回环可调，
  动作前后写 action_log.jsonl 账本；前端确认闸门拦截（gate.js）。
- v0.3 可观测：全局异常落 gui_error.log（轮转 1MB）；/api/healthz 探针。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

try:  # pythonw（双击启动器）下 stdout/stderr 为 None，不能裸调 reconfigure
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import collector  # noqa: E402
import runner      # noqa: E402
try:                       # v3.2.0 数据层（dsh 根）。缺失时新视图降级为"不可用"，不影响旧视图
    import collector_v3_2_0  # noqa: E402
except Exception:          # noqa: BLE001
    collector_v3_2_0 = None

STATIC = HERE / "static"
V2_DIST = HERE.parent / "gui-v2" / "dist"  # gui-v2 构建产物（决策 28 预览版；dist 不入库，npm run build 重新生成）
V22_DIST = HERE.parent / "gui-v2.2" / "dist"  # gui-v2.2（Sentri 设计系统版）构建产物；dist 不入库，npm run build 重新生成
# 当前发布版：gui-v3（液态玻璃 / iOS 外壳），版本号与核心对齐 3.2.0。
# 历史版本 gui-v2.3 目录保留（用户要求保留全部历史版本），但**不再作为发布入口**。
V3_DIST = HERE.parent / "gui-v3" / "dist"
V23_DIST = HERE.parent / "gui-v2.3" / "dist"  # 旧版，仅为兼容旧链接保留
_MIME = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
         ".woff2": "font/woff2", ".json": "application/json"}

GUI_LOG_DIR = HERE
ERROR_LOG = GUI_LOG_DIR / "gui_error.log"
ACTION_LOG = GUI_LOG_DIR / "action_log.jsonl"

# ── 动作白名单（T5）─────────────────────────────────────────────────────
# 每个动作 = 子进程调用生产 CLI；risk: strong=强确认（红字+勾选），light=轻确认（单次弹窗）
# argv 由服务端按动作类型拼装，前端只能传参数，不能传任意命令。
ACTIONS: dict[str, dict] = {
    "quota_extrude": {"name": "立即配额挤出", "risk": "strong",
                      "impact": "对超线 L1 记忆执行挤出沉淀（生产门②同款逻辑）",
                      "rollback": "写前自动备份 .bak；被挤出条目沉入 L2 详情层，可人工回迁",
                      "dry": ["eco_quota.py", "--dry-run"], "run": ["eco_quota.py"]},
    "gate_integrate": {"name": "写入门整合", "risk": "strong",
                       "impact": "消费 pending/ 候选写入 L2 详情层（生产门①同款逻辑）",
                       "rollback": "候选源文件保留（.done 标记），写前自动备份",
                       "dry": ["write_gate.py", "--dry-run"], "run": ["write_gate.py"]},
    "note_adopt": {"name": "经验候选采纳", "risk": "strong",
                   "impact": "pending 候选 → experiences 正式条目（外源候选自动降级 draft）",
                   "rollback": "候选源保留 .accepted 标记不删除；条目可人工下架",
                   "dry": ["eco_note_adopt.py", "--dry-run"], "run": ["eco_note_adopt.py"]},
    "skill_breed": {"name": "技能孵化", "risk": "strong",
                    "impact": "按名字/亲代/动机在 skills/.candidates 生成候选技能（观察期）",
                    "rollback": "候选目录可直接删除（未入正库）",
                    "form": True,
                    "dry": ["eco_breed.py", "--name", "{name}", "--sources", "{sources}",
                            "--motivation", "{motivation}", "--dry-run"],
                    "run": ["eco_breed.py", "--name", "{name}", "--sources", "{sources}",
                            "--motivation", "{motivation}"]},
    "cron_rerun": {"name": "cron 重跑", "risk": "strong",
                   "impact": "让调度器在下个 tick 重跑指定任务（hermes cron run）",
                   "rollback": "任务本身幂等；误重跑无破坏性",
                   "hermes": ["cron", "run", "{job_id}"]},
    "cron_pin": {"name": "cron 固定模型", "risk": "strong",
                 "impact": "将指定任务钉在当前 provider/model，消除 drift_skip（hermes cron edit）",
                 "rollback": "可再次 edit 改回；只影响该任务",
                 "hermes": ["cron", "edit", "{job_id}", "--provider", "{provider}",
                            "--model", "{model}"]},
    "health_run": {"name": "立即体检", "risk": "light",
                   "impact": "生成体检报告 + 按日归档（只读，约 30-60s）",
                   "rollback": "无需回滚（只读）",
                   "dry": None, "run": ["eco_health_check.py"], "timeout": 180},
    "index_rebuild": {"name": "重建检索索引", "risk": "light",
                      "impact": "强制重建 ecosystem.db 检索索引（只读生态文件）",
                      "rollback": "无需回滚",
                      "dry": None, "run": ["eco_search.py", "--rebuild"], "timeout": 180},
    "firstaid_fix": {"name": "急救箱修复", "risk": "light",
                     "impact": "清陈旧锁 + 重建过期索引（缓存与锁层面，不动数据本体）",
                     "rollback": "锁文件会由写入方自动重建",
                     "special": "firstaid"},

    # ── v3.2.0 写动作（全部转发给核心 CLI，GUI 不碰任何文件）──
    # 纪律（决策 D6）：GUI **永不写记忆内容**（不写 detail/MEMORY/user/pending、
    # 不跑门改数据）。只允许圈配置、记忆包启停、幂等重建这三类。
    "circle_write": {"name": "写入圈配置", "risk": "strong",
                     "impact": "改变哪些启动目录归到哪个圈、每圈目录预算。下一次会话装配时生效。",
                     "rollback": "核心写前自动备份 circles.json.bak-改圈前-*，可整体还原",
                     "core": "circle_write"},
    "pack_enable": {"name": "启用记忆包", "risk": "light",
                    "impact": "把已登记的只读记忆包纳入目录（只读引用，内容不进本库）",
                    "rollback": "再点停用即可；物理文件始终保留",
                    "core": "pack_enable"},
    "pack_disable": {"name": "停用记忆包", "risk": "strong",
                     "impact": "目录里不再出现该包的内容（不影响本库记忆）",
                     "rollback": "再点启用即可；物理文件始终保留",
                     "core": "pack_disable"},
    "core_maintain": {"name": "立即跑四门", "risk": "light",
                     "impact": "整合候选 → 配额巩固 → 画像蒸馏 → 复核降级（全部幂等，写前自动备份）",
                     "rollback": "各门写前都备份（MEMORY.md.bak-配额前-* / USER.md.bak-蒸馏前-*）",
                     "core": "maintain"},
    "core_materialize": {"name": "重建目录视图", "risk": "light",
                        "impact": "重新生成 .eco/catalog/*.json（插件热路径只读它）；幂等",
                        "rollback": "无需回滚（纯派生数据，可反复重生成）",
                        "core": "materialize"},
}

# 子进程环境。**必须带根**：GUI 用 --root 起在沙盒上时，若子进程只看 os.environ，
# 写动作会落到生产根（实测：沙盒 GUI 的"跑四门"写到了 %LOCALAPPDATA%\hermes）。
# 而 `_utf8_env` 之前是 **dict 却被当函数调用**（`env=_utf8_env()`），
# 于是 9 个写动作里有 7 个在 spawn 之前就 TypeError —— 写通道整体是死的。
def _utf8_env() -> dict:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        env["MEMORY_ECOLOGY_ROOT"] = str(collector.data_root())
    except Exception:  # noqa: BLE001 - 取不到根就退回原 env，不阻断
        pass
    return env


def _log_error(path: str, tb: str) -> None:
    """全局异常 → gui_error.log（轮转 1MB）。"""
    try:
        if ERROR_LOG.exists() and ERROR_LOG.stat().st_size > 1_000_000:
            ERROR_LOG.replace(ERROR_LOG.with_suffix(".1.log"))
        ERROR_LOG.open("a", encoding="utf-8").write(
            f"[{datetime.now().isoformat(timespec='seconds')}] {path}\n{tb}\n")
    except OSError:
        pass


def _log_action(entry: dict) -> None:
    """动作账本（C4）：GUI 自己的审计，与 eco.db 生态账本分开。"""
    try:
        with ACTION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def action_log_tail(limit: int = 200) -> list[dict]:
    out = []
    if ACTION_LOG.is_file():
        try:
            lines = ACTION_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-limit:]:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
    return list(reversed(out))  # 新→旧


_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _run_scripts(argv: list[str], timeout: int = 120) -> tuple[bool, str]:
    """在生产 scripts/ 目录下执行 CLI（cwd=scripts，UTF-8 env，超时可配，禁弹窗）。"""
    sd = collector.scripts_dir()
    if not sd:
        return False, "未找到生产 scripts 目录"
    try:
        r = subprocess.run([sys.executable, *argv], cwd=str(sd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=_utf8_env(), creationflags=_NO_WINDOW)
        out = (r.stdout or "") + (("\n[stderr] " + r.stderr) if r.stderr and r.returncode != 0 else "")
        return r.returncode == 0, out.strip() or f"(exit={r.returncode})"
    except subprocess.TimeoutExpired:
        return False, f"超时（>{timeout}s）"
    except OSError as e:
        return False, f"子进程启动失败：{e}"


def _run_hermes(argv: list[str], timeout: int = 60) -> tuple[bool, str]:
    hermes = None
    for c in (Path(os.environ.get("SystemDrive", "C:")) / "Users" / os.environ.get("USERNAME", "") /
              "AppData" / "Local" / "hermes" / "bin" / "hermes.exe",):
        if c.is_file():
            hermes = str(c)
            break
    if not hermes:
        try:
            import shutil
            hermes = shutil.which("hermes")
        except Exception:
            hermes = None
    if not hermes:
        return False, ("未找到 hermes CLI。请在本机手动执行：\n  hermes " + " ".join(argv))
    try:
        r = subprocess.run([hermes, *argv], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=_utf8_env(),
                           creationflags=_NO_WINDOW)
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip() or f"(exit={r.returncode})"
    except subprocess.TimeoutExpired:
        return False, f"超时（>{timeout}s）"
    except OSError as e:
        return False, f"子进程启动失败：{e}"


def _fmt_action(entry: dict) -> str:
    """动作的可复制命令文本（预览/回滚指引）。"""
    return entry.get("cmd", "")


def execute_action(name: str, params: dict, stage: str) -> dict:
    """动作执行体：stage=dry 预览 / stage=run 实跑。全部子进程调用生产 CLI。"""
    spec = ACTIONS.get(name)
    if not spec:
        return {"ok": False, "error": f"未知动作 {name}"}
    ts0 = time.time()
    entry = {"ts": datetime.now().isoformat(timespec="seconds"),
             "action": name, "stage": stage, "params": {k: str(v)[:120] for k, v in params.items()}}
    try:
        if spec.get("special") == "firstaid":
            import first_aid
            res = first_aid.diagnose(fix=(stage == "run"))
            ok, out = True, json.dumps(res, ensure_ascii=False)
        elif spec.get("core"):
            # v3.2.0 动作：**统一转发给核心 CLI**（核心自己校验 + 备份 + 原子写）。
            # GUI 绝不直接改 circles.json / pack 目录。
            if collector_v3_2_0 is None:
                ok, out = False, "collector_v3_2_0 未就绪（找不到 memory-ecology 核心）"
            else:
                res = collector_v3_2_0.run_action(spec["core"], params, stage)
                ok = bool(res.get("ok"))
                entry["cmd"] = res.get("cmd", "")
                out = (res.get("output") or "") + (
                    ("\n[error] " + str(res.get("error"))) if res.get("error") else "")
        elif spec.get("hermes"):
            argv = [a.format(**params) if "{" in a else a for a in spec["hermes"]]
            entry["cmd"] = f"hermes {' '.join(argv)}"
            ok, out = _run_hermes(argv, timeout=spec.get("timeout", 120))
        else:
            tmpl = spec.get("dry") if stage == "dry" else spec.get("run")
            if tmpl is None:
                return {"ok": False, "error": f"动作 {name} 无 {stage} 阶段"}
            argv = [a.format(**params) if "{" in a else a for a in tmpl]
            entry["cmd"] = f"python {' '.join(argv)}（cwd=生产 scripts/）"
            ok, out = _run_scripts(argv, timeout=spec.get("timeout", 120))
    except Exception as e:
        ok, out = False, f"{type(e).__name__}: {e}"
    entry.update({"ok": ok, "ms": int((time.time() - ts0) * 1000),
                  "out": out[:4000]})
    _log_action(entry)
    return {"ok": ok, "action": name, "stage": stage, "spec": {
        "name": spec["name"], "risk": spec["risk"], "impact": spec["impact"],
        "rollback": spec["rollback"]}, "output": out, "ms": entry["ms"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "EcoObservatory/0.3"

    def log_message(self, *a):  # 静默访问日志
        pass

    # ── 基础 ──
    def _json(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    _LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

    def _host_ok(self) -> bool:
        """防 DNS rebinding：恶意网页把域名解析到 127.0.0.1 时 Host 头是攻击者域名，
        客户端地址仍是回环——必须解析 Host 并精确比对主机名（startswith 会被
        localhost:8788.evil.com 之类畸形形态放行，审查 minor 修复）。"""
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):  # IPv6 字面量：[::1]:port
            inner, sep, rest = host[1:].partition("]")
            if not sep:
                return False  # 缺闭括号（[::1）——不得跳过端口校验后按回环放行
            if rest and not (rest.startswith(":") and rest[1:].isdigit()):
                return False  # ] 后跟非端口垃圾（[::1]junk 形态）——审查 note 收紧
            port = rest[1:] if rest.startswith(":") else ""
        else:
            inner, _, port = host.rpartition(":")
            if not inner:  # 无冒号＝缺省端口形态
                inner, port = host, ""
        return inner in self._LOOPBACK_HOSTS and (port == "" or port.isdigit())

    def _origin_ok(self) -> bool:
        """写通道附加校验：Origin/Referer 缺省（本机 webview/脚本调用常见），
        或其 hostname 精确为本机回环——子串匹配会被含 "localhost" 的攻击者域名
        绕过（审查 blocker 修复），故 urlparse 后只认 hostname。"""
        o = (self.headers.get("Origin") or self.headers.get("Referer") or "").strip()
        if o == "":
            return True
        try:
            u = urlparse(o)
        except ValueError:
            return False
        return u.scheme in ("http", "https") and u.hostname in self._LOOPBACK_HOSTS

    def _serve_file(self, root: Path, rel: str):
        p = (root / rel).resolve()
        try:  # commonpath 防兄弟目录前缀绕过（startswith 对 staticX 类目录名失效）
            inside = os.path.commonpath([str(p), str(root.resolve())]) == str(root.resolve())
        except ValueError:  # 跨盘符等不可比较情形一律视为越界
            inside = False
        if not inside or not p.is_file():
            self._json({"ok": False, "error": "not found"}, 404)
            return
        body = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _MIME.get(p.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel: str):
        self._serve_file(STATIC, rel)

    def _v2_static(self, rel: str):
        """gui-v2 构建产物（决策 28 预览版）——与旧 static 同款越界防护。"""
        self._serve_file(V2_DIST, rel)

    def _v22_static(self, rel: str):
        """gui-v2.2（Sentri 版）构建产物——与旧 static 同款越界防护。"""
        self._serve_file(V22_DIST, rel)

    def _v3_static(self, rel: str):
        """gui-v3（液态玻璃版，当前发布版）构建产物——与旧 static 同款越界防护。"""
        self._serve_file(V3_DIST, rel)

    def _v23_static(self, rel: str):
        """gui-v2.3（历史版本，仅为兼容旧链接保留）。"""
        self._serve_file(V23_DIST, rel)

    def do_GET(self):
        try:
            self._route()
        except BrokenPipeError:
            pass
        except Exception:
            _log_error(self.path, traceback.format_exc())
            try:
                self._json({"ok": False, "error": "服务内部错误（详见 gui_error.log）"}, 500)
            except Exception:
                pass

    def do_POST(self):
        try:
            self._route_post()
        except BrokenPipeError:
            pass
        except Exception:
            _log_error(self.path, traceback.format_exc())
            try:
                self._json({"ok": False, "error": "服务内部错误（详见 gui_error.log）"}, 500)
            except Exception:
                pass

    # ── GET 路由 ──
    def _route(self):
        if not self._host_ok():  # DNS rebinding 防护：Host 非本机回环一律拒绝
            return self._json({"ok": False, "error": "forbidden host"}, 403)
        u = urlparse(self.path)
        path, qs = unquote(u.path), parse_qs(u.query)

        if path in ("/", "/index.html"):
            # **默认入口 = 当前发布版**。以前指到旧的 static（v0.3 那一代），
            # 于是"打开观测舱"看到的永远是旧界面——新版藏在 /v2.3/ 里没人知道。
            return self._v3_static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        # **当前发布版**：/v3/ → gui-v3。"/" 也指到它（默认入口 = 最新版）。
        if path in ("/v3", "/v3/"):
            return self._v3_static("index.html")
        if path.startswith("/v3/"):
            return self._v3_static(path[len("/v3/"):])
        # 历史版本路由全部保留（用户要求保留全部历史版本，旧书签不能失效）
        if path == "/v2" or path == "/v2/":
            return self._v2_static("index.html")
        if path.startswith("/v2/"):
            return self._v2_static(path[len("/v2/"):])
        if path == "/v2.2" or path == "/v2.2/":
            return self._v22_static("index.html")
        if path.startswith("/v2.2/"):
            return self._v22_static(path[len("/v2.2/"):])
        if path == "/v2.3" or path == "/v2.3/":
            return self._v23_static("index.html")
        if path.startswith("/v2.3/"):
            return self._v23_static(path[len("/v2.3/"):])
        if not path.startswith("/api/"):
            return self._json({"ok": False, "error": "not found"}, 404)

        api = path[len("/api/"):]
        one = lambda k: (qs.get(k) or [""])[0]

        if api == "meta":
            return self._json({"ok": True, **collector.meta()})
        if api == "overview":
            return self._json({"ok": True, **collector.overview()})
        if api == "memories/l1":
            return self._json({"ok": True, **collector.scan_l1()})
        if api == "memories/l2":
            return self._json({"ok": True, "items": collector.scan_l2()})
        if api == "memories/detail":
            d = collector.detail_body(one("slug"))
            return self._json({"ok": d is not None, **({"detail": d} if d else {"error": "未找到"})})
        if api == "memories/aux":
            return self._json({"ok": True, **collector.aux_dirs()})
        if api == "experiences":
            data = collector.scan_experiences()
            items = data["items"]
            typ, status, page = one("type"), one("status"), int(one("page") or "1")
            size = 60
            if typ:
                items = [i for i in items if i["type"] == typ]
            if status:
                items = [i for i in items if i["status"] == status]
            total = len(items)
            pages = max(1, -(-total // size))
            page = min(max(1, page), pages)
            return self._json({"ok": True, "items": items[(page - 1) * size: page * size],
                               "total": total, "page": page, "pages": pages,
                               "pending": data["pending"]})
        if api == "experiences/detail":
            eid = one("id")
            for i in collector.scan_experiences()["items"]:
                if i["id"] == eid:
                    return self._json({"ok": True, "item": i})
            return self._json({"ok": False, "error": "未找到"}, 404)
        if api == "skills":
            return self._json({"ok": True, **collector.scan_skills()})
        if api == "skills/md":
            d = collector.skill_md(one("path"))
            return self._json({"ok": d is not None, **({"md": d} if d else {"error": "未找到"})})
        if api == "lineage":
            sk = collector.scan_skills()
            edges = [{"child": s["name"], "parent": s["evolved_from"], "kind": "evolved_from"}
                     for s in sk["skills"] if s["evolved_from"]]
            edges += [{"child": s["name"], "parent": s["merged_into"], "kind": "merged_into"}
                      for s in sk["skills"] if s["merged_into"]]
            return self._json({"ok": True, "edges": edges,
                               "candidates": sk["candidates"], "archive": sk["archive"],
                               "declared": sk["declared"], "total": len(sk["skills"])})
        if api == "logs":
            try:
                limit = min(int(one("limit") or "300"), 600)
            except ValueError:
                limit = 300
            return self._json({"ok": True, **collector.eco_logs(limit=limit)})
        if api == "logs/day":
            return self._json({"ok": True, "day": collector.gate_log_day(one("date"))})
        if api == "cron":
            return self._json({"ok": True, **collector.cron_info()})
        if api == "health":
            ev = collector.eval_reports()
            return self._json({"ok": True, "report": collector.health_report(),
                               "history": collector.score_history(),
                               "evals": ev[:5], "eval_latest": ev[0] if ev else None,
                               "backups": collector.backup_strata(),
                               "archives": collector.report_archives()})
        if api == "health/report":
            d = collector.report_by_name(one("name"))
            return self._json({"ok": d is not None, **({"report": d} if d else {"error": "未找到"})})
        if api == "candidates":
            return self._json({"ok": True, **collector.scan_candidates()})
        if api == "candidates/detail":
            d = collector.candidate_detail(one("kind"), one("name"))
            return self._json({"ok": d is not None, **({"detail": d} if d else {"error": "未找到"})})
        if api == "actions":
            return self._json({"ok": True, "actions": {k: {kk: vv for kk, vv in v.items()
                                                           if kk != "special"}
                                                       for k, v in ACTIONS.items()}})
        if api == "action_log":
            return self._json({"ok": True, "entries": action_log_tail()})
        if api == "search":
            return self._json({"ok": True, **runner.search(one("kind") or "note", one("q"))})
        if api == "retrieval":
            # S4（v2.3.0）：检索质量（零结果率趋势），只读经 runner 调核心 CLI
            try:
                days = min(max(int(one("days") or "30"), 0), 365)
            except ValueError:
                days = 30
            return self._json({"ok": True, **runner.retrieval_report(days)})
        if api == "firstaid":
            import first_aid
            return self._json({"ok": True, "result": first_aid.diagnose(fix=False)})

        # ── v3.2.0 新视图（dsh 根；与上面的旧根视图**双根并存，绝不混算**）──
        if api.startswith("v3.2.0/"):
            return self._route_v3(api[len("v3.2.0/"):], one)

        if api == "healthz":
            sd = collector.scripts_dir()
            v3_2_0 = collector_v3_2_0.meta() if collector_v3_2_0 is not None else {}
            return self._json({"ok": True,
                               "alive": True,
                               "data_root_ok": collector.data_root().is_dir(),
                               "search_cli_ready": bool(sd and (sd / "eco_note_query.py").is_file()),
                               "core_ok": bool(v3_2_0.get("core_ok")),
                               "core_root": v3_2_0.get("root", ""),
                               "core_version": v3_2_0.get("version", ""),
                               "ts": datetime.now().isoformat(timespec="seconds")})

        return self._json({"ok": False, "error": "unknown api"}, 404)

    # ── v3.2.0 路由（等值分发；单接口失败不炸整站）──
    _V3_ROUTES = {
        "meta": lambda m: m.meta(),
        "stats": lambda m: m.stats(),
        "circles": lambda m: m.circles(),
        "circles/config": lambda m: m.circles_config(),
        "gates": lambda m: m.gates(),
        "inject": lambda m: m.inject(),
        "cost": lambda m: m.cost(),
        "packs": lambda m: m.packs(),
        "firstaid": lambda m: m.firstaid(),
    }

    def _route_v3(self, sub: str, one):
        if collector_v3_2_0 is None:
            return self._json({"ok": False, "error": "core-not-found",
                               "detail": "collector_v3_2_0 未就绪"}, 200)
        fn = self._V3_ROUTES.get(sub)
        if fn is None:
            return self._json({"ok": False, "error": "unknown v3_2_0 api"}, 404)
        try:
            data = fn(collector_v3_2_0)
        except Exception as exc:  # noqa: BLE001 - 单接口失败不炸整站
            _log_error(self.path, traceback.format_exc())
            return self._json({"ok": False,
                               "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}, 200)
        if not isinstance(data, dict):
            data = {"value": data}
        return self._json({"ok": bool(data.get("ok", True)), **data})

    # ── POST 路由（v0.3 写通道，仅 127.0.0.1）──
    def _route_post(self):
        u = urlparse(self.path)
        path, qs = unquote(u.path), parse_qs(u.query)
        if not path.startswith("/api/action/"):
            return self._json({"ok": False, "error": "not found"}, 404)
        # 三重防护：Host 校验（防 DNS rebinding）＋ Origin/Referer 校验 ＋ 客户端地址回环校验
        if not self._host_ok() or not self._origin_ok():
            return self._json({"ok": False, "error": "写通道仅限本机回环（Host/Origin 校验失败）"}, 403)
        client = self.client_address[0] if self.client_address else ""
        if client not in ("127.0.0.1", "::1"):
            return self._json({"ok": False, "error": "写通道仅限本机回环"}, 403)
        name = path[len("/api/action/"):]
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
        except json.JSONDecodeError:
            return self._json({"ok": False, "error": "请求体非法 JSON"}, 400)
        if name == "gate_mute_reset":  # 状态栏「提醒重置」（无副作用，不入账本）
            return self._json({"ok": True})
        stage = body.get("stage") or (qs.get("stage") or ["run"])[0]
        if stage not in ("dry", "run"):
            return self._json({"ok": False, "error": "stage 须为 dry|run"}, 400)
        params = {k: str(v) for k, v in (body.get("params") or {}).items()}
        if name == "skill_breed" and stage == "run":
            if not params.get("name") or not params.get("sources"):
                return self._json({"ok": False, "error": "孵化需 name + sources（逗号分隔亲代）"}, 400)
        result = execute_action(name, params, stage)
        return self._json(result, 200 if result.get("ok") else 500)


def find_port(start: int) -> int:
    import socket
    for p in range(start, start + 21):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("127.0.0.1 无可用端口")


def _is_our_server(port: int) -> bool:
    """探测端口上是否已有本观测舱服务（/api/overview 含 score 键）。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/overview", timeout=1.5) as r:
            return "score" in json.loads(r.read().decode("utf-8"))
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="记忆生态 · 观测舱（v0.3 可操作驾驶舱）")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--root", help="数据根（默认 MEMORY_ECOLOGY_ROOT > 自动探测）")
    ap.add_argument("--scripts-dir", help="检索脚本目录（默认数据根 scripts/）")
    ap.add_argument("--no-browser", action="store_true", help="不自动开浏览器/窗口")
    ap.add_argument("--no-window", action="store_true", help="禁用 pywebview 窗口")
    ap.add_argument("--tray", action="store_true",
                    help="托盘常驻模式（需 pystray+Pillow；缺失时给出安装指引并正常起服务）")
    ap.add_argument("--eco-root", dest="eco_root",
                    help="v3.2.0 数据根（默认 MEMORY_ECOLOGY_ROOT > ~/.dsh/memory-eco-root > 布局默认）")
    ap.add_argument("--eco-core", dest="eco_core",
                    help="memory-ecology 核心包目录（默认自动探测）")
    a = ap.parse_args()
    # 端口策略：默认端口上已有本服务 → 只开窗口连过去；
    # 显式 --port 时维持旧语义（端口占用自动上扫，可并存多实例）
    if a.port == 8788 and _is_our_server(8788):
        url = "http://127.0.0.1:8788"
        print(f"观测舱已在运行 → 直接开窗口 {url}")
        tray = _start_tray(8788, url) if a.tray else None
        if a.no_browser:
            if tray is None:
                return 0
            try:
                threading.Event().wait()   # 托盘常驻：保持进程存活
            except KeyboardInterrupt:
                pass
            return 0
        _open_frontend(url, tray)
        return 0

    collector.set_root(a.root, a.scripts_dir)
    if collector_v3_2_0 is not None:
        collector_v3_2_0.set_root(a.eco_root, a.eco_core)
    port = find_port(a.port)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    m = collector.meta()
    print("╭──────────────────────────────────────────────╮")
    print("│ 记忆生态 · 观测舱 v0.3（可操作驾驶舱）        │")
    print("╰──────────────────────────────────────────────╯")
    print(f"  地址      {url}")
    print(f"  数据根    {m['root']}")
    print(f"  生态版本  {m['version']} · 评分模型 {m['score_model']}")
    print(f"  检索CLI   {'就绪' if m['search_cli_ready'] else '未找到（检索不可用，其余正常）'}")
    print("  写通道    POST /api/action/*（仅本机回环 · 全走生产 CLI · 闸门拦截）")
    print("  纪律      单写入方不受影响 · 异常落 gui_error.log · Ctrl+C 退出")
    tray = _start_tray(port, url) if a.tray else None
    if a.no_browser:
        pass
    else:
        _open_frontend(url, tray)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\n已退出。")


def _start_tray(port: int, url: str):
    """托盘常驻（T16，可选）：pystray 缺失时只提示，不影响服务。返回 Tray 或 None。"""
    try:
        from tray import Tray
    except ImportError as e:
        print(f"⚠ 托盘模块不可用（{e}）——继续以普通模式运行")
        return None
    import threading as _th
    t = Tray(port=port, url=url, on_quit=lambda: os._exit(0))
    _th.Thread(target=t.run, daemon=True).start()
    return t


def _open_frontend(url: str, tray=None):
    """开前端：pywebview 原生窗口（可选）→ 系统浏览器兜底。--tray 时窗口交给托盘管理。"""
    try:
        import webview  # pywebview 可选增强（未装自动跳过）
        win = webview.create_window("记忆生态 · 观测舱", url, width=1280, height=860)
        if tray is not None:
            tray.window = win
        webview.start()
        return
    except ImportError:
        if tray is None:
            webbrowser.open(url)


if __name__ == "__main__":
    main()
