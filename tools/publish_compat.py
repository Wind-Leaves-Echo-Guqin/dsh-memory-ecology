#!/usr/bin/env python3
"""publish_compat.py — 记忆生态·兼容版开源发布流水线（生成物模式）。

从开发树（memory-ecology-兼容版）生成全新开源树 memory-ecology-compat：
  src/memory_ecology/       可移植核心（四道门 + eco_note 可移植件 + lib + 测试）
  integrations/hermes/      Hermes 参考实现（state.db/cron 耦合件，自成 scripts 树）
  integrations/dsh/         dsh 适配器 ×2（EAC Extension SDK + 官方 cordis）+ Python 侧
  根文档                    README / VERSION / CHANGELOG / LICENSE / config.example

脱敏沿用生产 publish_opensource.py 的规则词表并追加；敏感词扫描不过 = 拒绝生成。
开源树是生成物、永不手编；改动回开发树后重跑本脚本。

用法: python publish_compat.py [--out DIR]
"""
import argparse
import datetime
import os
import pathlib
import re
import shutil
import sys

# 2026-10-29：v3.1 源脚本已从 SRC/scripts 迁到 SRC/src/memory_ecology（main 上是生成物），
# 生成器原来固定读 SRC/"scripts"，在 main 布局上必炸（实测 FileNotFoundError）。
# 改为：优先用显式指定的源树（本机 = local-dev-snapshot 的 worktree），否则回落旧布局。
SRC = pathlib.Path(os.environ.get("ECO_SRC") or r"C:\Users\qy160\dsh-memory-ecology")
SCRIPTS_DIR = (pathlib.Path(os.environ["ECO_SCRIPTS_DIR"]) if os.environ.get("ECO_SCRIPTS_DIR")
               else (SRC / "scripts"))
DEFAULT_OUT = pathlib.Path(r"C:\Users\qy160\dsh-memory-ecology-release")

# v3.2.1：v3.2.0 重写版核心（14 模块，纯 stdlib）的唯一真源在**另一个**本地仓库
# D:\work\dsh-memory-ecology（含自己的 .git，remote 指向已删除的 dsh-memory-ecology-plugin）。
# 发布树把它整体放在 core/ 子目录，与 src/ 下的 v3.1 历史线并存。
# 可用环境变量 ECO_CORE_SRC 覆盖（另一台机器上没有 D:\work 时用）。
CORE_SRC = pathlib.Path(os.environ.get("ECO_CORE_SRC") or r"D:\work\dsh-memory-ecology")

# core/ 随发的文件（**白名单，不是整树拷贝**）：排除 .git / __pycache__ /
# egg-info / dist。core/ 自己的 README 是"独立插件"口径（提到旧仓库名），
# 在发布树里由根 README 指路，故 core/README.md 一并随发但保持原样。
CORE_PUB_DIRS = ["src", "tests", "tools", "docs", "plugin"]
CORE_PUB_FILES = ["pyproject.toml", "VERSION.md", "CHANGELOG.md", "README.md", "run_tests.py",
                  ".gitignore"]
CORE_PUB_EXCLUDE_DIRS = {"__pycache__", ".git", "dist", "node_modules"}
CORE_PUB_EXCLUDE_SUFFIX = {".pyc", ".pyo", ".egg-info"}

# ── 发布集 ──────────────────────────────────────────────────────────────
CORE = [
    "write_gate.py", "eco_quota.py", "distill_stage.py", "eco_review.py",
    "eco_eval.py", "eco_eval_gold.py", "eco_breed.py", "eco_search.py", "eco_state.py",
    "eco_health_check.py", "eco_version.py",
    "eco_note_adopt.py", "eco_note_index.py", "eco_note_query.py",
    "eco_note_error_query.py", "eco_note_verify.py", "eco_note_merge_list.py",
    "check_entry_discipline.py",   # v2.5.0：CI 门禁（检查编码/数据根/建目录/锁四项纪律）
    "gate_log_rebuild.py",         # v2.5.2：从 eco.db 重建可读账本（修复被覆盖的历史）
    "eco_scale_check.py",          # v2.5.5：库规模与检索延迟监控（决定何时上 FTS5）
    # ── 以下为 v2.5.0 补登记：它们在上一版发布树里存在（手工补的），但白名单漏登记，
    #    导致重新生成时会被删掉。正式登记后由生成器稳定产出。
    "memory_query.py",             # 需求侧主检索（README §30 秒上手 第 0 步就用它）
    "eco_retrieval_report.py",     # 零结果率观测（GUI「检索质量」页签数据源）
    "eco_sim_eval.py",             # 相似度 golden 评测
    "analyze_discriminative.py",   # 难例分层分析
    "build_golden.py",             # golden 构建
    "calibrate_thresholds.py",     # 阈值标定
    "compare_embed_models.py",     # 多模型对比
    "fetch_embed_models.py",       # 模型拉取（走 hf-mirror）
    "gen_golden_candidates.py",    # golden 候选生成
    "migrate_thresholds.py",       # 阈值迁移
    "tradeoff_table.py",           # 误合并预算下召回表
    # v3.1.0（信息安全件）：核心备份加密（live 独有件收编进 dev 树后正式登记）
    "eco_backup.py",
]
CORE_TESTS = ["test_eco_gates.py", "test_eco_version.py", "test_eco_note_error_query_v2.py",
              "test_safeio.py", "test_eco_eval_v22.py",
              # v2.5.0：并发写入实测 + 变异测试（它们验证的是本次修的那批缺陷）
              "test_concurrent_writes.py", "test_mutation.py",
              # ── v2.5.0 补登记：上一版发布树里已有、但白名单漏登记的测试
              "test_evidence_chain.py", "test_memory_query_time.py", "test_p05_demand.py",
              "test_p0_fixes.py", "test_p1_fixes.py", "test_recall_rrf.py",
              "test_retrieval_report.py", "test_similarity.py", "test_vector_cache.py",
              "test_write_gate_conflict_tri.py"]
# ── tests/：v3.1 的独立测试套（2026-10-29 补登记）────────────────────
# 此前**整个 tests/ 目录都没进发布树**（生成器没有它的拷贝逻辑），后果有两层：
#   ① 开源版跑不了 v3.1 的完整回归，README 里"python test_*.py"那串命令
#      在发布树里有一半找不到文件；
#   ② 更要紧的是 **test_v31_security / test_pii_rules / test_privacy_findings /
#      test_round3_redteam 这批安全与隐私测试也没发** —— 开源版因此无法自证
#      "物理删除禁用、PII 脱敏、红队项已修"，而这恰是本项目对外的核心承诺。
# 这些测试用 `REPO/scripts` + `REPO/tools` 定位（开发树布局），发布树是
# `src/memory_ecology`，故随发前做一次布局适配（见 _adapt_test_paths）。
TESTS_DIR_FILES = [
    "test_a1_provenance.py", "test_audit_findings.py", "test_check_shell_sync_failclosed.py",
    "test_evo_gate.py", "test_evo_review_paths.py", "test_gate_max_body.py",
    "test_gate1_split_updates.py", "test_gates_characterization.py",
    "test_gui_contract.py", "test_gui_v3_2_0.py",
    # 注意：`test_gui_v2_api.py` **不发** —— 它 import `integrations/gui-v2/server/app.py`，
    # 而 gui-v2 是历史版本、按设计不随发布物分发（README 明说"按对应 tag checkout"）。
    # 发一个必然 ImportError 的测试，比不发更糟：会让"跑全部测试"永远红。
    "test_inject_gate_quarantine.py", "test_negation_flip_dispute.py",
    "test_noop_generational.py", "test_pii_rules.py", "test_privacy_findings.py",
    "test_redact_pii.py", "test_redblue_findings.py", "test_round3_redteam.py",
    "test_rule_conflict.py", "test_similarity_paraphrase.py",
    "test_sqlindex_fm_normalization.py", "test_sqlindex_privacy_erasure.py",
    "test_v256_fixes.py", "test_v256_tools_redteam.py", "test_v301_injection_scope.py",
    "test_v31_security.py",
]

LIB = ["__init__.py", "config.py", "metrics.py", "fs.py", "llm.py", "safeio.py",
       "gatekit.py", "memstore.py", "inject_gate.py", "sqlindex.py", "integrity.py",
       "similarity.py", "vector_cache.py", "watermark.py",
       "cli_base.py",
       # v2.5.8（隐私 PF1/PF2/PF4）：pii 是规则单源，redact 被 agent-hooks
       # 模块级 import；漏登记 = 发布树 `from lib.redact` 直接 ImportError。
       "pii.py", "redact.py",
       # v3.1.0：备份加密封套（eco_backup import；漏登记=发布树 ImportError）
       "crypto.py"]
# metrics：口径单源（§4-C）；gatekit/memstore：§C 解析与 IO 单源（v2.2.4 补登记，漏登记=发布树 import 全炸）；
# inject_gate：注入判定共享模块（hermes hook 引用）；
# OCR 2026-10-03：sqlindex 被 write_gate/eco_note_query/eco_note_error_query/hook 模块级无条件
# `from lib import sqlindex`（漏登记=发出去即断）；integrity 被 eco_health_check try/except 引用
# （漏发只静默降级，但 deploy_live 的 write_manifest 依赖它，随发保完整）。contracts.py 无发布集引用不入
# 2026-10-11（v2.4.0 补登记，同类事故再现）：similarity 被 CORE 的 write_gate/distill_stage/eco_review
# 模块级引用（漏登记=守卫直接拦）；watermark（水位线分页，非数字水印）被 hermes 集成层
# eco_extract/eco_note/eco_note_signals 引用；vector_cache 引用方虽都在发布集外，
# 但根 README 对 `--semantic`（lib/vector_cache.py shadow index）有公开承诺，随发保完整。
HERMES_SCRIPTS = [
    "eco_extract.py", "eco_note.py", "eco_note_signals.py", "eco_note_backfill.py",
    "eco_note_backfill_runall.py", "eco_health_alert.py", "eco_git_commit.py",
    "_ecoreview_selftest.py",
]
HERMES_TESTS = ["test_eco_note_v10.py", "test_eco_note_demo.py", "test_eco_note_backfill.py"]
HERMES_HOOKS = ["eco_note_inject.py", "test_eco_note_inject.py"]
# GUI 观测舱（v0.3 可操作驾驶舱）。GUI = "人类宿主的适配器"：读通道零写入，
# 写操作全部子进程转交核心 CLI；Hermes 专属动作（cron 重跑/固定模型）在无
# Hermes 的机器上自动降级为"复制命令"，不产生副作用。static/ 整目录随发。
GUI_DIR_FILES = ["eco_gui.py", "collector.py", "collector_v3_2_0.py", "runner.py",
                 "first_aid.py", "install_desktop.py",
                 "setup_ecology.py", "tray.py",
                 "README.md", "README-v3.2.0.md",
                 "启动生态观测舱.cmd", "生态急救箱.cmd", "启动生态观测舱.pyw",
                 # v3.2.0 起：默认入口指向 gui-v3
                 "启动生态观测舱v3.2.0.pyw"]
# gui-v3/（当前发布版前端，vite 构建产物不入库，只发源码）
GUI_V3_FILES = ["package.json", "vite.config.ts", "tsconfig.json", "tsconfig.app.json",
                "tsconfig.node.json", "index.html", ".oxlintrc.json", "README.md"]
# static/ 下不随发布的内容：
#   demo-3d.html —— Three.js 3D 星图**评估原型**（v2.2.2 的"暂不纳入"结论已在 v2.2.3
#     由宿主改判：3D 星空进主线。正式实现为 static/js/viz/gl3d.js —— 玻璃球体 +
#     透射折射 + 环境反射 + 血缘光流；因此 static/lib/three.module.js 现已随发布
#     （r160，1.27MB，MIT；懒加载，且 WebGL 不可用时自动回退平铺视图，功能不残缺））
#   demo-cruise.html —— 巡航模式设计原型（正式功能已在 cruise.js 内实现）
GUI_STATIC_EXCLUDE = ("demo-3d.html", "demo-cruise.html")

# ── 脱敏规则（沿用生产 publish_opensource.py + 兼容版追加）────────────────
SENSITIVE_PATTERNS = [
    (re.compile(r"C:[\\/]Users[\\/]qy160", re.I), "<USER_HOME>"),
    (re.compile(r"qy160"), "<user>"),
    (re.compile(r"风遗古琴声"), "<nickname>"),
    (re.compile(r"Wind-Leaves-Echo-Guqin"), "<github-user>"),
    (re.compile(r"湖北师大|湖北师范大学|黄石|咸宁", re.I), "<location>"),
    (re.compile(r"原神|崩铁|芙宁娜|星穹铁道", re.I), "<game>"),
    (re.compile(r"辰汐|凯文|卢米安|吴旭仁|柳文轩|张艺轩"), "<person>"),
    (re.compile(r"文体部"), "<org>"),
]
SCAN_WORDS = ["qy160", "风遗古琴声", "辰汐", "凯文", "卢米安", "吴旭仁", "柳文轩", "张艺轩",
              "咸宁", "黄石", "湖北师大", "湖北师范大学", "原神", "崩铁", "芙宁娜", "星穹铁道",
              "文体部", r"C:\\Users", r"C:/Users", r"D:/convlstm", r"D:\\convlstm",
              # 2026-10-29：词目 `memory-ecology-兼容版` 已移除——该目录已改名并归档，
              # 名称本身不再是"会泄漏本机布局的私有目录"（项目已正式定名 dsh-memory-ecology，
              # 改名史在 release 的 VERSION.md 里是正当记录）。路径泄漏仍由上面
              # qy160 / C:\\Users / C:/Users 三条兜住：任何写进发布物的本机绝对路径必带用户名。
              "convlstm", "valley_gate",  # 2026-09-06 三审追加：私人项目名/内部脚本名不得外泄
              # 注：AppData 毛扫不用——hook 的 ~\AppData\Local\hermes 为功能性默认路径（无用户名）；
              # 个人路径风险由 qy160 / C:\\Users / C:/Users 词目覆盖，审计时单独人工核对 AppData 残留
              ]

