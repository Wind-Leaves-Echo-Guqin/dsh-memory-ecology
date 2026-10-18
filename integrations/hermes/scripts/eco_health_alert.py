#!/usr/bin/env python3
"""生态健康告警（memory-skill-ecosystem 项目 3.6 健康双保险①）。

检测 cron 任务「连续 2 次失败」→ 输出告警（no_agent cron 模式：stdout 非空即投递，空则静默）。

用法: python eco_health_alert.py
"""
import datetime
import sqlite3
import sys
from pathlib import Path

from lib.config import hermes_root
from lib.metrics import MEMORY_TRIGGER, chars_of, parse_l1

EXEC_DB = hermes_root() / "cron" / "executions.db"
JOBS = hermes_root() / "cron" / "jobs.json"
PENDING = hermes_root() / "memories" / "pending"
MEMORY = hermes_root() / "memories" / "MEMORY.md"
MEM_LINE = MEMORY_TRIGGER  # 与配额门②触发线同源（PORT_SPEC §4-C 口径单源）


def check_extract_output() -> list[str]:
    """产出探针：近 7 天 pending 应有候选 .md 文件。
    管道空转（进程活着但零产出）是 2026-08-29 事故盲区——watermark 在动≠在提取。
    2026-08-29 修复 eco_extract 分批逻辑后新增本探针。"""
    alerts = []
    try:
        # OCR 2026-10-03: pending 目录缺失单独报——Path.glob 对不存在目录静默产出空，
        # 原先「目录没了」与「管道空转」混为同一条告警
        if not PENDING.is_dir():
            alerts.append(f"⚠️ pending 目录不存在: {PENDING}——数据目录缺失（非管道空转），请检查挂载/迁移")
            return alerts
        now = datetime.datetime.now().timestamp()
        cands = []
        for p in PENDING.glob("*.md"):
            if p.name.endswith((".done.md", ".rejected.md")):  # Q22 配套：已消费不算候选
                continue
            try:
                if (now - p.stat().st_mtime) <= 7 * 86400:
                    cands.append(p)
            except OSError:
                continue  # 单个文件 stat 失败不拖垮整轮扫描
        if not cands:
            wm = ""
            wm_err = ""
            try:
                wm = (PENDING / ".watermark").read_text().strip()
            except OSError as e:
                # OCR 2026-10-03: 水位线读取失败不再与「无水位线」混同
                wm_err = str(e)
            wm_disp = wm[:16] if wm else (f"读取失败: {wm_err[:60]}" if wm_err else "无")
            alerts.append(
                f"⚠️ eco_extract 近 7 天零候选（水位线 {wm_disp}）"
                "——管道可能空转，检查 state.db 消息源与 LLM 调用"
            )
    except Exception as e:
        alerts.append(f"⚠️ 产出探针异常: {e}")
    return alerts


def check_memory_watermark() -> list[str]:
    """记忆水位检查：MEMORY.md 按 eco_quota 门②同口径（chars_of）计数，
    超触发线（配额 × 85%）即告警。

    此前告警脚本每小时运行但不查水位——越线后只能等次日 12:25 配额门，
    出现「GUI 黄条报越线、告警通道静默」的盲区（T3）。
    v0.3 回填自 live 并升级：阈值与计数器从 eco_quota 同源导入，
    替换 live 版的去空白口径与硬编码 2550（PORT_SPEC §2）。"""
    alerts = []
    try:
        if MEMORY.is_file():
            # parse_l1 返回 (entries, nl) 元组——先解包再计数（探针抓出的移植 bug）
            entries, _nl = parse_l1(MEMORY.read_text(encoding="utf-8", errors="replace"))
            chars = chars_of(entries)
            if chars > MEM_LINE:
                alerts.append(
                    f"⚠️ 记忆水位越线: MEMORY.md {chars} 字符 > 触发线 {MEM_LINE}"
                    f"（超 {chars - MEM_LINE}）——等待配额门挤出，或手动运行 python eco_quota.py"
                )
    except Exception as e:
        alerts.append(f"⚠️ 记忆水位检查异常: {e}")
    return alerts


def main() -> int:
    from lib.cli_base import bootstrap as _bootstrap  # v2.5.0：编码兜底
    _bootstrap()
    # 产出探针最先执行（不依赖 cron DB，DB 异常也不影响探针生效）
    alerts = []
    alerts.extend(check_extract_output())
    alerts.extend(check_memory_watermark())

    # cron 失败检测：瞬时锁/IO 异常降级为仅探针，绝不整脚本崩溃
    try:
        conn = sqlite3.connect(f"file:{EXEC_DB}?mode=ro", uri=True, timeout=10)
        try:
            cur = conn.cursor()
            try:
                # OCR 2026-10-03: 「每 job 最近 2 次」下推 SQL（窗口函数）——原先全表
                # 读取+Python 分组，executions 随小时级 cron 无限增长
                cur.execute(
                    "SELECT job_id, status, error, started_at FROM ("
                    "  SELECT job_id, status, error, started_at,"
                    "         ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY started_at DESC) AS rn"
                    "  FROM executions"
                    ") WHERE rn <= 2 ORDER BY started_at DESC")
            except sqlite3.OperationalError:
                # 老版本 SQLite 无窗口函数 → 退化为有界最近 500 行
                cur.execute(
                    "SELECT job_id, status, error, started_at FROM executions "
                    "ORDER BY started_at DESC LIMIT 500")
            rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        alerts.append(f"⚠️ cron executions.db 不可读: {e}（仅产出探针生效）")
        rows = []

    # 每个 job 最近 2 次
    by_job: dict[str, list] = {}
    for jid, status, error, started in rows:
        by_job.setdefault(jid, []).append((status, error, started))

    # job 名映射
    names = {}
    try:
        import json
        data = json.loads(JOBS.read_text(encoding="utf-8"))
        for j in data.get("jobs", []):
            names[j["id"]] = j.get("name", j["id"])
    except Exception as e:
        # OCR 2026-10-03: jobs.json 损坏不再静默降级——至少留下诊断告警
        alerts.append(f"⚠️ jobs.json 不可读: {e}（job 名称映射降级为原始 id）")

    # Q14 修复（2026-09-06）：此处原为无条件 `alerts = []`，把上面产出探针的结果
    # 清空——探针永远无法告警（8-29 事故盲区修复自身失效）。改为保留探针结果。
    for jid, execs in by_job.items():
        recent = execs[:2]  # started_at DESC 排序下取最近 2 次
        # OCR 2026-10-03: 状态白名单加 "error"（jobs.json 同词表），时间取最近一次失败
        if len(recent) >= 2 and all(e[0] in ("failed", "error") for e in recent):
            t0 = (recent[0][2] or "")[:16]
            err = (recent[0][1] or "")[:150]
            alerts.append(f"⚠️ cron 连续失败×2: {names.get(jid, jid)}（最近 {t0}）\n    {err}")

    if alerts:
        print("## 生态健康告警")
        print("\n".join(alerts))
        print(f"\n（检测时间 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}，详情: hermes cron list）")
        return 0
    # 静默（无告警不输出）
    return 0


if __name__ == "__main__":
    sys.exit(main())
