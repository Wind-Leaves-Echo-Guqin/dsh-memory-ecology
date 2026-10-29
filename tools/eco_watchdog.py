#!/usr/bin/env python3
"""eco_watchdog — 宿主之外的看门狗（v2.5.6，审计 P1-1）。

为什么必须在宿主之外：`eco_health_alert` 自己也是 cron —— 网关一停，它同样不跑，
于是「面板全绿」与「管线已死」可以同时成立。审计实测：网关 2026-10-21 12:43
停止后，12/13 个 eco 任务 `next_run_at` 全部过期，而 jobs.json 里仍是
`enabled / state=scheduled / last_status=ok`；`VERSION.md` 写完 `cron 25/25 ok`
之后 **3 分钟** 网关就停了。

三条判据（任一命中即告警，退出码 1）：
  W1 心跳    cron/ticker_last_success 距今 > 2h（ticker 每分钟走一次，任何任务触发都会更新）
  W2 任务    任一 enabled 任务的 next_run_at 落后 now 超过 2h
  W3 网关    gateway_state.json 的 gateway_state != "running"

告警通道（v1，明确降级，不假装闭环）：
  · logs/eco_watchdog.log         追加一行（历史）
  · 桌面「记忆生态告警.txt」       每次覆盖写成当前状态摘要（人一眼能看到）
  · 退出码 1                      任务计划器的「上次运行结果」可见
没有推送通道 —— 见交接文档开放问题（inject_events 只有 3 行，注入侧无审计）。

用法:
  python tools/eco_watchdog.py              # 检查并落盘
  python tools/eco_watchdog.py --quiet      # 只在告警时输出
  python tools/eco_watchdog.py --prod <根> --max-lag-min 120

退出码: 0 健康 / 1 有告警 / 2 自身出错
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_MAX_LAG_MIN = 120
ALERT_FILE = "记忆生态告警.txt"


def cloud_gateway_state() -> str:
    """云端网关 qqbot 状态；未知/不可达返回 'unknown'。

    v3.0（2026-10-24）：生产已迁云端常驻、本地网关默认关闭。生产在云端时，
    本地心跳/任务/网关三项判据全部失真（每小时误报"心跳停摆"），必须跳过。
    """
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "ecs",
             "python3 -c \"import json;print(json.load(open("
             "'/opt/qqbot/hermes/gateway_state.json'))['platforms']['qqbot']['state'])\""],
            capture_output=True, text=True, timeout=20,
            creationflags=0x08000000 if os.name == "nt" else 0)
        return (r.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def parse_ts(raw):
    """解析时间戳：unix 秒（float）、ISO8601（可能带 +08:00 / Z）、'YYYY-MM-DD HH:MM'。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return dt.datetime.fromtimestamp(float(raw))
    text = str(raw).strip()
    if not text:
        return None
    try:
        return dt.datetime.fromtimestamp(float(text))
    except ValueError:
        pass
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def check_heartbeat(cron: Path, max_lag: dt.timedelta, now: dt.datetime) -> list[str]:
    problems = []
    beat = parse_ts((cron / "ticker_last_success").read_text(encoding="utf-8",
                                                             errors="replace")
                    if (cron / "ticker_last_success").is_file() else None)
    if beat is None:
        problems.append(f"{cron / 'ticker_last_success'} 缺失或无法解析：cron 从未成功触发过")
        return problems
    lag = now - beat
    if lag > max_lag:
        problems.append(f"心跳停摆：ticker_last_success = {beat:%Y-%m-%d %H:%M:%S}，"
                        f"已落后 {lag.total_seconds() / 3600:.1f}h（阈值 {max_lag.total_seconds() / 3600:.0f}h）")
    return problems


def _iter_jobs(data: dict):
    """从 jobs.json 取出任务列表，兼容三种历史布局。

    生产 cron/jobs.json（网关写入）的顶层是
        {"jobs": [ {...}, ... ], "updated_at": "..."}
    而旧版/测试夹具可能直接是 {job_id: {...}} 的 dict-of-jobs。
    原实现 `for job in data.values()` 只能读后者，在真实布局下拿到的是
    **列表本身**和 updated_at 字符串，全部被 isinstance 跳过 → W2 永远不告警。
    """
    raw = data.get("jobs")
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):                      # {"jobs": {id: job}}
        return list(raw.values())
    # 顶层就是 dict-of-jobs（旧布局）：过滤掉 updated_at 之类的标量
    return [v for v in data.values() if isinstance(v, dict)]