REPLACE_PAIRS = [
    ('MR_TOPICS = ["咸宁", "原神", "谷价", "MapGIS", "天气", "体力", "LibreOffice", "论文"]',
     'MR_TOPICS = ["python", "data", "report", "model", "config", "script", "image", "test"]'),
    ('    ("咸宁", "城市（家）"), ("黄石", "城市（学校）"),\n    ("原神", "游戏"), ("崩铁", "游戏"), ("体力", "游戏"),',
     '    ("python", "语言"), ("data", "数据"), ("report", "文档"),\n    ("model", "模型"), ("file", "文件"), ("user", "用户"),'),
    ("预设关键词白名单 [咸宁/黄石/原神/崩铁/体力]",
     "预设关键词白名单（config 可调）"),
    ('roots = [HERMES_HOME, Path.home() / ".hermes", Path(r"D:/convlstm模型"), DESKTOP]',
     'roots = [HERMES_HOME, Path.home() / ".hermes", DESKTOP]'),
    ('        if "convlstm" in p:', '        if "project" in p:'),
    ('            elif "convlstm" in b["path"]:', '            elif "project" in b["path"]:'),
    ('HERMES_HOME / ~/.hermes / D:/convlstm模型 / 桌面',
     'HERMES_HOME / ~/.hermes / 桌面'),
    ('把 eco_extract 产出的 pending 候选整合进 L2 详情层',
     '把会话提取管道产出的 pending 候选整合进 L2 详情层'),
    ('独立 gate 脚本（保护区外，不改 eco_extract.py）',
     '独立 gate 脚本（保护区外，不改提取管道）'),
    ('生态捕获骨架 v2（memory-skill-ecosystem 项目 3.2 节，捕获主通道=增量自动提取）。',
     '生态捕获骨架（会话记录增量提取，捕获主通道）。'),
    ('Path.home() / ".agent-index"', 'Path.home() / ".memory-ecology"'),
    ('产物为报告文件 + 评分历史（.agent-index/designs 区）',
     '产物为报告文件 + 评分历史（报告目录区）'),
    ('与 .agent-index/designs/（版本存档）', '与报告目录（版本存档）'),
    ('cc-switch\\\\ 移入备份区 `~/.agent-index/backup_staging/`',
     'cc-switch\\\\ 移入备份区 `~/.memory-ecology/backup_staging/`'),
    # 兼容版追加：开发树路径 → 发布树通用表述
    ("memory-ecology-兼容版", "memory-ecology-compat"),
    ("designs/记忆生态-v2.1.1-设计", "docs/DESIGN-v2.1.1"),
    # OCR 2026-10-03：旧规则锚定已废弃的硬编码 "AppData\\Local\\hermes\\scripts" 行，
    # 开发树 eco_note_inject.py 现为 __file__ 派生（规则永不命中=死规则，已删）；
    # 重锚到当前相对写法，保留"scripts + 探测核心目录"两段注入语义——发布树 hook 的
    # eco_note_query/eco_note_error_query 只在 src/memory_ecology，缺第二段=发出去即断
    ('sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))',
     'import pathlib as _pl\n'
     '_here = _pl.Path(__file__).resolve()\n'
     'sys.path.insert(0, str(_here.parent.parent / "scripts"))\n'
     'for _base in _here.parents:\n'
     '    if (_base / "src" / "memory_ecology").is_dir():\n'
     '        sys.path.insert(0, str(_base / "src" / "memory_ecology"))\n'
     '        break'),
]

# 集成层测试的路径桥接（发布树：tests 在 integrations/hermes/scripts 下，核心在 src/memory_ecology）
TEST_BRIDGE = (
    "# publish bridge: 集成层测试定位核心模块（src/memory_ecology）\n"
    "import sys as _sys, pathlib as _plib\n"
    "_sys.path.insert(0, str(_plib.Path(__file__).resolve().parents[3] / 'src' / 'memory_ecology'))\n"
)


# 实际输出根（build() 按 --out 重绑；相对路径日志不能写死 DEFAULT_OUT，否则 --out 必炸）
_OUT: pathlib.Path = DEFAULT_OUT


def sanitize(text: str) -> str:
    for old, new in REPLACE_PAIRS:
        if old in text:
            text = text.replace(old, new)
    for pat, repl in SENSITIVE_PATTERNS:
        text = pat.sub(repl, text)
    return text


def scan_sensitive(text: str) -> list[str]:
    return [w for w in SCAN_WORDS if re.search(w, text, re.I)]


def _adapt_test_paths(text: str) -> str:
    """把 tests/*.py 里的 `REPO / "scripts"` 布局适配成发布树的 `src/memory_ecology`。

    测试原本按开发树布局写死（REPO/scripts、REPO/tools）。发布树把脚本放在
    src/memory_ecology/，直接搬过去会 ImportError。这里只改 sys.path 注入那几行，
    断言与测试逻辑一律不动——**测试的判据不能因为发布而变形**。
    """
    text = text.replace('sys.path.insert(0, str(REPO / "scripts"))',
                        'sys.path.insert(0, str(REPO / "src" / "memory_ecology"))')
    text = text.replace("sys.path.insert(0, str(REPO / 'scripts'))",
                        "sys.path.insert(0, str(REPO / 'src' / 'memory_ecology'))")
    # agent-hooks：开发树在 REPO/agent-hooks，发布树发在 integrations/hermes/agent-hooks
    text = text.replace('sys.path.insert(0, str(REPO / "agent-hooks"))',
                        'sys.path.insert(0, str(REPO / "integrations" / "hermes" / "agent-hooks"))')
    text = text.replace("sys.path.insert(0, str(REPO / 'agent-hooks'))",
                        "sys.path.insert(0, str(REPO / 'integrations' / 'hermes' / 'agent-hooks'))")
    return text


def copy_sanitized(src: pathlib.Path, dst: pathlib.Path, actions: list[str]) -> None:
    """文本文件脱敏后写出；二进制资源（字体 woff2 等）原样按字节复制。

    为什么：脱敏是文本层面的事（正则替换个人标识），对字节流无意义且会解码失败。
    v2.2.2 起 GUI 自托管字体，static/fonts/*.woff2 走二进制分支。
    """
    if src.suffix.lower() == ".log":
        actions.append(f"{src.name}（跳过：运行期日志不入发布物——v2.2.3 曾混入 gui_error.log，2026-09-30 清史后立规）")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    raw = src.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        dst.write_bytes(raw)
        actions.append(str(dst.relative_to(_OUT)) + "（二进制原样）")
        return
    # 行尾先归一到 LF，再交给 write_text 按平台写出（Windows 得 CRLF）。
    # 若跳过这步，文本里已有的 \r\n 会被再翻译一次 → \r\r\n 污染发布树。
    dst.write_text(sanitize(text.replace("\r\n", "\n")), encoding="utf-8")
    actions.append(str(dst.relative_to(_OUT)))


def copy_test_with_bridge(src: pathlib.Path, dst: pathlib.Path, actions: list[str]) -> None:
    """集成层测试 = 脱敏 + 路径桥接（插到 shebang/注释/docstring/__future__ 之后）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    lines = sanitize(src.read_text(encoding="utf-8")).splitlines(keepends=True)
    i, n = 0, len(lines)
    while i < n and lines[i].lstrip().startswith("#"):
        i += 1  # shebang / 顶部注释
    if i < n and lines[i].lstrip().startswith(('"""', "'''")):
        q = lines[i].lstrip()[:3]
        # OCR 2026-10-03：单行 docstring（开闭同行）本行已闭合，不能从下一行起找
        # 结束三引号——否则会一路扫到下一个无关三引号/EOF，把 TEST_BRIDGE 插到文件尾
        if q not in lines[i].lstrip()[3:]:
            i += 1
            while i < n and q not in lines[i]:
                i += 1  # 多行 docstring 收尾
        i += 1
    while i < n and lines[i].startswith("from __future__"):
        i += 1
    lines.insert(i, TEST_BRIDGE)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("".join(lines), encoding="utf-8")
    actions.append(str(dst.relative_to(_OUT)))


