"""合成生态生成器（V3_BLUEPRINT B 阶段·测试沙盒机制）。

create_eco(root, seed=42) 在 root 下构建一个迷你但结构真实的记忆生态，
供 tests/test_gates_characterization.py 以「子进程 + MEMORY_ECOLOGY_ROOT」
方式驱动真实 CLI（scripts/write_gate.py、eco_quota.py、distill_stage.py、
eco_review.py），钉住门的当前可观察行为。

结构真实 = 字段口径对齐读取方的解析期望：
- memories/detail/*.md        ← write_gate.load_detail / eco_quota.is_promo_candidate /
                                distill_stage 候选资格 / eco_review.scan_detail
- memories/pending/*.md       ← eco_extract 输出形态（"# 捕获候选" 标题 + "- [类型] 文本"
                                列表行），write_gate.parse_candidates 消费
- experiences/exp-*.md        ← eco_note_query._parse_entry（frontmatter + 正文
                                title:/action:/boundary:/evidence: 字段）
- cron/jobs.json              ← eco_health_check / gui collector 的 {"jobs": [...]} 布局
- skills/*/SKILL.md           ← eco_state.scan_skills（status/fate 取校验矩阵合法组合）

确定性：random.Random(seed) 只驱动「门行为无关」的装饰性字段（id 后缀、版本号、
会话号、job id）；所有被门读取的文本为固定常量——改变 seed 不改变任何门的判定。

零 LLM / 零网络（fixture 设计约束，不是巧合）：
- pending 候选文本与两条 detail 正文归一化相似度远低于 write_gate 的 SIM_SUSPECT
  （0.50）→ 无相似命中 → 走规则兜底直判 ADD，不调 lib.llm；
- detail 条目 occurrences=1 < distill 候选门槛 MIN_OCC(2) → 蒸馏门 dry-run 也不调 LLM。

关键沙盒事实（fixture 依赖，勿"修复"）：
- write_gate / eco_quota / distill_stage 的实例锁固定在 <root>/scripts/.xxx.lock，
  且 write_gate 连 --dry-run 也会先抢锁再删锁 → root 必须自带一个空 scripts/ 目录；
  否则 _acquire_lock 的 os.open(O_CREAT) 抛 FileNotFoundError（它只捕 FileExistsError）。
"""
from __future__ import annotations

import datetime
import json
import random
from pathlib import Path

__all__ = ["create_eco"]

# ---------------------------------------------------------------------------
# 固定文本（门行为关键区）。改动前先跑 tests/test_gates_characterization.py，
# 确认相似度隔离与规则判定（ADD/semantic）仍成立。
# ---------------------------------------------------------------------------

# L1 常驻记忆：2 条目，§ 分隔（贴近真实 L1 的平铺陈述风格）。
# 字符数远低于 2550 触发线（lib.metrics.MEMORY_TRIGGER）→ 配额门无动作。
MEMORY_ENTRIES = (
    "用户主开发机是 Windows，终端用 Git Bash，全生态文件统一 UTF-8 编码。",
    "生态四门每日 13 点后由 cron 依次运行：提取、写入整合、配额、蒸馏，全部 no_agent 模式。",
)

# USER.md：1 条目。远低于 1275 触发线，也远低于蒸馏门 90% 配额水位。
USER_ENTRY = "用户是独力开发者，偏好中文交流与简洁直接的回答，不喜欢过度装饰的措辞。"

# L2 detail：2 条目。正文互相不相似（eco_review 合并候选阈值 0.70 以下），
# 且与 PENDING 候选文本不相似（write_gate SIM_SUSPECT=0.50 以下）。
DETAIL_STEMS = ("dev-env-windowsterminal", "daily-gates-routine")
DETAIL_BODIES = (
    "用户主开发机是 Windows，常用终端为 Git Bash，运行 Python 前先设 PYTHONUTF8=1 防 GBK 乱码",
    "生态四门由调度器在午后依次驱动，任何一门失败都会在健康告警通道留下记录",
)

# pending 候选：eco_extract 输出形态。rule_type 判 semantic（不含流程/教训/一次性
# 事件特征词）→ write_gate 走 ADD，全程零 LLM。
PENDING_FILENAME = "2026-09-26.md"
PENDING_CAND_TYPE = "陈述"
PENDING_CAND_TEXT = "用户偏好深色主题与简洁排版，代码评审时希望直接给修改建议"


