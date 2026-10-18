# -*- coding: utf-8 -*-
"""eco_review.py 自测：语法检查 + fixture dry-run + 实际执行验证。"""
import ast
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from lib.config import hermes_root

SCRIPT = hermes_root() / "scripts" / "eco_review.py"

# ---------- 1. 语法检查 ----------
src = SCRIPT.read_text(encoding='utf-8')
ast.parse(src)
print("[1] AST parse OK; BOM literal count =", src.count('\ufeff'))
# 2026-09-27 随 C 接线更新：BOM/frontmatter 解析已单源到 lib/memstore（转义 \ufeff），
# 本文件不再要求源码含字面 BOM——改为钉「零字面 BOM」（编码卫生，字面 BOM 易被工具破坏）
assert src.count('\ufeff') == 0, 'eco_review 源码不应再含字面 BOM 字符（解析已单源 memstore）'

# ---------- 2. 构造 fixture ----------
tmp = Path(tempfile.mkdtemp(prefix='eco_review_selftest_'))
try:
    mem = tmp / 'memories'
    detail = mem / 'detail'
    quar = mem / 'quarantine'
    arch = mem / 'archive'
    detail.mkdir(parents=True)
    arch.mkdir(parents=True)

    today = date.today()
    # OCR 2026-10-03: quarantine 子目录名由 today 推导——scan_quarantine 优先用目录名
    # YYYY-MM-DD 计算超期，写死 2026-01-01/2026-07-01 的「时间腐化」会随日期推进
    # 把本应 fresh 的目录判成超期（2026-10-03 实测已 94 天 > 90）
    old_date = (today - timedelta(days=150)).isoformat()
    fresh_date = (today - timedelta(days=10)).isoformat()
    (quar / old_date).mkdir(parents=True)
    (quar / fresh_date).mkdir(parents=True)

    def write_detail(name, type_, status, lv, body):
        """2026-09-27（G1 修正）：name 一律无后缀（真实生态里 frontmatter name 由
        write_gate.slug_of 生成、从不带 .md）——文件名由本函数补 .md。
        此前 fixture 把 'act-over-1.md' 整个写进 name:，连锁出「merge 输出带后缀、
        归档目标变 dorm-over.md.md」的假断言失败。"""
        (detail / f"{name}.md").write_text(
            f"---\nname: {name}\ntype: {type_}\nstatus: {status}\n"
            f"last_verified: {lv}\n---\n{body}\n", encoding='utf-8')

    write_detail('act-over-1', 'semantic', 'active',
                 (today - timedelta(days=200)).isoformat(),
                 '训练任务固定使用 .venv312 解释器')
    write_detail('act-over-2', 'episodic', 'active',
                 (today - timedelta(days=100)).isoformat(),
                 '训练任务固定使用 .venv312 解释器')
    write_detail('dorm-over', 'lesson', 'dormant',
                 (today - timedelta(days=240)).isoformat(),
                 '旧课程记忆：Windows glob 需要去重')
    write_detail('fresh', 'semantic', 'active',
                 (today - timedelta(days=5)).isoformat(),
                 '新记忆内容示例')

    old_ts = datetime.combine(today - timedelta(days=150), datetime.min.time()).timestamp()
    fresh_ts = datetime.combine(today - timedelta(days=10), datetime.min.time()).timestamp()
    for name in ('q-old-1.md', 'q-old-2.md'):
        f = quar / old_date / name
        f.write_text(name, encoding='utf-8')
        os.utime(f, (old_ts, old_ts))
    qf = quar / fresh_date / 'q-fresh.md'
    qf.write_text('q-fresh', encoding='utf-8')
    os.utime(qf, (fresh_ts, fresh_ts))

    print('[2] fixture 就绪:', tmp)

    # ---------- 3. dry-run ----------
    r = subprocess.run([sys.executable, str(SCRIPT), '--dry-run', '--detail', str(detail)],
                       capture_output=True, text=True, encoding='utf-8')
    print('[3] ---- dry-run stdout ----')
    print(r.stdout)
    if r.stderr:
        print('[3] ---- dry-run stderr ----')
        print(r.stderr)
    assert r.returncode == 0, f'dry-run exit={r.returncode}'
    out = r.stdout
    assert '[mark_dormant] act-over-1' in out
    assert '[mark_dormant] act-over-2' in out
    assert '[archive] dorm-over' in out
    assert '[merge_candidate]' in out
    assert 'act-over-1 <-> act-over-2' in out
    assert '[quarantine_cleanup]' in out
    assert old_date in out and 'q-old-1.md' in out
    # OCR 2026-10-03: 恒真断言修复——原 `A or B` 在 q-fresh 正常保留时右半恒真、
    # 左半形同虚设，且 split 缺标题时抛 IndexError；直接断言保留文件不出现在清单
    assert 'q-fresh' not in out, '未超期的 q-fresh 不应被清理（不应出现在 dry-run 清单）'
    # dry-run 不得改动任何文件/数据库
    assert (detail / 'act-over-1.md').read_text(encoding='utf-8').count('status: active') == 1
    assert (detail / 'dorm-over.md').exists()
    assert not (arch / 'dorm-over.md').exists()
    assert not (arch / old_date).exists()
    assert not (mem / 'gate_log').exists()
    # OCR 复核 2026-10-03（high）：eco.db 的真实落点是生态根（tmp/eco.db，见下方
    # 第 129-132 行的 G1 修正），原断言查的是 mem/eco.db（memories 下）——位置错误
    # 导致恒真、dry-run 写库根本测不出来。改为断言真实位置。
    assert not (tmp / 'eco.db').exists(), 'dry-run 不得创建 eco.db（生态根）'
    print('[3] dry-run 验证通过：仅打印，未修改/移动任何文件、未写 db/gate_log')

    # ---------- 4. 实际执行 ----------
    r2 = subprocess.run([sys.executable, str(SCRIPT), '--detail', str(detail)],
                        capture_output=True, text=True, encoding='utf-8')
    print('[4] ---- 实际执行 stdout ----')
    print(r2.stdout)
    if r2.stderr:
        print('[4] ---- 实际执行 stderr ----')
        print(r2.stderr)
    assert r2.returncode == 0, f'real-run exit={r2.returncode}'

    # 降级：active→dormant 原地重写，文件仍在 detail
    assert 'status: dormant' in (detail / 'act-over-1.md').read_text(encoding='utf-8')
    assert 'status: dormant' in (detail / 'act-over-2.md').read_text(encoding='utf-8')
    assert (detail / 'act-over-1.md').exists()
    # 归档：dormant 超期 → 移入 archive
    assert not (detail / 'dorm-over.md').exists()
    assert (arch / 'dorm-over.md').exists()
    # quarantine 清理：保留日期子目录
    assert (arch / old_date / 'q-old-1.md').exists()
    assert (arch / old_date / 'q-old-2.md').exists()
    assert (quar / fresh_date / 'q-fresh.md').exists()  # 未超期保留
    assert (quar / old_date / 'q-old-1.md').exists() is False
    # eco.db（2026-09-27 G1 修正）：resolve_paths 的根重定位语义 = --detail 的上两级为
    # 生态根（<root>/memories/detail → root=tmp），eco.db 落生态根——与生产 hermes/eco.db 同构
    dbp = tmp / 'eco.db'
    assert dbp.exists()
    conn = sqlite3.connect(str(dbp))
    rows = conn.execute('SELECT action, COUNT(*) FROM review_log GROUP BY action').fetchall()
    conn.close()
    print('[4] review_log 分组:', dict(rows))
    counts = dict(rows)
    assert counts.get('mark_dormant') == 2
    assert counts.get('archive') == 1
    assert counts.get('merge_candidate') == 1
    assert counts.get('quarantine_cleanup') == 2
    # gate_log 清单
    gl = mem / 'gate_log' / f'review-{today.isoformat()}.md'
    assert gl.exists()
    gtext = gl.read_text(encoding='utf-8')
    for key in ('## 一、过期复核清单', '## 二、碎片合并候选', '## 三、quarantine 清理清单',
                'act-over-1', 'dorm-over', 'q-old-1'):
        assert key in gtext, f'gate_log 缺少: {key}'
    print('[4] 实际执行验证通过：降级/归档/清理/DB/gate_log 均符合预期')

    print()
    print('ALL SELF-TESTS PASSED')
finally:
    # OCR 2026-10-03: 清理挪进 finally——中间断言失败不再泄漏 eco_review_selftest_* 临时目录
    shutil.rmtree(tmp, ignore_errors=True)