def build(out: pathlib.Path) -> list[str]:
    global _OUT
    actions: list[str] = []
    out = pathlib.Path(out).resolve()
    _OUT = out
    src_r = SRC.resolve()
    # Q31 修复（2026-09-06）：--out 指向源树时原实现会 rmtree 删光开发树——加守卫
    # OCR 2026-10-03（high）补强：互为祖先/后代同样危险（--out 指向开发树父目录
    # 时，下方清空逻辑会把开发树本身删掉）；陌生已填充目录（无 .git）也拒绝清空
    if out == src_r or src_r in out.parents or out in src_r.parents:
        raise SystemExit("❌ --out 与开发树相同/互为祖先-后代，拒绝发布")
    if out.exists():
        if not (out / ".git").is_dir() and any(out.iterdir()):
            raise SystemExit("❌ --out 指向已存在的非发布目录（无 .git），拒绝清空陌生目录")
        # Q31 修复：保留 .git（开源树含发布历史；原 rmtree 会连 git 库一起删）
        # OCR 2026-10-03（high）：先物化列表再删——边遍历边删目录项在 Windows 上
        # 可能漏删/跳项，残留文件会被后续守卫误判为"live 独有差异"
        for child in list(out.iterdir()):
            if child.name == ".git":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
    else:
        out.mkdir(parents=True)
    core_dir = out / "src" / "memory_ecology"
    core_dir.mkdir(parents=True)

    (core_dir / "__init__.py").write_text(
        '"""Memory Ecology Compat — 多宿主记忆生态（可移植核心）。"""\n', encoding="utf-8")
    actions.append("src/memory_ecology/__init__.py")

    for name in CORE + CORE_TESTS:
        copy_sanitized(SCRIPTS_DIR / name, core_dir / name, actions)
    for name in LIB:
        copy_sanitized(SCRIPTS_DIR / "lib" / name, core_dir / "lib" / name, actions)

    # ── v2.5.0 补登记：以下四类此前靠"手工补进发布树"，生成器不管，
    #    一旦重新生成就会被删掉（本次真实踩到：25 个文件消失）。
    #    现在由生成器稳定产出。
    #  ① 相似度 golden 评测集（eco_sim_eval 的标尺，README 有公开承诺）
    #  golden **不发布**：它含真实用户语料（个人项目名/桌面结构），脱敏会破坏语义，
    #  且 README 已声明"182 对评测集不在仓库内；随仓库的种子集仅作格式示例"。
    #  下游想跑 eco_sim_eval 请自备 golden（格式见 eco_sim_eval.py 头注释）。
    #  ② 架构待办与已知裂缝清单（README 引用 3 次，是"已知问题"的唯一入口）
    at_src = SRC / "ARCHITECTURE_TODO.md"
    if at_src.is_file():
        copy_sanitized(at_src, out / "ARCHITECTURE_TODO.md", actions)
    #  ③ 本地 embedding 选型说明（README 引用；模型本体不入库，只发说明）
    md_src = SRC / "models" / "README.md"
    if md_src.is_file():
        (out / "models").mkdir(parents=True, exist_ok=True)
        copy_sanitized(md_src, out / "models" / "README.md", actions)
    #  ④ 双树漂移比对工具（README 的仓库结构段列了 tools/）
    #     2026-10-29 补登记 eco_selfcheck / eco_watchdog：二者是**生产在跑的运维件**
    #     （selfcheck 由 Windows 计划任务调用、watchdog 做宿主外看门狗），
    #     生成器原来不管它们 → 一旦重新生成，发布树里的这两个文件会被删掉。
    #     同一类事故此前已踩过一次（"重新生成 25 个文件消失"），这里按老规矩补进白名单。
    (out / "tools").mkdir(parents=True, exist_ok=True)
    # tests/ 在**源树**根下（SCRIPTS_DIR 是 <源树>/scripts），用 ECO_SRC 定位最稳
    # 测试/钩子依赖的 tools 件：不随发则 tests/ 与 agent-hooks/ 直接 ImportError
    # （实测：test_v31_security 缺 eco_trace、test_a1_provenance 缺 eco_backfill_origin）
    for _tool in ("sync_trees_check.py", "eco_selfcheck.py", "eco_watchdog.py",
                  "eco_trace.py", "eco_backfill_origin.py",
                  "eco_backfill_scope.py"):
        _s = SRC / "tools" / _tool
        if _s.is_file():
            copy_sanitized(_s, out / "tools" / _tool, actions)

    # ── .github/assets/：README 首屏 hero 图（2026-10-30 新增）───────────
    # README 用 <picture> 引 .github/assets/hero-{dark,light}.svg 做深/浅色自适应头图。
    # 这些 SVG 是**手绘矢量**（每个 ~3.8 KB），不是截图，所以随发布物一起发没有
    # 体积与脱敏问题（纯几何图形 + 固定文案）。不随发则 README 首屏图片 404。
    assets_src = SRC / ".github" / "assets"
    if assets_src.is_dir():
        (out / ".github" / "assets").mkdir(parents=True, exist_ok=True)
        for svg in sorted(assets_src.glob("*.svg")):
            shutil.copy2(svg, out / ".github" / "assets" / svg.name)
            actions.append(f".github/assets/{svg.name}")

    hermes_dir = out / "integrations" / "hermes" / "scripts"
    hermes_dir.mkdir(parents=True)
    (out / "integrations" / "hermes" / "scripts" / "README.md").write_text(
        "# Hermes 参考集成层\n\n这些脚本与 Hermes 会话库（state.db）/ cron 耦合，"
        "作为编写其他宿主适配器的参考实现保留；在 Hermes 布局（scripts/ + lib/ 同级）下运行。\n",
        encoding="utf-8")
    for name in HERMES_SCRIPTS:
        copy_sanitized(SCRIPTS_DIR / name, hermes_dir / name, actions)
    for name in HERMES_TESTS:
        copy_test_with_bridge(SCRIPTS_DIR / name, hermes_dir / name, actions)
    for name in LIB:
        copy_sanitized(SCRIPTS_DIR / "lib" / name, hermes_dir / "lib" / name, actions)
    for name in HERMES_HOOKS:
        copy_sanitized(SRC / "agent-hooks" / name, out / "integrations" / "hermes" / "agent-hooks" / name, actions)

    dsh_src = SRC / "adapters" / "dsh"
    dsh_dst = out / "integrations" / "dsh"
    for rel in [
        "README.md", "test_smoke.js",
        # v2.2.5：子进程语义单源（解释器解析/环境裁剪/参数分隔/退出码分档）+ 两个新测试
        "test_common.js", "test_cordis.mjs",
        "package/package.json", "package/index.js", "package/lib/common.js",
        "package/python/eco_note_dsh_context.py", "package/python/test_eco_note_dsh_context.py",
        "package/python/test_eco_note_dsh_context_v4.py",
        # 官方桌面版（cordis patch 层）适配器，v2.2.4 起随发
        "package-cordis/package.json", "package-cordis/index.js", "package-cordis/cordis.patch.yml",
    ]:
        copy_sanitized(dsh_src / rel, dsh_dst / rel, actions)

    gui_src = SRC / "integrations" / "gui"
    gui_dst = out / "integrations" / "gui"
    for name in GUI_DIR_FILES:
        copy_sanitized(gui_src / name, gui_dst / name, actions)
    for p in sorted((gui_src / "static").rglob("*")):
        if not p.is_file():
            continue
        if ".bak" in p.name:
            # 维护者本地同步备份（如 health.js.bak-同步前-*）不入发布物——守卫第 6 条
            # 同判据；只挡复制，不碰源文件（删除与否由维护者决定）
            continue
        rel = p.relative_to(gui_src / "static").as_posix()
        if rel in GUI_STATIC_EXCLUDE:
            continue
        copy_sanitized(p, gui_dst / "static" / rel, actions)

    # ── gui-v3：当前发布版前端（v3.2.0）源码 ─────────────────────────────
    # **只发源码，不发 dist/**：dist 是 vite 构建产物（近 1 MB 压缩包），
    # 与"dist 不入库"的既有纪律一致，用户 clone 后 `npm run build` 即可。
    # 历史版本 gui-v2 / v2.1 / v2.2 / v2.3 **不随发布**（体积大、且已被 v3 取代），
    # 需要旧版的从 git 历史 checkout 对应 tag。
    v3_src = SRC / "integrations" / "gui-v3"
    v3_dst = out / "integrations" / "gui-v3"
    if v3_src.is_dir():
        for name in GUI_V3_FILES:
            if (v3_src / name).is_file():
                copy_sanitized(v3_src / name, v3_dst / name, actions)
        for p in sorted((v3_src / "src").rglob("*")):
            if p.is_file():
                copy_sanitized(p, v3_dst / "src" / p.relative_to(v3_src / "src"), actions)

    # 论文实验工件（experiments/，2026-10-11 随发）：消融/基线 harness + 结果数据，
    # 复现命令见 experiments/README.md；tests/fixtures 是 ablation.py 的运行依赖
    # （create_eco 沙盒），漏发 = RQ1 复现 ImportError
    for rel in ["tests/fixtures/__init__.py", "tests/fixtures/eco.py"]:
        copy_sanitized(SRC / rel, out / rel, actions)
    exp_src = SRC / "experiments"
    exp_dst = out / "experiments"
    for rel in ["ablation.py", "baselines.py", "paper_figs.py", "README.md",
                "results/summary.json", "results/baselines-summary.json"]:
        copy_sanitized(exp_src / rel, exp_dst / rel, actions)

    # ── core/：v3.2.0 重写版核心（唯一真源）──────────────────────────────
    # v3.2.1 起随发。它来自另一个本地仓库（CORE_SRC），与 src/ 下的 v3.1
    # 历史线**实现完全不同**（只有 __init__.py / similarity.py 同名），
    # 所以并存而非替换——直接覆盖会毁掉 v3.1 的 33 个 CLI 脚本（另有 17 个测试文件）。
    core_pub_src = CORE_SRC
    if not core_pub_src.is_dir():
        raise SystemExit(f"❌ core 源树不存在：{core_pub_src}"
                         f"（设 ECO_CORE_SRC 指向 v3.2 核心仓库）")
    core_out = out / "core"
    core_out.mkdir(parents=True, exist_ok=True)
    for name in CORE_PUB_FILES:
        if (core_pub_src / name).is_file():
            copy_sanitized(core_pub_src / name, core_out / name, actions)
    for dname in CORE_PUB_DIRS:
        dsrc = core_pub_src / dname
        if not dsrc.is_dir():
            continue
        for p in sorted(dsrc.rglob("*")):
            if not p.is_file():
                continue
            rel_parts = p.relative_to(core_pub_src).parts
            if CORE_PUB_EXCLUDE_DIRS & set(rel_parts):
                continue
            if any(part.endswith(".egg-info") for part in rel_parts):
                continue
            if p.suffix.lower() in CORE_PUB_EXCLUDE_SUFFIX:
                continue
            copy_sanitized(p, core_out / p.relative_to(core_pub_src), actions)
    # 核对随发的 core 版本号与发布树 VERSION 一致
    # （发版最易漏的一处：core/VERSION.md 与主 VERSION.md 是两份，独立演进）
    # 注意：比的是**模板** ROOT_VERSION（发布树真正写出去的那份），不是开发树的
    # VERSION.md——后者是历史线，长期停在 v3.1.0，拿它比必然假红。
    core_ver_file = core_pub_src / "src" / "memory_ecology" / "__init__.py"
    if core_ver_file.is_file():
        m = re.search(r'__version__\s*=\s*"([^"]+)"', core_ver_file.read_text(encoding="utf-8"))
        mv = re.search(r"v(\d+\.\d+\.\d+)", ROOT_VERSION)
        if m and mv and mv.group(1) != m.group(1):
            raise SystemExit(f"❌ core 版本 {m.group(1)} 与发布树 VERSION 模板 {mv.group(1)} 不一致")

    for pyc in out.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)

    # 版本徽章：**从 core 的 __version__ 动态渲染**，不在模板里硬编码。
    # 2026-10-29 实测踩到：ROOT_VERSION 升到 3.2.2 了，ROOT_README 里的徽章
    # 还写着 v3.2.1——两处独立演进就一定会漂移，索性让 README 跟着 core 走。
    # （上面刚校验过 core 版本与 ROOT_VERSION 一致，这里直接用 core 的值）
    #
    # 2026-10-29（本棒）：占位符从"只有徽章一处"扩到**正文里所有产品版本号**
    # （仓库结构表的 core 版本、测试标题、版本段）。原因同一条：手抄必漂移。
    # 渲染后**必须校验没有残留 __ECO_VERSION__**，否则会把占位符直接发给用户。
    readme_text = ROOT_README
    if core_ver_file.is_file():
        m2 = re.search(r'__version__\s*=\s*"([^"]+)"', core_ver_file.read_text(encoding="utf-8"))
        if m2:
            readme_text = ROOT_README.replace("__ECO_VERSION__", m2.group(1))
    if "__ECO_VERSION__" in readme_text:
        raise SystemExit("❌ README 里仍有未渲染的 __ECO_VERSION__ 占位符"
                         "（core 版本没读到？）—— 不能把占位符发给用户")
    (out / "README.md").write_text(readme_text, encoding="utf-8")
    (out / "VERSION.md").write_text(ROOT_VERSION, encoding="utf-8")
    (out / "CHANGELOG.md").write_text(ROOT_CHANGELOG, encoding="utf-8")
    (out / "LICENSE").write_text(ROOT_LICENSE, encoding="utf-8")
    (out / "config.example.yaml").write_text(CONFIG_EXAMPLE, encoding="utf-8")
    # 2026-10-29：补 `_diagram.*`——上游 ced966e 手工加过这条（误提交的临时图
    # 文件清理），但模板漏登记，重新生成时被无声覆盖 = 回退别人的修复。
    # 发布物是生成物，"手改会被覆盖"这条纪律对模板本身同样成立：上游手改过的
    # 规则必须回流到模板，否则每次重建都是一次静默回退。
    # 末尾的空行也要留着（与上游文件逐字节一致，省掉每次重建都多一条无关 diff）。
    # 2026-10-29 再补 `*.db-shm` / `*.db-wal`——同一条纪律的第二次复发：上游手工
    # 加过 SQLite 边车规则（eco.db 早已忽略，但 -shm/-wal 没覆盖，工作区长期挂
    # 两个未跟踪文件），模板又漏登记，重建时被无声删掉。**凡是上游手改过的
    # .gitignore 规则，都必须回流到这里**，否则每次重建都是一次静默回退。
    (out / ".gitignore").write_text(
        "__pycache__/\n*.pyc\nconfig.yaml\nmemories/\nexperiences/\n*.db\n.env\n"
        ".dsh_inject_state*.json\n\n_diagram.*\n*.db-shm\n*.db-wal\n\n",
        encoding="utf-8")
    actions += ["README.md", "VERSION.md", "CHANGELOG.md", "LICENSE",
                "config.example.yaml", ".gitignore"]
    return actions