def _write(path: Path, text: str) -> None:
    """写文本文件，强制 LF（与 .gitattributes 的仓库口径一致，且避开
    Windows 下 write_text 默认把 \\n 翻成 CRLF 带来的口径噪声）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _dump_fm(fm: dict, body: str) -> str:
    """最小 frontmatter 序列化（与各门 dump_frontmatter 同构）。"""
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {v}")
    lines += ["---", "", body]
    return "\n".join(lines) + "\n"


def create_eco(root: Path, seed: int = 42) -> dict:
    """在 root 下构建迷你但结构真实的生态，返回路径字典。

    root 应为空目录（或至少不与生态路径冲突）；本函数幂等（重复调用覆盖同名文件）。
    返回字典键：root / version / scripts / memories / memory / user / detail /
    pending / pending_file / quarantine / gate_log / experiences / cron_jobs /
    skills / db（db 只给路径，不创建——由被测门自己落盘）。
    """
    rng = random.Random(seed)
    root = Path(root)
    today = datetime.date.today().isoformat()  # last_verified 用当天：永远"新鲜"，复核门不判超期

    memories = root / "memories"
    detail = memories / "detail"
    pending = memories / "pending"
    quarantine = memories / "quarantine"
    gate_log = memories / "gate_log"
    experiences = root / "experiences"
    cron_dir = root / "cron"
    skills = root / "skills"
    scripts = root / "scripts"   # 空目录：三个门的锁文件落点（见模块 docstring）

    for d in (scripts, detail, pending, quarantine, gate_log):
        d.mkdir(parents=True, exist_ok=True)

    paths = {
        "root": root, "scripts": scripts, "memories": memories,
        "memory": memories / "MEMORY.md", "user": memories / "USER.md",
        "detail": detail, "pending": pending, "quarantine": quarantine,
        "gate_log": gate_log, "experiences": experiences,
        "cron_jobs": cron_dir / "jobs.json", "skills": skills,
        "version": root / "VERSION.md", "db": root / "eco.db",
    }

    # VERSION.md（最小；真实生态里由 eco_version 每日重建）
    _write(paths["version"],
           "# 测试夹具生态 VERSION.md\n\n"
           "- 生态版本: v0.0.0-fixture（仅测试沙盒用，非真实版本号）\n"
           f"- 夹具生成日期: {today}\n")

    # L1：MEMORY.md（2 条目 § 分隔）
    _write(paths["memory"], MEMORY_ENTRIES[0] + "\n§\n" + MEMORY_ENTRIES[1] + "\n")
    # L1：USER.md（1 条目）
    _write(paths["user"], USER_ENTRY + "\n")

    # L2 detail：2 条目，frontmatter 字段集 = write_gate.add_entry 的 11 字段口径。
    # occurrences=1/session_count=1 → 不是 eco_quota 提升候选（≥3/≥2）、
    # 不是 distill 候选（≥2/≥2）→ 两个门零 LLM、零提升动作。
    for i, body in enumerate(DETAIL_BODIES):
        fm = {
            "type": "semantic",
            "status": "active",
            "occurrences": "1",
            "session_count": "1",
            "first_seen": "2026-09-01",
            "last_seen": "2026-09-01",
            "valid_time": "2026-09-01",
            "transaction_time": "2026-09-01T09:00:00",
            "last_verified": today,
            "origin_session_id": f"sess-fixture-{rng.randrange(10000):04d}",  # seed 只影响装饰字段
            "superseded_by": "",
        }
        _write(detail / f"{DETAIL_STEMS[i]}.md", _dump_fm(fm, body))

    # pending：1 个候选（eco_extract 输出形态：标题 + "- [类型] 文本"）
    _write(pending / PENDING_FILENAME,
           f"# 捕获候选 2026-09-26\n\n- [{PENDING_CAND_TYPE}] {PENDING_CAND_TEXT}\n")

    # experiences：2 条 exp-*.md（eco_note_query._parse_entry 可解析）
    for i, (etype, title, action, boundary, evidence) in enumerate((
        ("error", "Windows 控制台中文乱码需设 PYTHONUTF8",
         "运行 Python 子进程前设置 PYTHONUTF8=1",
         "仅 Windows 控制台场景需要，Linux 无此问题",
         "GBK 编码下 print 中文抛 UnicodeEncodeError"),
        ("pattern", "生态门测试统一走临时目录沙盒",
         "构造 fixture 后用 MEMORY_ECOLOGY_ROOT 指向沙盒",
         "只适用于支持该环境变量的脚本",
         "lib/config.py 的 hermes_root 覆盖机制"),
    )):
        exp_id = f"exp-2026090{i + 1}-{rng.randrange(10000):04d}"
        fm = {"id": exp_id, "type": etype, "status": "verified",
              "last_hit": f"2026-09-0{i + 1}"}
        body = f"title: {title}\naction: {action}\nboundary: {boundary}\nevidence: {evidence}"
        _write(experiences / f"{exp_id}.md", _dump_fm(fm, body))

    # cron/jobs.json：2 个 job（{"jobs": [...]} 布局，字段对齐 health_check / collector）
    jobs = {"jobs": [
        {"id": f"job-{rng.randrange(1000):03d}", "name": "生态提取（eco_extract）",
         "schedule": {"display": "每日 13:00"}, "enabled": True, "last_status": "ok",
         "script": "scripts/eco_extract.py"},
        {"id": f"job-{rng.randrange(1000):03d}", "name": "生态四门整合（write_gate/quota/distill）",
         "schedule_display": "每日 13:05", "enabled": True, "last_status": "ok",
         "script": "scripts/write_gate.py"},
    ]}
    _write(paths["cron_jobs"], json.dumps(jobs, ensure_ascii=False, indent=2) + "\n")

    # skills：2 个技能目录，各含最小 SKILL.md（字段覆盖 eco_state.REQUIRED_FIELDS，
    # status/fate 取校验矩阵合法组合 active/retained）
    for name, desc in (("report-helper", "生成生态健康报告摘要的示例技能"),
                       ("note-helper", "整理经验笔记条目的示例技能")):
        fm = {"name": name, "description": desc,
              "version": f"0.{rng.randrange(10)}.{rng.randrange(10)}",
              "status": "active", "fate": "retained",
              "domain": "reporting" if name == "report-helper" else "notebook",
              "environment": "windows", "merged_into": "", "evolved_from": ""}
        _write(skills / name / "SKILL.md", _dump_fm(fm, "最小 SKILL.md 占位正文。"))

    return paths