def check_jobs(cron: Path, max_lag: dt.timedelta, now: dt.datetime) -> list[str]:
    data = load_json(cron / "jobs.json")
    if not isinstance(data, dict):
        return [f"{cron / 'jobs.json'} 无法解析"]
    overdue = []
    for job in _iter_jobs(data):
        if not isinstance(job, dict) or not job.get("enabled"):
            continue
        nxt = parse_ts(job.get("next_run_at"))
        if nxt is None:
            overdue.append(f"{job.get('script') or job.get('id')}(next_run_at 缺失)")
            continue
        if (now - nxt) > max_lag:
            overdue.append(f"{job.get('script') or job.get('id')}"
                           f"(落后 {(now - nxt).total_seconds() / 3600:.1f}h)")
    if overdue:
        return [f"任务过期 {len(overdue)} 个："] + [f"  - {x}" for x in overdue[:15]]
    return []


def check_gateway(prod: Path) -> list[str]:
    state = load_json(prod / "gateway_state.json")
    if not isinstance(state, dict):
        return [f"{prod / 'gateway_state.json'} 无法解析"]
    kind = state.get("gateway_state")
    if kind != "running":
        return [f"网关状态 = {kind!r}（pid {state.get('pid')}，"
                f"exit_reason={state.get('exit_reason')}）—— cron 不会触发"]
    return []


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="记忆生态宿主外看门狗")
    ap.add_argument("--prod", type=Path,
                    default=Path(os.environ.get("MEMORY_ECOLOGY_PROD_ROOT")
                                 or (Path.home() / "AppData" / "Local" / "hermes")))
    ap.add_argument("--max-lag-min", type=int, default=DEFAULT_MAX_LAG_MIN)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    prod = args.prod.resolve()
    cron = prod / "cron"
    if not cron.is_dir():
        print(f"❌ 找不到 cron 目录: {cron}", file=sys.stderr)
        return 2
    now = dt.datetime.now()
    max_lag = dt.timedelta(minutes=args.max_lag_min)

    # 云端感知（v3.0）：生产在云端时本地三项判据失真，跳过并记录健康说明
    cloud_state = cloud_gateway_state()
    if cloud_state == "connected":
        stamp = f"{now:%Y-%m-%d %H:%M:%S}"
        logs = prod / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        note = f"生产在云端（qqbot {cloud_state}），本地心跳/任务/网关检查跳过"
        with open(logs / "eco_watchdog.log", "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{stamp}\tOK\t{note}\n")
        desktop = Path.home() / "Desktop"
        target = (desktop if desktop.is_dir() else prod) / ALERT_FILE
        body = [f"记忆生态看门狗 · {stamp}", "结论：✅ 健康（云端生产运行中）", "",
                f"{note}。", "本地网关按设计处于关闭状态，不是故障。"]
        try:
            target.write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
        except OSError as e:
            # 留痕便于排查（正常情况下应能写入；失败说明桌面被重定向/锁定等）
            try:
                (logs / "desktop_write_err.txt").write_text(
                    f"{stamp} {type(e).__name__}: {e}\ntarget={target}\n", encoding="utf-8")
            except Exception:
                pass
        if not args.quiet:
            print(f"✅ 健康（{stamp}）：{note}")
        return 0

    try:
        problems = (check_heartbeat(cron, max_lag, now)
                    + check_jobs(cron, max_lag, now)
                    + check_gateway(prod))
    except Exception as e:
        print(f"❌ 看门狗执行出错: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    stamp = f"{now:%Y-%m-%d %H:%M:%S}"
    logs = prod / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    verdict = "ALERT" if problems else "OK"

    # 历史行（只追加）
    with open(logs / "eco_watchdog.log", "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{stamp}\t{verdict}\t" + (" | ".join(problems) if problems else "健康") + "\n")

    # 当前状态摘要（每次覆盖，避免告警文件越滚越长）
    desktop = Path.home() / "Desktop"
    target = (desktop if desktop.is_dir() else prod) / ALERT_FILE
    body = [f"记忆生态看门狗 · {stamp}", f"结论：{'⚠️ 需要处理' if problems else '✅ 健康'}",
            f"判据：心跳 / 任务 next_run_at / 网关状态，阈值 {args.max_lag_min} 分钟", ""]
    body += problems if problems else ["无告警。"]
    try:
        target.write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
    except OSError as e:
        print(f"⚠️ 桌面告警文件写入失败（{e}），日志仍已记录", file=sys.stderr)

    if problems:
        print(f"⚠️ {verdict}（{stamp}）：")
        for p in problems:
            print(f"  {p}")
        print(f"📄 {target}")
        return 1
    if not args.quiet:
        print(f"✅ 健康（{stamp}）：心跳/任务/网关三项正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