# ⚠️ 必须是 **raw string**：模板里满是 `%LOCALAPPDATA%\dsh`、`\.`、`\s` 这类
# Windows 路径与正则片段，普通三引号会让 Python 逐条报 SyntaxWarning
# （`invalid escape sequence`），CI 用 `-W error` 直接崩。
ROOT_README = r"""<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset=".github/assets/hero-light.svg">
  <img alt="dsh-memory-ecology" src=".github/assets/hero-light.svg" width="840">
</picture>

[![version](https://img.shields.io/badge/version-v__ECO_VERSION__-2ea043?style=flat-square)](VERSION.md) [![python](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/) [![core deps](https://img.shields.io/badge/%E9%9B%B6%E4%BE%9D%E8%B5%96-%E7%BA%AF%20stdlib-success?style=flat-square)](#-30-秒上手) [![tests](https://img.shields.io/badge/core%20tests-7%20%E7%BB%84%E5%85%A8%E7%BB%BF-2ea043?style=flat-square)](#-测试) [![deletion](https://img.shields.io/badge/%E7%89%A9%E7%90%86%E5%88%A0%E9%99%A4-%E4%BB%A3%E7%A0%81%E5%B1%82%E7%A6%81%E7%94%A8-8957e5?style=flat-square)](#-两条硬纪律) [![hosts](https://img.shields.io/badge/%E5%AE%BF%E4%B8%BB-dsh%20%7C%20Hermes%20%7C%20%E4%BB%BB%E6%84%8F-8957e5?style=flat-square)](#-兼容什么)

**给 agent 的记忆做「生命周期治理」：四道门自动运转，零第三方依赖，永不物理删除。**

</div>

agent 的记忆通常"只进不出、写入靠自觉、超限不可见"。
本项目不管「怎么召回」——那是检索层的事；它管**召回之前**：什么该进、进来放哪、什么时候忘、忘了能不能找回来。

---

## ⚡ 30 秒上手

不需要安装，纯 stdlib，克隆下来就能跑：

```bash
git clone https://github.com/Wind-Leaves-Echo-Guqin/dsh-memory-ecology && cd dsh-memory-ecology/core/src

python -m memory_ecology --root /tmp/eco-demo init
python -m memory_ecology --root /tmp/eco-demo deposit --session s1 --text "用户偏好中文回复"
python -m memory_ecology --root /tmp/eco-demo gate all      # 顺跑四门
python -m memory_ecology --root /tmp/eco-demo search "中文"  # 检索（命中回写 last_hit）
python -m memory_ecology --root /tmp/eco-demo catalog       # 看目录：注入上下文的就是它
```

每一步都是 JSON 进、JSON 出，退出码分档——便于任何宿主封装，也便于你自己接脚本。
跑完你会看到：条目进了 `detail/`，四门各跑了一遍，检索按词面命中。

<sub>想接宿主（dsh / Hermes）或装 GUI 观测舱，见 [🚀 完整上手](#-完整上手)。</sub>

---

## 🚪 四道门：它到底治理什么

记忆不是"存下来"就完事。它会腐烂、会重复、会互相打架、会超限。
四道门各管一件事，**自动运转、零维护**：

| 门 | 治理动作 | 不跑会怎样 |
|---|---|---|
| **① 写入整合** | 候选 → 去重 → 同源合并 → 类型归一 → 取代链 | 候选堆成山，同一件事存十份 |
| **② 配额巩固** | L2 → L1 提升；L1 超限 → 挤出回 L2（**绝不丢**） | L1 变成"只出不进"的单向漏斗 |
| **③ 画像蒸馏** | 跨会话稳定事实 → USER 画像（观察期 + 交叉印证） | 用户画像永远停在三个月前 |
| **④ 复核降级** | 遗忘曲线：active → dormant → archived（**文件一直在**） | 陈旧记忆腐烂，或最活跃的反被归档 |

四道门里，**只有门①的相似/矛盾终审和门③的措辞用到 LLM**，其余环节零 LLM、可完全离线。

### 两条硬纪律（机制保证，不是口头约定）

**1. 物理删除在代码层禁用。**
只有状态迁移（active→dormant→archived）、取代标记、隔离区移动。仓库里有一条测试
`test_no_unlink_of_memories_anywhere` 扫源码，发现 `unlink`/`remove` 记忆文件就红。

**2. 一个数据根只允许一个写者。**
并发只追加靠 pid 唯一临时名 + 单次 `O_APPEND` + SQLite WAL——三者缺一即丢写。

---

## 🆚 和别的记忆方案比，差别在哪

多数方案把力气花在**召回**（向量、重排、RAG）。召回做好了，记忆依然会：
过期的压着新的、矛盾的两条并存、超限后静默丢最新的。

本项目解决的是**召回之前**那层。诚实地说清楚边界：

| | 本项目 | 典型 RAG / 向量记忆 |
|---|---|---|
| 核心问题 | 记忆的**生命周期**（进/放/忘/找回） | 记忆的**检索质量** |
| 检索方式 | 词面 + 圈过滤（**召回质量不是卖点**） | 向量 / 混合检索 |
| 写入 | 规则判重 + 取代链 + 矛盾标记 | 通常直接 append |
| 超限 | 挤出到 L2 层，**绝不丢** | 截断或丢弃最旧 |
| 删除 | **代码层禁用**，只能降级 | 通常可删 |
| 依赖 | **0**（纯 stdlib） | 向量库 / embedding 模型 |
| 实测体量 | 核心 3,784 行 / 测试 3,626 行 | — |

> **什么场景下你不该选它**：如果你要的是"把十万篇文档塞进去做语义检索"，本项目帮不上
> 忙——它的检索只有词面匹配，`README` 里也明说"召回质量不是卖点"。它是给**单个 agent
> 的长期记忆**做治理的，不是通用检索引擎。

---

## 📦 仓库结构

从 `v3.2.2` 起是**两层并存**（`core/` 是唯一维护线，`src/` 是已冻结的历史线）：

| 目录 | 是什么 | 版本 |
|---|---|---|
| **`core/`** | **核心引擎（唯一维护线）**：四道门 + 多生态圈 + 只读记忆包 + 检索，纯 stdlib；dsh 插件本体 `core/plugin/` 也在这里 | **__ECO_VERSION__** |
| `core/src/memory_ecology/` | 14 个模块：`gates.py` / `ledger.py` / `store.py` / `paths.py` / `retrieve.py` / `packs.py` / `config.py` / `root.py` / `similarity.py` / `measure.py` / `version.py` / `cli.py` / `__init__.py` / `__main__.py` | __ECO_VERSION__ |
| `core/tests/` | 核心 / 兼容与数据安全 / 并发 / 稳定性 / 变异（`run_tests.py` 7 组） | — |
| `src/memory_ecology/` | v3.1 治理核心（历史线，33 个 CLI 脚本 + 17 个测试）：写门/配额/蒸馏/复核/eco_note/向量缓存 | 3.1.0 |
| `integrations/hermes/` | Hermes 参考实现（state.db / cron 耦合件） | — |
| `integrations/dsh/` | dsh 适配器（`package/` = EAC；`package-cordis/` = 官方桌面版 cordis）。**v3.1 旧线（eco-note 时代），已冻结，勿用于新装** | — |
| `integrations/gui-v3/` | 观测舱 GUI 当前发布版（液态玻璃 / iOS 外壳，七新视图） | 3.2.0 |
| `models/` | 本地 embedding 权重（按需 fetch，不入库） | — |
| `tools/` | 运维与回填工具（自检 / 看门狗 / 双树比对 / scope 回填） | — |

### 双树收敛计划（**不是"永久并存"**）

| 树 | 定位 | 现在能改吗 |
|---|---|---|
| **`core/`** | **唯一维护线**。新功能、缺陷修复只进这里 | ✅ 唯一入口 |
| `src/`（v3.1，33 脚本） | **已冻结**：只接受安全修复，不再加功能 | ⚠️ 冻结 |
| `integrations/hermes/` | 生产 cron 今天仍跑这一套（v3.1 脚本） | ⚠️ 冻结 |

- **冻结判据（可验证）**：`tools/sync_trees_check.py` 报双树漂移时，若改动只发生在 `src/`
  而 `core/` 无对应物，即视为冻结线被误改。
- **合并的唯一前置**：把生产 cron 从 v3.1 脚本切到 `core/`。在这一步完成前，合并两棵树
  都是不安全的（会连带改掉正在跑的运维件）。

> 两棵树为什么并存：`core/` 是重写版（14 模块），与 `src/` 下 v3.1（33 脚本）**实现完全
> 不同**，只有 `__init__.py` / `similarity.py` 同名。直接覆盖会毁掉 v3.1 那 33 个脚本以及
> 依赖它们的 `integrations/hermes` / `integrations/dsh`。

---

## 🚀 完整上手

### 1) 只用核心 CLI（任何宿主 / 无宿主）

```bash
cd core/src
python -m memory_ecology --root <数据根> init
python -m memory_ecology --root <数据根> deposit --session s1 --text "用户偏好中文回复"
python -m memory_ecology --root <数据根> gate all          # 顺跑四门
python -m memory_ecology --root <数据根> search "中文"      # 词面检索
python -m memory_ecology --root <数据根> catalog           # 目录载荷（注入用）
python -m memory_ecology --root <数据根> materialize       # 物化到 .eco/catalog/
python -m memory_ecology --root <数据根> health            # 健康快照（GUI 用）
python -m memory_ecology version                          # 版本四处对齐自检

cd .. && python run_tests.py                              # 全量测试（7 组）
```

### 2) dsh 宿主

```powershell
dsh plugin --profile <profile> add link:<本仓库>/core/plugin

# 可选：配数据根（不配就用 %LOCALAPPDATA%\dsh\memory-eco，首次自动建）
# ~/.dsh/profiles/<profile>/cordis.patch.yml:
#   - id: eco-memory
#     config:
#       rootPath: D:/work/_eco_root
#       catalogLimit: 20
#       catalogMaxChars: 600
```

配置全部可被同名环境变量覆盖：`ECO_HOME` / `ECO_CORE` / `ECO_PYTHON`。

### 3) Hermes 宿主

`integrations/hermes/` 是参考实现（state.db 会话增量提取 / pre_llm_call 报错注入 hook /
cron 健康告警 / 基因库快照），展示如何把核心接到"有会话库和调度器的宿主"上。
其他宿主照这个模式写适配器。

### 4) 观测舱 GUI（可选，人类宿主）

**当前发布版 = `integrations/gui-v3/`**（GUI 组件版本 **3.2.0**，液态玻璃 / iOS 外壳），
在原有九视图之外新增七个：四门实况 / 圈的视角 / 注入观测 / 记忆包 / 圈管理 / 成本面板 / 急救箱。

```bash
# gui-v3 是 vite 工程，dist 不入库，首次用需构建：
cd integrations/gui-v3 && npm install && npm run build

# 启动（默认 127.0.0.1:8788，自动开窗口）
python integrations/gui/eco_gui.py
# → 浏览器打开 http://127.0.0.1:8788/v3/
```

| 前端目录 | 路由 | 状态 |
|---|---|---|
| `integrations/gui-v3/` | `/v3/` | **当前发布版**（GUI 组件版本 **3.2.0**） |
| `integrations/gui/`（static） | `/`（v0.3 静态版） | 历史版本，保留 |
| `gui-v2` / `v2.1` / `v2.2` / `v2.3` | 各自路由 | 历史版本，不随发布物分发，按对应 tag checkout |

> **关于两套版本号**（新人最容易懵的一处）：本仓库有**两个互不相干的版本轴**——
> ① **产品/核心版本** = `__ECO_VERSION__`（真源 `core/src/memory_ecology/__init__.py`）；
> ② **GUI 组件版本** = `3.2.0`（真源 `integrations/gui-v3/package.json`）。
> 它们刻意不对齐：GUI 是可选的人类观测界面，改动节奏与核心无关。
> README 里凡裸版本号若未带上下文，一律指①。

纯离线、仅绑 `127.0.0.1`；写动作共 **14 个**（配额挤出 / 写入门整合 / 经验采纳 / 技能繁育 /
cron 重跑 / cron 钉住 / 体检 / 索引重建 / 急救修复 / 圈配置 / 记忆包开 / 记忆包关 /
核心维护 / 物化），全部转发核心 CLI 并走确认闸门，**没有任何删除记忆的动作**。

---

## ⚠️ 当前边界与已知限制

写在前面，省得你试完才失望：

- **召回质量不是它的卖点**。检索是词面匹配 + 圈过滤，不做向量重排。要语义检索请自接。
- **四道门的门槛是保守值**，认的是"宁可重复，不可丢内容"。宁可多留条，不愿错删。
- **门④ 的"遗忘"是降级不是删除**：archived 条目文件一直在，可显式恢复。
- **GUI 是可选件**，核心不依赖它，没装 node 也能用全部治理能力。
- **只在 Windows 上做了真机验证**（生产环境是 Windows + 计划任务），Linux/macOS 未实测。
### ⚠️ 从旧版本升级时必读：第一次 `gate all` 会降级一批条目

**这不是数据丢失，但看起来像。**

门②③④ 在旧版本里因 phase 竞态**从未真正执行过**（详见 `core/CHANGELOG.md` H1 条）。
修好后第一次跑 `gate all`，会按遗忘曲线把久未使用的条目从 active 降为 dormant
（实测样本：**10 条里降 5 条**）。

这些条目**不再出现在注入目录**（目录只收 active），于是上下文里"突然少了一半记忆"。
但它们并没有被删除：

| 你想做什么 | 怎么做 |
|---|---|
| 确认条目还在 | `memory_query --revive`（无参数）列出今日复活候选 |
| 把它恢复回目录 | `memory_query --revive <slug>`（v3.1 线）<br>或 `python -m memory_ecology bring-back <id>`（`core/` 线） |
| 只是读正文 | `memory_query <关键词>` 仍能命中降级条目；`memory_load` 可读全文 |
| 想避免被降级 | 升级后先跑 `gate 4 --dry-run` 看会降哪些，确认后再真跑 |

> 两条线的恢复命令不同：**v3.1（生产当前在跑）= `memory_query --revive`**；
> **`core/`（新引擎）= `bring-back` 子命令**。别拿一条线的命令去另一条线上用。

---

## 🧩 兼容什么

| 消费者 | 怎么用 | 入口 |
|---|---|---|
| **任意 Python 程序** | `python -m memory_ecology` 子命令，JSON 进 JSON 出 | `core/src/` |
| **dsh**（DeepSeek Harness） | cordis 插件，只读 + fail-open，往 `pending/` 追加候选 | `core/plugin/`（唯一维护线） |
| **EAC / Extension SDK** | 同上，走 EAC 协议（v3.1 旧线，已冻结） | `integrations/dsh/package/` |
| **Hermes** | 参考实现：会话提取 + 注入 hook + cron | `integrations/hermes/` |
| **观测舱 GUI** | 人类用：本机浏览器 / pywebview 原生窗口，仅绑 127.0.0.1 | `integrations/gui-v3/` |

核心零改动即可接新宿主。**新装一律用 `core/plugin/`**（随发布树的 dsh 插件本体，v3.2.x，安装命令见上文「dsh 宿主」）；
[integrations/dsh/README.md](integrations/dsh/README.md) 是 v3.1 旧线（eco-note 时代）适配器文档，
已冻结，仅供维护旧部署时参考，**勿按它新装**。

---

## ✅ 测试

```bash
# 核心引擎（__ECO_VERSION__，7 组全绿）
cd core && python run_tests.py

# v3.1 历史线（cwd=src/memory_ecology）
python test_eco_gates.py                # 四门持久化回归
python test_eco_note_error_query_v2.py  # 根因分层回归
python test_eco_version.py              # 健康行口径回归
python test_memory_query_time.py        # 时间维检索回归
python test_write_gate_conflict_tri.py  # 失效理由回归
python test_evidence_chain.py           # 证据链回归
python test_retrieval_report.py         # 零结果率观测回归
python test_vector_cache.py             # 向量缓存 shadow index 回归
python test_recall_rrf.py               # OR 召回 + RRF 融合回归
python test_concurrent_writes.py        # 并发写入
python test_safeio.py                   # 安全写路径回归
python test_similarity.py               # 相似度后端回归

# dsh 适配器（cwd=integrations/dsh）
node test_smoke.js    # EAC：注册面 / fail-open / 探针 reason 日志
node test_common.js   # 子进程语义
node test_cordis.mjs  # 官方桌面版：纯函数 + apply() 端到端
```

> 缺 python 或 zstandard 时测试会**显式失败**而不是静默跳过——它们是关键护栏。
> 变异测试组（`test_mutation.py`）专门验证"这些断言真的抓得住 bug"：把所有关键修复
> 逐个改回错误实现，看测试是否变红。

---

## 🧭 已知未完成项（如实登记）

> 债清单一向"让债可见"。以下几件事**有明确结论、只是还没做**，集中登记在此。

### 1. `src/` 与 `core/` 的合并 —— **现在不做，先对齐能力**

**结论**：不合并成"一棵树"，而是让 `core/` 吸收 `src/` 的缺口，齐平后直接删 `src/`。

**为什么不现在做**：两棵树差的是**能力面**，不是结构。`core/` 14 个模块 / 17 个子命令，
`src/` 33 个 CLI 脚本（另有 17 个测试）；`core/` 目前**没有** v3.1 的写入门（`write_gate`）、
golden 评测链路、`eco_health_check` 等。直接合并 = 功能回退。

**顺序（每步都可独立验证）**：
1. 让 A1 自检恢复可用 —— **已完成**（双布局探测 + 脱敏归一）；
2. 把 v3.1 独有能力逐个搬进 `core/`，每搬一个补对等测试；
3. 生产 cron 从 v3.1 脚本切到 `core/`，A1 连续通过一段时间；
4. 删除 `src/`。

### 2. `disputed_by` 的自动裁决 —— **不做全自动，改半自动**

**结论**：不做"LLM 自动判谁对谁错并删掉一条"。

**为什么**：`disputed_by` 的语义是"两条矛盾记忆并存"。让 LLM 自动裁决并删除，违背
两条硬纪律——"物理删除在代码层禁用"与"宁可重复，不可丢内容"。判错即**不可逆地丢失
用户记忆**。且实测生产库当前 **0 条 DISPUTE 标记**，无真实数据无法评估准确率。

**替代方案（待做）**：门③ 蒸馏时顺带为带 `disputed_by` 的条目产出**裁决建议**，写入
`pending/` 走既有的人工确认链路；`memory_query --disputes` 输出加一列建议。先把"建议"
跑准（可做影子比对），再谈放开自动。

### 3. v3.1 独立测试（`tests/`）尚未随发布物分发

**现状**：发布树只发 `tests/fixtures/`（论文复现依赖）。源树 `tests/` 下另有 27 个测试
（含 `test_v31_security` / `test_pii_rules` / `test_round3_redteam` 这批安全与隐私测试）
**没有随发**。

**为什么没做完**：这批测试依赖开发树的 `scripts/`、`tools/`、`agent-hooks/` 三处布局
（含 `subprocess` 里硬编码 `scripts/write_gate.py`、`ECO_SCRIPTS_DIR` 等）。实测：只补
`sys.path` 能到 201/222 通过，剩余 21 个缺 `eco_evolve` / `check_shell_sync` / `eco_extract`
等未登记件。

**为什么回退而不是硬发**：发一个"201/222 能跑"的测试目录，比不发更糟——会让人以为发布树
可回归。要么补齐到全绿，要么不发。

### 4. 门④ 的调度频率与"使用驱动"缺数据源（**生产侧，本仓库不含**）

- **门④ 一个月只跑一次**：生产 cron 表达式 `15 13 1 * *`（每月 1 号 13:15）。实测
  `review_log` 只有 2 行。也就是说"遗忘曲线"在**其余 30 天里完全不运转**。
- **"使用驱动免降级"缺数据源**：该机制依赖检索命中写 `hit` 表，但生产 `eco.db`
  **没有 `hit` 表**。缺这张表时"用过的不因日历超期被排水"实际**从未生效**。

**建议修法（未做）**：① 先确认 `hit` 表为何没建；② 把门④ 从"月跑"改成事件驱动或至少
每日；③ 再谈刷 `last_verified` 的上游改动——**顺序不能反**：数据源没有之前，刷戳逻辑
写了也跑不到。

### 5. 生态体检 cron 连续失败（Permission denied，**间歇性**）

生产「生态每日体检」曾 `failure_streak=5`，症状是写 `eco_score_history.json` 与
`生态体检报告.md` 时 `Permission denied`，但**报告内容其实生成成功**（stdout 兜底输出），
只是最后两个文件写不进去。

**已做诊断**：两个文件都不是只读、ACL 未拦；独占打开成功、父目录建临时文件成功；
**手动重跑同一脚本 exit 0**，两个文件均正常写入。

**结论：间歇性写冲突**，不是持续故障。对策（未做）：先观察下次成功后 `failure_streak`
是否归零；若高频复现，再给两个写入点加重试+退避（与 `lib/fs.py` 对 Windows `os.replace`
的既有处理同款），而不是去改权限。

### 6. A5 scope 覆盖未达标（缺 `scope` 键 + 回填工具不在生产）

生产自检 A5 报红：`detail 238 篇：新条目缺 scope 键 24（≥2026-10-25 部署线）；存量覆盖率
1%（回填目标 ≥70%）`。

**根因**：`tools/eco_backfill_scope.py` 只在本仓库，生产侧没有这个工具，回填从未在真实
数据上跑过。只读实测：`已有 scope 1 / 挤出跳过 58 / 指纹不可反推 31 / 待回填 148`。

**为什么暂缓（关键）**：生产 `detail` 里有 **3 条乱码重复条目**（如
`2026-10-08-1807-鍩庣幆26绾ц姳鍚嶅唽鏍搁獙-…` 是「城环26级花名册核验」的 GBK/UTF-8
混淆版），与正常中文命名条目**内容重复**。若现在回填，会把脏数据一并刷成有 scope，
等于把历史编码事故固化进数据面。

**稳妥顺序**：① 先处理那 3 条乱码重复条目；② 再跑 `--apply` 回填；③ 复跑 A5 确认。
在此之前 A5 保持红是**诚实状态**，不该为了让门禁变绿而回填脏数据。

---

## 📄 版本与历史

- 当前版本 **v__ECO_VERSION__**——版本唯一真源：`core/src/memory_ecology/__init__.py::__version__`
  （核心四处：`__init__.py` / `pyproject.toml` / `plugin/package.json` / `VERSION.md`，
  `python -m memory_ecology version` 校验）。
- 版本要点：[VERSION.md](VERSION.md)；完整变更：[CHANGELOG.md](CHANGELOG.md)；
  架构待办与已知裂缝：[ARCHITECTURE_TODO.md](ARCHITECTURE_TODO.md)。
- 历史版本用 **git 历史 + 标签**管理：`git checkout <tag>` 或 Releases 页下载任意版本。

## 📄 License

MIT © 2026 Wind-Leaves-Echo-Guqin（沿用上游 memory-ecology 许可）
"""

ROOT_VERSION = """# dsh-memory-ecology · 版本

- **v3.2.2**（2026-10-29）—— `core/` 五条治理缺陷修复（不采信验收报告，逐条独立复核）
  - **H1 门②③④ 结构性从不运行**（最严重）：宿主 `turn()` 内部派发
    `agent/turn-stopping`，而 `kick().finally` 才把 phase 切回 `idle`——所以处理器
    运行时 phase 必然是 `running`，`runMaintenance` 非 idle 即抛，插件每轮必抛、
    catch 只 `logger.debug`，于是 `gate all`（门②③④ 唯一调用点）**从未执行**。
    改为直接 `enqueueMaintenance`，catch 提为 `warn`。
  - **H2 损坏即隔离**：坏 frontmatter 让宽松解析返回 `({}, 全文)`，`scope` 回落
    `"global"`（最宽取值）——损坏一个字节就把圈内机密单向放宽成全局可见；门④ 又把
    全文当正文回写，造出两个 frontmatter 块 + 矛盾 status。现在损坏条目**不进目录/
    检索/门**，`root.item()` 拒绝读，`health` 增加 `damaged[]` 并置 `warn`。
  - **H3 `merged` 不消费候选** → `occurrences` 灌水（空跑 reconcile 也能 2→5）、
    pending 永久卡住、`hits=0` 也被提升进 L1。`merged` 补 `known.add(fp)`，
    已消费文件指纹剔出 `known`，判重与判消费共用 `candidate_lines()`。
  - **H4 冷路径无圈隔离**：核心 `--scope` 语义本来是对的，缺陷在插件——`memory_search`
    只认模型填的 scope（省略即全库）、`memory_load` 不校验圈。两者改用早已算出的
    `state.circle` 兜底；显式 `scope=global` 仍放行；跨圈 load 回"没有这条记忆"。
  - **新发现：`dormant` 永远回不到目录**。目录只收 `active`，而 `review()` 只刷新
    `last_verified` 不改 `status`，`bring_back()` 又没有 CLI 入口 → 记忆被反复使用
    仍不在上下文、用户无恢复通道、目录单向收缩。改为被使用即可回 `active`，
    新增 `bring-back` 命令；`archived` 保持终态。
  - **升级须知**：门②③④ 开始真跑后，首次 `gate all` 会按遗忘曲线降级一批久未使用
    的条目（实测 10 条降 5 条）。这是设计行为不是丢失——降级条目仍可被
    `memory_search` 命中、`memory_load` 读正文，再次使用会自动回目录，也可用
    `bring-back <id>` 手动恢复。
  - **测试**：修正三条"把 bug 当规格"的用例；新增插件 T8d/T13、regressions 7 条、
    core 2 条、mutation M9 对齐 + M9b/M9c。每条新用例都在旧代码上验证过会红。
  - 并发追加用例 10→16 轮（实测去锁后单轮丢行率约 28%，不是文档写的 >50%），
    消除变异组"随机红"。`core/run_tests.py` **7/7 全绿**；发布流水线重建通过。

- **v3.2.1**（2026-10-28）—— v3.2.0 核心引擎并入 `core/`（GitHub 上一直缺的就是它）
  - **`core/` 新增**：v3.2.0 重写版核心的**唯一真源**（14 个模块 / 纯 stdlib / 零宿主依赖）——
    四道门 `gates.py` + 账本 `ledger.py` + 存储与指纹 `store.py` + 圈与路由
    `config.py`/`root.py` + 检索 `retrieve.py` + 只读记忆包 `packs.py` + dsh 插件本体 `plugin/`。
  - **为什么并存不替换**：`core/`（14 模块）与 `src/memory_ecology/`（v3.1，33 个 CLI 脚本
    `write_gate.py`/`eco_quota.py`）是**两套完全不同的实现**，只有 `__init__.py`/`similarity.py`
    同名。直接覆盖会毁掉 v3.1 的 33 个脚本，所以放 `core/` 子目录；迁移完成后再合并。
  - 版本号对齐 **3.2.1**（核心四处 + 主仓库 VERSION）；`core/run_tests.py` **7/7 全绿**。
  - 修 `test_version_report_shape`：原硬编码 `"3.2.0"`，发版必红；改为断言「四处一致 + 形如 X.Y.Z」。

- **v3.2.0**（2026-10-28）—— 观测舱 GUI v3.2.0（七视图）· 项目定名 dsh-memory-ecology
  - **观测舱新增 `integrations/gui-v3/`**（液态玻璃 / iOS 外壳，版本号与核心对齐 3.2.0）：
    在原有九视图之外新增七个——**四门实况** / 圈的视角 / 注入观测 / 记忆包 /
    圈管理 / 成本面板 / 急救箱。路由 `/v3/`，`/` 也指到它（默认入口 = 最新版）。
    历史版本 `gui-v2` / `v2.1` / `v2.2` / `v2.3` 全部保留（路由仍可用），但只有 v3 是发布入口。
  - **`integrations/gui/collector_v3_2_0.py` 新增**：v3.2.0 数据层，**只调核心 CLI**，
    不自己解析 frontmatter、不自己算圈、不自己判门死活——GUI 自己算一遍必然与核心漂移，
    而漂移是静默的。
  - **四门实况区分五种状态**：`ok` / `dead`（账本一行都没有）/ `idle-spin`（有行全是心跳）/
    `starved`（有动作但期望的那种从没成功过）/ `stale`。旧界面只能看出第一种，
    而"有行但没产出"正是治理停摆两个月的伪装形态。
  - **写动作共 14 个**（quota_extrude / gate_integrate / note_adopt / skill_breed / cron_rerun /
    cron_pin / health_run / index_rebuild / firstaid_fix / circle_write / pack_enable / pack_disable /
    core_maintain / core_materialize），
    全部转发核心 CLI 并走确认闸门，**没有任何删除记忆的动作**。
  - **修两个挡路的旧缺陷**：① `_utf8_env` 是 dict 却被当函数调用（9 个写动作里 7 个在
    spawn 前就 TypeError，写通道整体是死的）；② `--root` 不传导给子进程（沙盒 GUI 的写会落到生产根）。
  - **核心探测必须验版本**：本仓库 `src/memory_ecology/` 是 v3.1 镜像，先命中它会得到
    "核心可达 ✓ 但新视图全 404"的假成功（实测踩到，有断言钉住）。
  - 桌面临捷方式改指 `启动生态观测舱v3.2.0.pyw`；启动器新增同名文件。
  - 项目正式定名 **dsh-memory-ecology**（原 "memory-ecology-兼容版" / 发布树 "memory-ecology-compat"）。

- **v2.5.0**（2026-10-18）—— 入口纪律统一 · 并发写入修复 · 第一公里补齐（用户模拟测试驱动）
  - **`lib/cli_base.py` 新增**：统一 CLI 入口基座（编码兜底 / 数据根 / 目录 / 互斥 / 失败出口）。
    26 个 CLI 脚本接入，解决"四道门四项纪律没有一项齐"的结构性问题。
  - **编码**：22 个脚本缺 stdout 兜底 → 中文 Windows（GBK）下 print emoji 崩；`write_gate`
    崩在收尾位置造成"数据已落盘却 exit 1"的假失败；异常处理里的 print 也会崩，把真正的
    FileNotFoundError 掩盖成编码错误。现全部走 `bootstrap()`（`errors="replace"`）。
  - **数据根**：门④ `eco_review` 自算 `__file__` 无视 `MEMORY_ECOLOGY_ROOT`（本线 2026-10-11
    已修，本版同步进发布物）；门② `eco_quota` 锁文件不建父目录 → 自定义根必崩，改走
    `gatekit.acquire_lock_auto()`。
  - **并发**：`gatekit` 把"本进程持有"当残留锁回收 → 同进程重入不再互斥（多 agent 同进程会踩）；
    `lib/fs.atomic_write` 在 Windows 上 `os.replace` 遇读者占用会抛 WinError 5/32
    （POSIX 允许 rename 覆盖已打开文件，Windows 不允许）→ 三级重试 + 可回滚兜底。
  - **静默丢数**：`eco_note_adopt` 用 `utf-8` 读 pending 会把 BOM 留成 `﻿`，
    正则匹配不到首行 → 第一条候选静默丢失；改 `utf-8-sig`。
  - **报错改进**：`lib/safeio` 的 type 枚举校验报错现在列出合法值。
  - **第一公里**：README 新增「0) 先存一条记忆」与「如何迁移已有记忆」；
    `eco_health_check` 新增 `--no-write`（原来忽略 `--help` 直接执行并写 3 处），空库不再给健康分。
  - **LLM 端点可配**：`lib/llm.py` 的 base_url/model 此前硬编码，只有 key 可配 →
    接第三方 OpenAI 兼容网关时 401 且报错误导。现支持 `MEMORY_ECOLOGY_API_BASE` /
    `MEMORY_ECOLOGY_API_MODEL`（运行期解析，不设时行为不变）。
  - **`config.env_key` 报错改进**：原本文案提到环境变量但该函数只读 .env，已改为列出三种解法。
  - **自适应召回条数（v2.5.1，实测驱动）**：`memory_query` 此前固定 `--top 5`。
    LongMemEval 实测：多会话类题平均需要 **2.6 条**证据，top-5 只能全命中 **66.9%**
    的题，top-20 提到 **87.2%**；端到端 multi-session 正确率因此
    **59.1% → 72.1%（+13.0pp）**，全量 MACRO **78.5% → 90.3%**。
    现按查询形态自适应：聚合类（"总共/多少次/how many"）与多跳类
    （"分别/顺序/对比/respectively"）放宽到 20，其余仍为 5。
    单点查询不受影响（实测 R@1 已达 0.87，加大只增噪声）。
    显式 `--top N` 仍优先。
  - **偏好类查询的读法提示**：实测 `single-session-preference` 从 50.0% → 93.3%，
    关键不在检索（金标 83% 已命中）而在**呈现姿态**——同一份证据，
    当"事实资料"读 vs 当"用户画像"读，差 40pp 以上。已记录为
    `docs/EXP-LOG/` 的实证结论，供下游上下文组装参考。
  - **测试**：新增 `test_concurrent_writes.py`（C1–C5）+ `test_mutation.py`（6 变异体，全抓到）。
  - **CI 门禁**：`check_entry_discipline.py`（D1 编码 / D2 数据根 / D3 建目录 / D4 锁成对）。
- **v2.4.0**（2026-10-07）——向量缓存（灰度）· OR 召回 + RRF 融合（外部调研落地第二期）
  - S6 向量缓存 shadow index：条目级 content-hash 增量/换模型整库失效/prune 镜像；
    缓存一律可丢弃（坏=无损）；`--semantic` 向量路 + 自动回退；灰度不切默认
  - S7 OR 召回 + RRF：原计划 FTS5 实测证伪（porter 英文专用/trigram 需≥3 字符 vs 中文 2 字词/
    unicode61 与子串路重合）→ 改道零依赖 OR 召回 + RRF(K=60) 融合；`--recall` 默认关=行为锁定
  - 新增测试 19 用例；FTS5 登记为 >1000 条规模化路径

- **v2.3.0**（2026-10-07）——时间维检索 · 证据链 · 零结果率观测（外部调研落地第一期）
  - S1 时间维检索：三档半连续衰减（≤7d +10 / ≤30d +5 / 31–120d 线性）+ 发生时间回退链 +
    `--since/--until/--window` 过滤（valid_time→transaction_time→first_seen）
  - S2 矛盾失效带理由：superseded_reason/superseded_at 入条目，检索命中返回理由，
    复活提案带失效原因（隔离区变可查历史时间轴）
  - S3 证据链：CONFLICT 新旧条目 evidence/superseded_by 互指 + 门③ distilled_at 蒸馏状态位
    （源失效/复活清空 → 可重蒸馏）
  - S4 零结果率观测：eco_retrieval_report.py + GUI「检索质量」页签（bge 默认化在线证据）
  - 修复 scan_memory_dirs/revive def 时绑定默认参数；sync_trees_check 行尾归一化

- **v2.2.5**（2026-09-30）——dsh 适配器审计修复（15 条），新增发布守卫
  - **内核 v4 拒收修复（关键）**：注入消息 source.kind 由 `'plugin'` 改为 producer 自己的
    名字（`'eco-note'`）。dsh 0.2.0-rc.2（hostProtocolVersion 4）的
    `assertV4MessageSources` 会抛 `format v4 message requires a producer-owned source kind`；
    官方 dsh-time-context/tmux-context 同款写法。v2.2.4 的发布物在 0.2.0-rc.2 上注入必被拒。
  - 检索工具语义修正：stdout 优先 + 退出码分档（未命中 exit 1 ≠ 检索不可用）；
    位置参数走 `--` 分隔（检索词以 `-` 开头不再被 argparse 当选项）；top 夹到 1..20
  - 数据根默认值对齐核心 `lib/config.py`（开源 `src/memory_ecology` 布局下原会算成
    `<repo>/src`，与核心认定的 `<repo>` 不一致 → 注入永远 no-hit）
  - 子进程加固：解释器解析为绝对路径（堵 Windows 当前目录植入 `python.exe`，CWE-427）；
    默认最小环境（只透传系统 + DSH_/ECO_/MEMORY_ECOLOGY_，`ECO_KEEP_FULL_ENV=1` 可退回）
  - 会话定位改用 `agent.id` 精确匹配（原实现按 mtime 猜"当前会话"，并发会话会串扰）；
    完全不像 id 的形态才回落 mtime；id 形态合法但文件未落盘也**不回落**（否则会拿到别的会话）
  - 报错识别改"只认 tool/result + 强证据"：`message.isError` / `Traceback (most recent call last)`
    / **独占一行**的非零 `[exit code: N]`。真机样本：原整行正则 19 条 tool/result 命中里只有
    3 条是真失败（其余是读源码/写报告/引用文档）。残余路径已写明：agent 用 read 工具读一个
    含 traceback 的日志文件仍会被当成失败（见 integrations/dsh/README 已知边界）
  - 有界解压（流式 + 尾部 2MB + 总长 64MB 上限）；状态**每会话一个文件**（原全局单文件：
    6 并发实测丢 4/6 桶 → 丢冷却与去重，同一 episode 每轮重复注入；旧文件兼容读 + 原子写）
  - 注入载荷卫生：来源与非指令声明、哨兵/注入头中和、总长 1200 字上限；探针 reason 落日志
    （cordis 与 EAC 两端），激活日志带宿主包版本（实测本机 profiles 里的 `@deepseek-ai/dsh-tools`
    是 npm 全局 dsh 0.1.1-rc.2 的 junction，而内核是桌面版 0.2.0-rc.2——漂移进日志便于发现）
  - 核心 `eco_note_query` 加库快照缓存（(mtime,size) 失效）：`rank()` 本机 237 条库 × 20 关键词
    368ms → **31ms**（含首次解析全库）；同一进程内第二次 1ms。注意探针每步是新进程，故每步
    实际开销 ≈150-200ms（其中解释器启动约 130ms），"2ms" 只适用于同进程重复调用
  - 测试：新增 `test_common.js`（12 项）/ `test_cordis.mjs`（11 项，含 apply() 端到端）/
    `test_eco_note_dsh_context_v4.py`（17 项，含 6 并发状态分片用例）；`run_tests.py` 改 glob
    自动纳入 `test_*.js|mjs`；三处核心护栏缺依赖时**显式失败**而非静默跳过
  - **发布守卫**：生成后自动校验（cordis source kind 不得为 `'plugin'`；JS 本地 import 必须
    解析得到（先剥注释）；必需文件齐备（含两端 package.json 与 GUI/Hermes 入口）；发布物 .py
    全部可编译；`from lib.X import` 的模块必须随发）；守卫与敏感词扫描**一律回滚**发布物
    ——v2.2.4 曾因漏登记 lib 模块导致"发布树 import 全炸"，此后有人工清单但无守卫

- **v2.2.4**（2026-09-30）——dsh 官方桌面版适配器（集成层新增，核心脚本零改动）
  - **新增 `integrations/dsh/package-cordis/`**：dsh 官方桌面版（cordis patch 层）适配器——
    `dsh plugin --profile desktop add` 官方路径安装；`agent/pre-step` 每步注入（照官方
    dsh-time-context 模式）+ `ctx.tools.register(defineTool)` 双工具（照官方 dsh-tool-todo 契约）；
    host 端运行期动态解析 `@deepseek-ai/dsh-tools` / `@deepseek-ai/dsh-llm`（link: 安装的包
    裸导入解析不到宿主依赖树）；JS 侧兜底节流可配（`jsThrottleMs`，默认 60s，权威窗口/冷却
    10min/15min 仍在 Python 状态机）；已在官方运行时端到端实测（工具检索命中 + 注入落盘）
  - `package/`（EAC Extension SDK 适配器）不变；Python 侧两包单源共用（package-cordis 零复制）
  - 同步结论（2026-09-30 对齐核查）：兼容版核心与生产树 v2.2.0 **内容一致**（diff 全量
    仅 EOL/bytecode 差异，EOL 归一仍属 v3 单源化批次）
  - 实机环境：dsh 官方桌面版 desktop/headless 两 profile 已部署并配置共享数据根

- **v2.2.3**（2026-09-16）
- 观测舱 GUI v0.3.3 视觉深化与形象化动效：
  · 技能星图双形态：**3D 星空**（本地 Three.js r160，无 CDN，懒加载）玻璃球体 =
    MeshPhysicalMaterial 真实透射折射 + ior 1.46 + clearcoat + 内部衰减体色 + 内发光，
    配程序化星云环境贴图（equirect → PMREM）提供环境反射/折射（即"简单光追"）、
    枢纽节点泛光、(0,0,0) 血缘边流动光点、星尘 1400 点、雾与 ACES 色调映射；
    材质分档（枢纽=清透水晶 / 叶节点=哑光磨砂）、三种布局、拖拽旋转 / 滚轮推拉 /
    射线拾取悬停点选、HTML 悬浮标签；WebGL 不可用自动回退平铺视图，切走即停 rAF
  · **平铺视图**（保留）：深空观测窗（星云/星尘/视差/暗角）+ 哑光玻璃球体 + 类别软分组
    （聚类引力 + 星云云团 + 组名）+ 边方向箭头与流动光点 + 标签贪心避让 + 收敛自动 fit +
    悬停涟漪光流与邻域高亮 + 单击聚焦
  · 血缘谱系从纯文本升级为**真实谱系图**：手写分层布局（代际最长路径 + 列内父位置排序），
    繁殖（父→子）与合并（子→父）两类边带方向箭头与流动光点，悬停高亮整条链路
  · 形象化动效：记忆库条目错峰入场（轮询只动画变化项）/ 水位流动 / 徽章过渡 / 检索关键词
    高亮 / 隔离区低温视觉 / 惰性入场；候选孵化台待孵化形态；时间线泳道顺序入场与回放事件带；
    体检评分环闪光；动作日志新行高亮
  · 动效基座 viz/anim.js：缓动与插值、可中断错峰入场、IntersectionObserver 封装、
    变化签名比对、CSS 动效 token、三档强度与 prefers-reduced-motion 全覆盖；
    帧率监测寄生在已有循环中（不新增长驻 rAF），低帧率自动降级
  · 图标与空态统一：内联 SVG 图标集（16px 网格 / stroke 1.5 / currentColor）替代 unicode 符号；
    四类专属空态插画；清理遗留死 CSS
  · 回归：九视图 × 三色板 × 三档强度零报错零溢出；单帧 JS 成本 3D 全图 2.19ms /
    平铺全图 1.15ms
- Three.js 3D 星图：**已纳入主线**（v2.2.2 的"暂不纳入"由宿主改判）；库随发布（MIT，1.27MB），
  懒加载且 WebGL 不可用时自动回退平铺视图
- 核心脚本（src/memory_ecology）零改动，与上游 v2.2.0 功能一致

- **v2.2.2**（2026-09-15）
- 观测舱 GUI v0.3.2 视觉重构与体验升级：
  · 视觉体系：色板收敛至 3 套精修（雾白/青瓷/深空大屏）、自托管 Inter + Noto Sans SC
    屏显字体（SIL OFL，无 CDN）、亚克力毛玻璃质感、响应式凝练布局（≤1400px 窄图标导航，
    桌面 1280×860 窗口自动命中）、次要文字对比度按 4.5:1 重校
  · 新增功能：键盘快捷键（g+字母跳九视图、? 参考表、/ 聚焦检索）、Ctrl+K 命令面板
    （模糊匹配视图/动作/操作，写动作仍走确认闸门 + dry-run）、检索历史持久化、
    一键导出视图数据（JSON/Markdown/纯文本）、诊断包一键复制、
    浏览器桌面通知（仅后台标签页推送）、新手引导扩至五步
  · 动画系统：视图切换 300ms 过渡（消除闪烁）、星图 D3-force 同级物理引擎
    （斥力/弹簧/碰撞/退火/拖拽惯性，零依赖，质心对齐视图中心）、Canvas 粒子光晕
    （仅深色大屏与巡航激活）、数字计数递增、图表入场过渡、动画强度三档（轻/中/满载）
  · 长期观测与桌面集成：自动轮询（15/30/60 秒/关，无变化不重渲染）、
    全屏巡航双模式（轮播九视图 / 固定驾驶舱大屏）、setup_ecology.py 一键安装、
    tray.py 托盘常驻 + 开机自启（当前用户级，默认关，可逆，缺依赖时优雅降级）
- Three.js 3D 星图评估：**已评估、暂不纳入主线**（避免 +1.27MB 库体与 WebGL 依赖，
  2D SVG 力导向已覆盖核心任务；原型页保留在开发树，不随发布）
- 发布流水线：GUI 发布集新增 setup_ecology.py / tray.py
- 核心脚本（src/memory_ecology）零改动，与上游 v2.2.0 功能一致

- **v2.2.1**（2026-09-15）
- 集成层新增：观测舱 GUI v0.3（`integrations/gui/`，可操作驾驶舱——只读观测 +
  走确认闸门的写操作，全部转交核心 CLI；含急救箱与启动器三入口：cmd / .pyw /
  install_desktop.py 桌面生成器）
- 启动器加固：仓库内 cmd 全部相对路径 + 解释器探测（pythonw → pyw → 报错可读）；
  急救箱修复路径拼接 bug（双位置均不可用）并补失败检测；敏感词扫描扩展名新增
  .cmd/.html/.css/.pyw
- 核心脚本（src/memory_ecology）零改动，与上游 v2.2.0 功能一致

- **v2.2.0**（2026-09-06）
- 阶段 C 评测验收（迭代策略 §3）：伪 gold 构建器（eco_eval_gold.py，gold=实际被使用过的记忆）
  + eco_eval 两条新判定线（INJ 注入超限 ≤5 次/30 天、GOLD top5 相关率 ≥60%）+ --gate 纯规则门禁模式
- 安全写路径加固（v2.2 首项）：lib/safeio.py（路径白名单/防穿越 + schema 校验 + 原子写/备份）
  接线 write_gate / eco_note_adopt / 注入 hook 全部条目写入方
- 注入文本压缩（每条目 1 行）+ 注入遥测新增 chars/error 字段
- 独立 code review 14 项 findings 全修；双树三轮回测全绿、改动文件字节一致
- 历史：v2.1.2（2026-09-06）两树同号同源起点 / v2.1.1-compat.1（2026-09-05）首个兼容版
"""

ROOT_CHANGELOG = """# Changelog

## v2.5.0（2026-10-18）——入口纪律统一 · 并发写入修复 · 第一公里补齐

> 驱动：三轮用户模拟测试（纯新手 / 英文老手 / 大库用户）+ 独立终审 + OCR 规则审查。
> 主题不是加功能，是把散落在 26 个 CLI 里的公共纪律收敛为一个强制入口，并做成 CI 门禁。

### 新增
- `lib/cli_base.py`：`bootstrap()` / `resolve_root()` / `ensure_dirs()` / `ensure_parent()` /
  `locked()` / `fail()`。零依赖，POSIX 上编码修复是 no-op。
- `test_concurrent_writes.py`：C1 同门并发 / C2 跨门并发 / C3 stale 锁回收 /
  C4 原子写可见性（13 万次并发读）/ C5 同进程锁重入。
- `test_mutation.py`：6 个变异体验证上述测试的有效性。
- `check_entry_discipline.py`：CI 门禁（D1 编码 / D2 数据根 / D3 建目录 / D4 锁成对）。

### 修复
| 现象 | 级 |
|---|---|
| `write_gate` 数据已落盘却 exit 1（崩在收尾 print 的 emoji） | Blocker |
| 异常处理里的 print 自身崩，真错被编码错掩盖 | Blocker |
| 门④ 自算数据根，无视 `MEMORY_ECOLOGY_ROOT` | Blocker |
| 门② 锁文件不建父目录，自定义数据根必崩 | Blocker |
| `gatekit` 锁把本进程持有当残留回收 → 同进程重入不互斥 | 并发 |
| `atomic_write` 在 Windows 上遇读者占用失败 | 并发 |
| pending 带 BOM 时第一条候选静默丢失 | 静默丢数 |
| type 枚举报错不说合法值 | 可用性 |
| `eco_health_check` 忽略 `--help`、无开关写 3 处、空库给健康分 | 可用性 |

### 测试
新增 5 并发用例 + 6 变异体；回归 150 个既有测试全绿；门禁 39 个脚本 0 违规。
代码换行统一为 LF。

## v2.4.0（2026-10-07）——向量缓存（灰度）· OR 召回 + RRF 融合

> 外部调研落地第二期。纪律不变：**新能力全部默认关闭**，观察期后另行决定默认化。

- **S6 向量缓存 shadow index（memory_query --semantic / lib/vector_cache.py）**：
  SQLite BLOB 存归一化向量（零 numpy，array('f') 互转）；条目级 content_hash(sha256)
  增量——未变跳过重嵌（memsearch/EverOS 同配方）；模型名变化整库失效（向量空间不可混）；
  prune 镜像删除；单条重嵌失败跳过不阻塞（**缓存一律可丢弃：坏/删=无损，源真源仍是 Markdown**）。
  `--semantic` 向量检索路（余弦 + 时间衰减 + 状态罚分），embedding 不可用自动回退词面并提示；
  `MEMORY_ECOLOGY_VECTOR_CACHE` 可覆盖缓存路径。**灰度：默认后端仍 difflib，观察期后定默认**
- **S7 OR 召回 + RRF 融合（memory_query --recall）**：原计划 FTS5 双路，实测三配置均不适合中文
  （porter 英文专用；trigram 要求 ≥3 字符而中文常用词恰 2 字，「备份」「框架」全零；unicode61
  与子串路高度重合）——按「对比排序变化幅度后才合入」纪律改道：**or_recall** 任一 term 命中即召回
  （补多词 AND 的召回缺口：实测「记忆 检索」AND=0 而 OR=5），**rrf_fuse**（Cormack 2009，K=60）
  按名次融合两路（两路分值口径不同，只吃名次）。`--recall` 默认关闭=**行为锁定**；
  实测 91 条语料「记忆 检索」默认零命中 → `--recall` 召回 5 条相关条目
- 测试：新增 test_vector_cache（9）/ test_recall_rrf（10）共 19 用例
- FTS5 降级登记为 >1000 条的规模化路径（ARCHITECTURE_TODO §三）

## v2.3.0（2026-10-07）——时间维检索 · 证据链 · 零结果率观测

> 本轮源自 20+ 外部记忆系统调研（~/.memory-ecology/designs/外部记忆系统调研-v1.0.md）：
> 三个被多项目独立收敛的原则中，superseded 隔离区与 git 基因库已被验证，**证据链溯源**是补上的那块。

- **S1 时间维检索（memory_query）**：score_entry 时间衰减扩为三档半连续
  （≤7d +10 / ≤30d +5 既有操作点不动，31–120d 线性 5→0 消除 30 天硬悬崖）；回退链
  last_hit→last_seen→valid_time→first_seen——旧格式无 last_hit 的存量条目零迁移参与打分；
  新增 `--since/--until/--window` 按「发生时间」（valid_time→transaction_time→first_seen）过滤，
  无时态条目跳过并计数（Hindsight time-window 语义：时间窗查询只匹配有时间的记忆）；
  json 补 valid_time/transaction_time。首次消费 Graphiti 双时态轻量版的时间维
- **S2 矛盾三态+失效带理由（write_gate/memory_query）**：门① CONFLICT 失效理由入条目
  （superseded_reason=LLM reason 键 + superseded_at 失效日期）——隔离区从垃圾箱变可查历史
  时间轴（graphiti/EverOS deprecated_by/deja-vu rejected 五处独立收敛的「失效不删除」补全）；
  memory_query 命中失效条目返回「已失效：理由（被 X 取代）」，复活提案表带失效原因列
- **S3 证据链（write_gate/distill_stage/memory_query）**：CONFLICT 换代的新条目 evidence
  指向被取代旧条目（与旧条目 superseded_by 互指，换代链双向可溯）；门③候选落盘即登记源条目
  distilled_at（Hindsight consolidated_at 同思想，只标记不判定）；源失效/复活清空 distilled_at
  → 下轮门③重蒸馏，「源变→结论待更新」可逆闭环
- **S4 零结果率观测（新 eco_retrieval_report + GUI）**：聚合 .memory_hits.jsonl（零结果也落行）
  → 按日零结果率/平均命中/零结果查询 Top N；GUI health 页新增「检索质量」页签。
  零结果率是 difflib→ngram→embedding 默认化决策的在线证据（OpenViking observer 同思想）
- **修复**：memory_query.scan_memory_dirs/revive 默认参数 def 时绑定 → 测试 patch 模块路径失效
  （改调用时解析）；sync_trees_check 行尾归一化（CRLF 幻影漂移 34→16，剩余为真实内容漂移）
- **回迁**：live 2026-10-05 蒸馏三修复（STABLE_DAYS=14 稳定口径 / 推理模型预算阶梯 / 批量 env）
  进 compat 与兼容版；live eco_git_commit 基因库排除 .lock 修复进兼容版
- 测试：新增 test_memory_query_time（7）/ test_write_gate_conflict_tri（10）/
  test_evidence_chain（8）/ test_retrieval_report（6）共 31 用例

## v2.2.5（2026-09-30）

- **修复（关键）**：dsh 内核 v4 拒收 `source.kind='plugin'` 的注入消息——改为 producer 自己的
  名字 `'eco-note'`（官方 time-context/tmux-context 同款）。v2.2.4 的发布物在 dsh 0.2.0-rc.2
  上注入通道必被拒收（`format v4 message requires a producer-owned source kind`）
- 修复：检索工具"未命中（exit 1）"被报成"检索不可用"；检索词以 `-` 开头被 argparse 当选项；
  `top` 非法值原样透传（现夹 1..20）
- 修复：开源 `src/memory_ecology` 布局下数据根默认值算成 `<repo>/src`（核心认定 `<repo>`）→
  注入永远 no-hit
- 安全：解释器解析为绝对路径（CWE-427 当前目录植入）；子进程默认最小环境
- 修复：会话定位改用 `agent.id` 精确匹配；冷却/节流按会话隔离（id 合法但文件未落盘也不借用别的会话）
- 修复：报错识别只认 `tool/result` + 强证据（`message.isError` / Traceback / 独占一行的非零
  `[exit code: N]`）。真机样本：原整行正则 19 条命中里只有 3 条真失败；**残余路径**：read 工具
  读一个含 traceback 的日志文件仍会被当成失败（README 已知边界）
- 修复：有界解压（流式；尾部 2MB + 总长 64MB 上限）；状态**每会话一个文件** + 原子写
  （原单文件 RMW：6 并发实测丢 4/6 桶 → 丢冷却与去重）
- 卫生：注入载荷加来源/非指令声明、中和哨兵、总长上限；探针 reason 两端都落日志（含 EAC）
- 性能：核心 `eco_note_query` 库快照缓存（`rank()` 368ms → 31ms 冷；同进程第二次 1ms。
  探针每步是新进程，故每步实际 ≈150-200ms）
- 测试：新增 3 个测试文件（JS 23 项 + Python 17 项，含 6 并发状态分片用例）；`run_tests.py`
  glob 自动纳入 JS 测试；核心护栏缺依赖时显式失败（不再静默 skip）
- 工程：`publish_compat.py` 增发布守卫（source kind / JS 本地 import（先剥注释）/ 必需文件
  （含两端 package.json + GUI/Hermes 入口）/ .py 可编译 / `from lib.X` 随发）；守卫与敏感词
  扫描一律回滚；删掉重复定义的 `_norm_bytes`/`_deploy_sources` 死代码
- 插件包版本 `0.1.0` → `0.1.1`（两端同步，装到机器上可区分代际）

## v2.2.4（2026-09-30）

- 新增 dsh 官方桌面版（cordis patch 层）适配器 `integrations/dsh/package-cordis/`：
  `agent/pre-step` 报错根因注入 + `eco_note_query` / `eco_note_error_query` 原生工具，
  官方 `dsh plugin --profile desktop add` 路径安装，宿主包运行期动态解析，fail-open 只读
- 发布集扩展：package-cordis 三件（package.json / index.js / cordis.patch.yml）随发
- EAC Extension SDK 适配器（`package/`）保持不变；Python 侧单源共用
- 核心脚本零改动（与生产树 v2.2.0 内容一致，对齐核查 2026-09-30）

## v2.2.3（2026-09-16）

- 观测舱 GUI v0.3.3：技能星图双形态（3D WebGL 玻璃球体 + 简单光追 / 2D 力导向平铺深空玻璃版）、
  血缘谱系真图（分层布局 + 方向箭头 + 流动光点）、记忆库与孵化台/时间线/体检/动作日志形象化动效、
  动效基座（anim.js + CSS token + 三档强度 + 低帧率自动降级）、内联 SVG 图标集与空态插画
- Three.js 3D 星图纳入主线（库随发布，懒加载，WebGL 不可用自动回退平铺视图）
- 核心脚本零改动；两树版本号同步

## v2.2.2（2026-09-15）

- 观测舱 GUI v0.3.2：视觉重构（三色板精修 + 自托管屏显字体 + 毛玻璃质感 + 响应式凝练布局）
- 体验升级：键盘快捷键与 Ctrl+K 命令面板、检索历史、一键导出、桌面通知、诊断包、
  动画强度三档、星图力导向物理引擎、粒子光晕、自动轮询、全屏巡航双模式
- 桌面集成：`setup_ecology.py` 一键安装（环境体检 → 依赖引导 → 端口探测 → 桌面入口 →
  首跑自检）、`tray.py` 托盘常驻 + 开机自启（当前用户级、默认关、可逆）
- Three.js 3D 星图：已评估、暂不纳入主线（原型不随发布）
- 核心脚本零改动；两树版本号同步

## v2.2.1（2026-09-15）

- 集成层新增观测舱 GUI v0.3（`integrations/gui/`）接入开源发布树：
  - 可操作驾驶舱：九视图只读观测 + 四类写操作（配额挤出/写入门整合/经验采纳/技能孵化），
    全部经确认闸门（高风险红字 + 勾选）转交核心 CLI 子进程执行，动作前后记 action_log.jsonl
  - 急救箱（first_aid.py）：自检 → 修复 → 重启服务 → 开窗；生产 cron 领地只诊断不动手
  - 启动器三入口：`启动生态观测舱.cmd`（相对路径）/ `启动生态观测舱.pyw`（无黑框）/
    `install_desktop.py`（按机器生成桌面指针入口，杜绝手工复制绝对路径）
- 启动器修复（安全）：急救箱 cmd 路径拼接 bug（原 `%~dp0integrations\\gui\\...`
  在桌面/仓库两个位置均指向不存在文件，双击失败且伪装成功）；补 python/py 探测链与
  errorlevel 失败提示；cmd 统一 CRLF
- 发布流水线：敏感词扫描扩展名白名单新增 .cmd/.html/.css/.pyw（堵住启动器与前端页面
  不被扫描的盲区）；scripts_dir 探测新增发布树 src/memory_ecology 布局
- 核心脚本零改动；数据根/检索 CLI 定位链保持 参数 > 环境变量 > 探测 > 兜底

## v2.2.0（2026-09-06）

- 阶段 C 评测验收：新增 eco_eval_gold.py 伪 gold 构建器（命中=实际使用，会话文本即证据）；
  eco_eval 新增 INJ（注入超限 ≤5 次/30 天）与 GOLD（top5 相关率 ≥60%）两条门禁判定线，
  INSUFFICIENT（数据不足）诚实单列不计 FAIL；--gate 纯规则模式可入 cron/回归
- 安全写路径：新增 lib/safeio.py（条目名白名单+防穿越、detail/experience frontmatter schema
  校验、原子写+备份、JSONL 容量护栏）；write_gate/eco_note_adopt/注入 hook 全部条目写入方接线
- 注入文本压缩为每条目 1 行（体积约减半）；.injected.jsonl 新增 chars/error 遥测字段
- 独立 code review 15 项 findings：14 项修复（adopt per-candidate 异常隔离、gold 时间归因、
  CONFLICT 时序、时区归一、原子化收口等），1 项记录为不可达口径（白名单小项）

## v2.1.2（2026-09-06）

- dsh 适配器修复：会话文件 glob 对齐真实命名（session.jsonl.zstd）、流式 zstd 解压
  （read_across_frames）、原生工具指向核心 scripts 目录、只扫最新活跃会话
- 注入质量门：verified 经验优先 + 注入文本标注状态 + 三次态确认命中 ≥2 自动 draft→verified
- hit 反馈回路修复（entry_ids 键名 + 判定记账去重），last_hit 真实回写
- write_gate 消费后标记 .done.md；健康告警产出探针复活且排除已消费件
- 数据根：开源 src 布局默认落到仓库根（原落 src/）；实现 MEMORY_ECOLOGY_API_KEY 环境变量
- 发布流水线：保留 .git、--out 守卫、敏感词扫描失败自动回滚
- 测试：日期炸弹修复 + 新增 mark_consumed / auto-verify 用例，全绿

## v2.1.1-compat.1（2026-09-05）

- 首个兼容版：自上游 v2.1.1 复制（四道门 + eco_note 家族 + lib + 测试，76 用例基线全绿）
- 多宿主化：全部路径常量收敛 `lib/config.py`（`MEMORY_ECOLOGY_ROOT` 可覆盖）
- 新增 dsh 适配器（Extension SDK：provideContext 根因注入 + eco_note_query/error_query 原生工具）
- 报错检索新增**根因分层**（异常类名提取：同根因 > 泛化 > 异根因沉底），修复按词匹配误注入
- eco_version 健康行字符数口径修复（len() 即字符数，删除 //3）
"""

ROOT_LICENSE = """MIT License

Copyright (c) 2026 Wind-Leaves-Echo-Guqin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

CONFIG_EXAMPLE = """# Memory Ecology Compat 配置示例（复制为 config.yaml 后按需修改；缺省用默认值即可运行）
# 数据目录（默认：跟随安装位置自动派生；多宿主共享根/多实例用环境变量
#   MEMORY_ECOLOGY_ROOT=/path/to/root 覆盖）
# 模型（默认 deepseek-v4-flash；任意 OpenAI 兼容端点）
# model: deepseek-v4-flash
# llm_base_url: https://api.deepseek.com/v1/chat/completions
# 密钥：环境变量 MEMORY_ECOLOGY_API_KEY 或数据根 .env
# 阈值（不配置则用代码内保守默认）
# quota_ratio: 0.85
# observe_days: 30
"""


# ── 发布守卫（v2.2.5）────────────────────────────────────────────────────
# 为什么需要：发布树是"手工清单 + 脱敏改写"生成的，历史上两类事故都是"生成物看起来正常、
# 跑起来才炸"：
#   ① 漏登记 lib 模块 → 发布树 import 全炸（v2.2.4 补登记 gatekit/memstore/inject_gate）；
#   ② 适配器沿用内核已废弃的消息语法 → 注入通道在实机上必被拒收（v2.2.4 发布物
#      source.kind='plugin'，dsh 0.2.0-rc.2 会抛 "format v4 message requires a
#      producer-owned source kind"）。
# 守卫只做静态可判定的事：秒级、零副作用、零网络。实机行为仍由 integrations/dsh 自带测试
# （node test_*.js|mjs / python test_*.py）与宿主侧验收负责。
_GUARD_REQUIRED = [
    "integrations/dsh/README.md",
    "integrations/dsh/test_smoke.js",
    "integrations/dsh/test_common.js",
    "integrations/dsh/test_cordis.mjs",
    "integrations/dsh/package/index.js",
    "integrations/dsh/package/package.json",
    "integrations/dsh/package/lib/common.js",
    "integrations/dsh/package/python/eco_note_dsh_context.py",
    "integrations/dsh/package/python/test_eco_note_dsh_context.py",
    "integrations/dsh/package/python/test_eco_note_dsh_context_v4.py",
    "integrations/dsh/package-cordis/index.js",
    "integrations/dsh/package-cordis/package.json",
    "integrations/dsh/package-cordis/cordis.patch.yml",
    # GUI 观测舱入口（漏登记 = 发布树缺页）
    "integrations/gui/eco_gui.py",
    "integrations/gui/collector.py",
    "integrations/gui/static/index.html",
    "integrations/gui/static/js/core.js",
    # Hermes 集成层入口
    "integrations/hermes/agent-hooks/eco_note_inject.py",
    "integrations/hermes/scripts/eco_note.py",
    "src/memory_ecology/eco_note_query.py",
    "src/memory_ecology/eco_note_error_query.py",
    "src/memory_ecology/lib/config.py",
    # 论文实验工件（漏登记 = 论文 artifact 缺件）
    "experiments/ablation.py",
    "experiments/baselines.py",
    "experiments/paper_figs.py",
    "experiments/README.md",
    "experiments/results/summary.json",
    "experiments/results/baselines-summary.json",
    # ablation 的运行依赖（漏发 = RQ1 复现 ImportError）
    "tests/fixtures/__init__.py",
    "tests/fixtures/eco.py",
]
_GUARD_JS_LOCAL = re.compile(r"""(?:require\(\s*|from\s+|import\(\s*)['"](\.[^'"]+)['"]""")
# OCR 2026-10-03：原正则只认 `from lib.X import` / `import lib.X` 点号形式，漏掉本项目
# 主流写法 `from lib import X`（sqlindex 漏登记未被守卫拦下的正是这个盲区）；
# 同时处理同行逗号多名导入（`from lib import a, b` 逐名核对）
_GUARD_PY_LIB = re.compile(
    r"^\s*(?:from\s+(lib(?:\.[\w]+)*)\s+import\s+([\w.]+(?:\s*,\s*[\w.]+)*)"
    r"|import\s+(lib(?:\.[\w]+)+))",
    re.M)


def _strip_js_comments(text: str) -> str:
    """粗剥 JS 注释（守卫只看代码，不看注释里的反例说明）。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)^[ \t]*//.*$", "", text)
    return text


def publish_guard(out: pathlib.Path) -> list[str]:
    """发布物静态守卫 → 问题清单（空 = 通过）。"""
    problems: list[str] = []

    # 1) cordis 注入消息必须是 producer-owned source kind（内核 v4 会拒收 'plugin'）
    cordis = out / "integrations" / "dsh" / "package-cordis" / "index.js"
    if not cordis.is_file():
        problems.append("缺 integrations/dsh/package-cordis/index.js")
    else:
        code = _strip_js_comments(cordis.read_text(encoding="utf-8", errors="replace"))
        if re.search(r"kind:\s*['\"]plugin['\"]", code):
            problems.append("package-cordis/index.js 仍用 kind:'plugin'"
                            "——dsh 0.2.0-rc.2 的 v4 校验会拒收注入消息")
        # 正例：构造 user message 的 source 里 kind 必须是标识符（= producer 名），不是字面量
        if not re.search(r"source:\s*\{[^{}]*kind:\s*[A-Za-z_$]", code, re.S):
            problems.append("package-cordis/index.js 未见 `source: { kind: <标识符> … }`"
                            "（producer-owned source kind）")

    # 2) 必需文件齐备（手工清单漏登记的直接体现）
    for rel in _GUARD_REQUIRED:
        if not (out / rel).is_file():
            problems.append(f"缺文件（发布集漏登记？）: {rel}")

    # 3) JS 本地相对 import 必须解析得到（漏登记 common.js 这类）
    #    审 P2：先剥注释——注释里写一句 require('./x') 不该被判成缺文件
    for p in list(out.rglob("*.js")) + list(out.rglob("*.mjs")):
        text = _strip_js_comments(p.read_text(encoding="utf-8", errors="replace"))
        for spec in _GUARD_JS_LOCAL.findall(text):
            target = (p.parent / spec).resolve()
            if not target.is_file():
                problems.append(f"{p.relative_to(out)} → import '{spec}' 解析不到")

    # 4) 发布物 Python 必须可编译（脱敏改写/漏文件的语法级后果）
    for p in out.rglob("*.py"):
        try:
            compile(p.read_text(encoding="utf-8", errors="replace"), str(p), "exec")
        except SyntaxError as e:
            problems.append(f"{p.relative_to(out)} 无法编译: {e}")

    # 5) `from lib.X import` / `from lib import X` 的模块必须随发（同目录 lib/ 或核心 lib/）
    lib_dirs = [d for d in out.rglob("lib") if d.is_dir()]
    for p in out.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in _GUARD_PY_LIB.finditer(text):
            # OCR 2026-10-03：裸 `from lib import a, b` 的名字即 lib 子模块，逐名核对；
            # `from lib.X import name` 的 name 是属性（可能非模块），只核对模块 X 本身；
            # `import lib.X` 取末段模块名
            if m.group(2) is not None:
                if m.group(1) == "lib":
                    # v2.5.6：裸 `from lib import a, b` 里，双下划线名字是**包属性**
                    # （PEP 396 的 `__version__`），不是子模块 —— 守卫此前把
                    # `from lib import __version__` 误判成"缺 __version__.py"。
                    mods = [n.strip().split(".")[-1] for n in m.group(2).split(",")
                            if n.strip() and not n.strip().startswith("__")]
                else:
                    mods = [m.group(1).split(".")[-1]]
            else:
                mods = [m.group(3).split(".")[-1]]
            for mod in mods:
                cands = [p.parent / "lib" / f"{mod}.py", p.parent / f"{mod}.py"]
                cands += [d / f"{mod}.py" for d in lib_dirs]
                if not any(c.is_file() for c in cands):
                    problems.append(f"{p.relative_to(out)} 引用 lib.{mod}，但发布物里没有 {mod}.py")

    # 6) 无构建/运行残留（v2.2.5 审查 P2：发布后跑测试会留下 .pyc，整目录打包会带出去）
    for p in out.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(out).as_posix()
        if ("__pycache__" in p.parts or p.suffix == ".pyc" or ".bak" in p.name
                or p.name.startswith(".dsh_inject_state")):
            problems.append(f"发布物残留运行/构建产物: {rel}")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    out = args.out
    try:
        actions = build(out)
    except SystemExit:
        raise
    except Exception as e:
        # OCR 2026-10-03（medium）：build 开头已清空旧发布物，中途异常若不处理
        # 会留下半更新树，极易被"没看退出码就 add -A && push"带出去——回滚（保 .git）
        outp = pathlib.Path(out).resolve()
        if outp.is_dir():
            for child in list(outp.iterdir()):
                if child.name == ".git":
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink()
            print("（build 异常：已回滚发布物，仅保留 .git）")
        print(f"❌ build 失败: {e}")
        return 1

    # 发布守卫（v2.2.5，见 publish_guard docstring）
    guard = publish_guard(out)

    # 敏感词扫描（脱敏后的发布物必须干净）
    issues = []
    for p in out.rglob("*"):
        if p.suffix.lower() not in (".py", ".js", ".mjs", ".json", ".md", ".yaml", ".yml", ".txt",
                                    ".cmd", ".html", ".css", ".pyw"):
            continue
        hits = scan_sensitive(p.read_text(encoding="utf-8", errors="replace"))
        if hits:
            issues.append(f"  {p.relative_to(out)}: {hits}")

    print(f"发布物: {out}（{len(actions)} 个文件）")
    if issues:
        print("\n=== ❌ 敏感词扫描未过 ===")
        for i in issues:
            print(i)
    if guard:
        print("\n=== ❌ 发布守卫未过 ===")
        for g in guard:
            print(f"  {g}")
    if issues or guard:
        # Q31 修复（2026-09-06）+ v2.2.5 审查对称化：两类失败一律回滚已落盘的发布物。
        # 发布目标是 git 仓库，留半更新状态最容易被"没看退出码就 add -A && push"带出去。
        # OCR 2026-10-03：先物化列表再删（同 build() 清空循环）
        for child in list(out.iterdir()):
            if child.name == ".git":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        print("（已回滚本次发布物，仅保留 .git；修好开发树后重跑本脚本）")
        return 1
    print("✅ 敏感词扫描通过（发布物无个人标识）")
    print("✅ 发布守卫通过（source kind / JS 本地 import / 必需文件 / .py 可编译 / lib 随发）")
    return 0


# ── deploy-live（PORT_SPEC §5：dev→live 单向部署）──────────────────────────
# 注（v2.2.5 审查）：本段曾有 `_norm_bytes` / `_AGENT_HOOKS_SKIP` / `_deploy_sources`
# 的重复定义（前一组是死代码，Python 取的是后一组），已删——留一份，避免改一处不生效。
_CRLF = bytes((13, 10))
_LF = bytes((10,))


def _norm_bytes(p: pathlib.Path) -> bytes:
    """读文件并归一 LF——drift 比较基准（dev CRLF vs live LF 不应虚报为差异）。"""
    try:
        return p.read_bytes().replace(_CRLF, _LF)
    except OSError:
        return b""


# agent-hooks 里属于运行时产物/本地状态、不参与部署的文件（G10 白名单制）
_AGENT_HOOKS_SKIP = re.compile(
    r"(\.bak|__pycache__|\.log$|state\.json$|\.pyc$)")


def _deploy_sources(dev_root: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """返回 [(dev 侧文件, live 侧相对路径)]——scripts 顶层 + lib + agent-hooks。

    覆盖面（2026-09-27 扩展）：影子接线（eco_note_inject 等 agent-hooks）随批部署；
    此前 deploy_live 只覆盖 scripts/，hooks 需手工拷贝（违反「live 只被脚本写入」纪律）。
    """
    out: list[tuple[pathlib.Path, str]] = []
    for f in sorted((dev_root / "scripts").glob("*.py")):
        out.append((f, f"scripts/{f.name}"))
    lib = dev_root / "scripts" / "lib"
    if lib.is_dir():
        for f in sorted(lib.glob("*.py")):
            out.append((f, f"scripts/lib/{f.name}"))
    hooks = dev_root / "agent-hooks"
    if hooks.is_dir():
        for f in sorted(hooks.glob("*")):
            if f.is_file() and not _AGENT_HOOKS_SKIP.search(f.name):
                out.append((f, f"agent-hooks/{f.name}"))
    return out


def deploy_live(live_root: pathlib.Path, dry: bool = False) -> int:
    """单向 dev→live 部署：前置漂移检查 → 复制 → 归档 → live 仓库自动 commit。

    纪律：live 只被此脚本写入。任何手动修改 live 文件都会被下次 deploy 覆盖。
    覆盖面：scripts/*.py + scripts/lib/*.py + agent-hooks/*.py
    （agent-hooks 仅源文件——运行时产物 .log/state/.bak 跳过，G10 白名单制）。
    漂移比较按 EOL 归一（dev CRLF vs live LF 不再虚报为差异）。
    回滚：git -C <live_root> checkout <tag>~1（每次部署自动打 tag）。
    """
    import subprocess
    dev_root = pathlib.Path(__file__).resolve().parent
    if not (dev_root / "scripts").is_dir() or not (live_root / "scripts").is_dir():
        print("❌ dev 或 live scripts/ 目录不存在")
        return 1

    sources = _deploy_sources(dev_root)

    # 前置漂移检查（EOL 归一比较）：只列实质差异
    drift = []
    for src, rel in sources:
        dst = live_root / rel
        if not dst.exists():
            drift.append(f"  + {rel}（dev 新增）")
        elif _norm_bytes(src) != _norm_bytes(dst):
            drift.append(f"  ~ {rel}（内容差异）")
    # live 独有文件：仅顶层 scripts/*.py 提示（lib/hooks 不逐一列举，避免噪音）
    dev_top = {s.name for s, r in sources
               if r.startswith("scripts/") and "/" not in r[len("scripts/"):]}
    for f in sorted((live_root / "scripts").glob("*.py")):
        if f.name not in dev_top:
            drift.append(f"  - scripts/{f.name}（live 独有，部署不触碰）")
    if drift:
        print(f"⚠️ 漂移检测（dev↔live 实质差异 {len(drift)} 项，EOL 已归一）：")
        for d in drift:
            print(d)
    else:
        print("✅ 无漂移（dev↔live 已同步）")
        return 0

    if dry:
        print("（dry-run——实际部署去掉 --dry）")
        return 0

    # 归档被替换文件（保留相对目录结构：scripts/ 与 agent-hooks/ 分池，避免同名相撞）
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    for src, rel in sources:
        dst = live_root / rel
        if dst.exists() and _norm_bytes(src) != _norm_bytes(dst):
            bak = live_root / "_archive_bak" / stamp / rel
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, bak)

    # 复制（EOL 归一 LF）
    copied = 0
    for src, rel in sources:
        dst = live_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        content = src.read_text(encoding="utf-8").replace("\r\n", "\n")
        dst.write_text(content, encoding="utf-8", newline="\n")
        copied += 1
    print(f"✅ 已复制 {copied} 个文件（scripts + lib + agent-hooks，EOL 归一 LF）")

    # O1（设计稿 2026-10-03）：部署即生成完整性清单（部署集 sha256 + Hermes 补丁
    # 锚点）——写入在 git add 之前，随部署一起进 live 仓库提交；体检 §10 据此比对
    try:
        sys.path.insert(0, str(dev_root / "scripts"))
        from lib.integrity import write_manifest
        mp = write_manifest(live_root, [r for _s, r in sources], tag=f"deploy-{stamp}")
        print(f"✅ 完整性清单 → {mp}")
    except Exception as e:
        print(f"⚠️ 完整性清单生成失败（不影响部署本身）: {e}")

    # live 仓库自动 commit + tag
    if (live_root / ".git").is_dir():
        def git(*a):
            return subprocess.run(["git", "-C", str(live_root)] + list(a),
                                  capture_output=True, text=True)
        git("add", "-A")
        tag = f"deploy-{stamp}"
        r_commit = git("commit", "-m", f"deploy: dev→live {tag}（{copied} files）")
        if r_commit.returncode != 0:
            # OCR 二审 2026-10-03：此时 live 文件已复制但无回滚 tag——如实告知手动回滚法
            print(f"❌ live 提交失败: {(r_commit.stderr or '').strip()[:200]}")
            print(f"⚠️ live 文件已复制但无部署 tag；如需回退: git -C {live_root} checkout HEAD~1 -- scripts agent-hooks")
            return 1
        r_tag = git("tag", tag)
        if r_tag.returncode != 0:
            print(f"❌ live 打标签失败: {(r_tag.stderr or '').strip()[:200]}")
            return 1
        print(f"✅ live 仓库已提交＋标签 {tag}")
        print(f"   回滚: git -C {live_root} checkout {tag}~1")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--deploy-live", type=pathlib.Path, metavar="LIVE_ROOT",
                    help="dev→live 单向部署（PORT_SPEC §5）；需先 git init live 根")
    ap.add_argument("--dry", action="store_true", help="deploy-live 前置漂移检查（不写入）")
    args = ap.parse_args()
    if args.deploy_live:
        sys.exit(deploy_live(args.deploy_live, dry=args.dry))
    else:
        sys.exit(main())
