#!/usr/bin/env python3
"""生态捕获骨架（会话记录增量提取，捕获主通道）。

LLM 提取版：扫描 state.db 新增会话的用户消息（限量采样），用 deepseek-v4-flash
提炼「值得长期记住的稳定事实/偏好/纠正」候选，写入 memories/pending/<date>.md
候选区（待确认，不直接进记忆）。

设计：LLM 主通道 + 保守 prompt（宁缺毋滥）+ 硬熔断（条数/输出上限，token 可控）。
用法: python eco_extract.py   （cron 每日 13:00 no_agent）
"""
import datetime
import json
import os
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

from lib.config import hermes_root
from lib import llm as _llm  # P1 单源收编（2026-10-04）：LLM 传输/密钥/解析统一走 lib/llm

HERMES = hermes_root()
DB = HERMES / "state.db"
PENDING_DIR = HERMES / "memories" / "pending"
WATERMARK = PENDING_DIR / ".watermark"
MAX_MSGS = 30          # 单次扫描最多取的用户消息条数（token 熔断）
MAX_OUTPUT = 5         # 单次最多产出的候选条数
MAX_BATCHES = 40       # OCR 2026-10-03: 单轮最多扫描批数（大积压分段消化，防单次 cron 无限烧 API）
PROMPT = """你是记忆提取器。以下是用户今天在 AI 助手会话中发送的消息（已过滤工具输出和系统内容）。
提取值得长期记住的稳定事实、偏好或纠正（如：居住地、习惯、喜欢/讨厌、工作流程、项目信息、明确纠正）。
规则：1) 只提取明确陈述，不推断、不脑补；2) 一次性/临时性内容不提取；3) 每条候选用用户原话片段（≤60字）；4) 宁缺毋滥，没有则输出空数组。
输出 JSON 数组，每项格式：{"type": "陈述|偏好|纠正", "text": "原话片段"}。
只输出 JSON，不要其他文字。
用户消息：
"""


def llm_extract(msgs: list[str]) -> list[dict]:
    """P1 收编：手拼 HTTP + 本地 read_key + 硬编码模型 → lib/llm.complete_json。

    收益：MEMORY_ECOLOGY_API_KEY 环境变量生效；模型/URL/超时/重试策略与门①③同源；
    密钥缺失时报"缺 API key"而非 FileNotFoundError。"""
    items = _llm.complete_json(
        "\n".join(f"- {m}" for m in msgs),
        system=PROMPT, max_tokens=800, temperature=0.1, retries=0, require=list)
    return [i for i in items if isinstance(i, dict) and i.get("text")][:MAX_OUTPUT]


def scan_new_msgs(watermark) -> tuple[list[str], tuple[float, int] | None, dict]:
    """扫一批（LIMIT MAX_MSGS）窗口内消息，返回 (消息列表, 新水位线 (ts, id), 窗口溯源)。
    P1（2026-10-04）：水位线改 (timestamp, id) 复合键——同 timestamp 截断不再跳窗；
    旧标量水位线兼容读取。水位线必须用本批末行（不是全表 MAX），否则未扫描的消息会被跳过。
    v3.0.0a1（审计 P0-3）：第三返回值 = 本批窗口溯源 {session_ids, first_id, last_id, count}——
    候选正文是 LLM 提炼，落库后无法反查会话；session 关联必须在捕获时落 sidecar，过时不候。
    """
    from lib import watermark as _wm
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        wc, wparams = _wm.where_clause(watermark)
        cur.execute(
            "SELECT id, session_id, content, timestamp FROM messages "
            f"WHERE ({wc}) AND role='user' "
            "AND content IS NOT NULL AND length(content) BETWEEN 8 AND 500 "
            "ORDER BY timestamp ASC, id ASC LIMIT ?",
            [*wparams, MAX_MSGS],
        )
        rows = cur.fetchall()
        msgs = [r[2].replace("\n", " ")[:200] for r in rows]
        # v3.0.0a1：session_id 保序去重（一个窗口通常集中在少数会话）；空值跳过
        session_ids: list[str] = []
        for r in rows:
            sid = str(r[1] or "").strip()
            if sid and sid not in session_ids:
                session_ids.append(sid)
        provenance = {
            "session_ids": session_ids,
            "first_id": rows[0][0] if rows else None,
            "last_id": rows[-1][0] if rows else None,
            "count": len(rows),
        }
        # v3.0.0a1：SELECT 变为 (id, session_id, content, timestamp)——水位线索引
        # 相应从 (ts=2, id=0) 改为 (ts=3, id=0)；test_a1_provenance 钉住此口径。
        new_wm = _wm.next_watermark(rows, 3, 0) if rows else None
    finally:
        conn.close()
    return msgs, new_wm, provenance


def _merge_provenance(acc: dict, batch: dict) -> dict:
    """多批扫描的溯源合并：session_ids 保序去重拼接，id 区间取并集范围。"""
    if not acc:
        return {**batch, "session_ids": list(batch.get("session_ids", []))}
    merged = dict(acc)
    merged["session_ids"] = list(merged.get("session_ids", []))
    for sid in batch.get("session_ids", []):
        if sid not in merged["session_ids"]:
            merged["session_ids"].append(sid)
    for k in ("first_id", "last_id"):
        vals = [v for v in (merged.get(k), batch.get(k)) if v is not None]
        if vals:
            merged[k] = min(vals) if k == "first_id" else max(vals)
    merged["count"] = merged.get("count", 0) + batch.get("count", 0)
    return merged


def write_provenance_sidecar(pending_dir: Path, date: str, prov: dict) -> Path | None:
    """v3.0.0a1（审计 P0-3）：把扫描窗口的 session 溯源落到 `<date>.md.sessions.json`。

    为什么是 sidecar 而不是改候选行格式：候选行 `- [type] text` 是 ECO_CONTRACT.md
    v1.0.0 钉死的写入面契约（群/记忆桥按行解析），契约变更需独立走 semver——
    sidecar 文件对契约消费者不可见（write_gate 读取、其他消费者忽略），零契约影响。
    同日多次运行 → 与已有 sidecar 合并（session 求并集，id 区间取范围）。
    """
    if not prov.get("session_ids"):
        return None
    out = pending_dir / f"{date}.md.sessions.json"
    data = {
        "schema": 1,
        "date": date,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "session_ids": list(prov["session_ids"]),
        "first_message_id": prov.get("first_id"),
        "last_message_id": prov.get("last_id"),
        "message_count": prov.get("count", 0),
    }
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
            merged = _merge_provenance({"session_ids": old.get("session_ids", []),
                                        "first_id": old.get("first_message_id"),
                                        "last_id": old.get("last_message_id"),
                                        "count": old.get("message_count", 0)},
                                       prov)
            data["session_ids"] = merged["session_ids"]
            data["first_message_id"] = merged.get("first_id")
            data["last_message_id"] = merged.get("last_id")
            data["message_count"] = merged.get("count", 0)
            data["merged_runs"] = int(old.get("merged_runs", 1)) + 1
        except Exception as e:
            # 旧 sidecar 损坏：备份后覆盖（不阻塞候选主流程）
            out.replace(out.with_name(out.name + f".bad-{int(datetime.datetime.now().timestamp())}"))
            print(f"⚠️ 旧 sidecar 损坏已备份（{e}），重写")
    tmp = out.with_name(out.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out)
    return out


def main() -> int:
    from lib.cli_base import bootstrap as _bootstrap  # v2.5.0：编码兜底
    _bootstrap()
    # v2.5.7（审计 Finding6）：宿主依赖缺失必须给可操作信息，不能抛 sqlite3 裸 traceback。
    # 与 eco_note_signals 同一口径（FileNotFoundError: state.db 不存在: <path>）。
    if not DB.is_file():
        print(f"❌ state.db 不存在: {DB}（Hermes 宿主数据库；请先配置 HERMES_HOME "
              f"或启动宿主，使 {DB} 可用）", file=sys.stderr)
        return 2
    from lib import watermark as _wm
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    wm = WATERMARK.read_text().strip() if WATERMARK.exists() else ""
    try:
        watermark = _wm.parse_watermark(wm)  # 旧标量兼容 → (ts, id)
    except ValueError:
        # 水位线文件损坏 → 备份留查 + 回退默认窗口（与 eco_note 同语义）
        print(f"⚠️ 水位线文件损坏（{wm[:40]!r}），回退默认窗口；损坏文件已备份")
        WATERMARK.replace(WATERMARK.with_name(f".watermark.bad-{int(datetime.datetime.now().timestamp())}"))
        watermark = _wm.parse_watermark(None)
        watermark = (datetime.datetime.now().timestamp() - 86400, -1)

    # 分批扫描：每批 MAX_MSGS 条，水位线逐批推进（本批末行 (ts,id)，非全表 MAX），
    # 直到窗口耗尽或候选达到 MAX_OUTPUT。修复：旧版单批 30 条 + 全表 MAX
    # 推进会把未扫描消息永久吞掉（2026-08-29 实测 6 天零产出的根因）。
    total_items: list[dict] = []
    window_prov: dict = {}   # v3.0.0a1：全轮扫描窗口溯源（跨批合并，随 sidecar 落盘）
    batch_count = 0
    batch_failed = False  # OCR 2026-10-03: 失败批要在收尾可辨识（原先被「无新增候选」日志掩盖）
    while True:
        if batch_count >= MAX_BATCHES:
            print(f"ℹ️ 达单轮批数上限 {MAX_BATCHES}，剩余消息留待下轮（水位线已推进）")
            break
        msgs, new_wm, prov = scan_new_msgs(watermark)
        if not msgs or new_wm is None:
            break
        batch_count += 1
        window_prov = _merge_provenance(window_prov, prov) if window_prov else prov
        print(f"扫描第 {batch_count} 批（{len(msgs)} 条，水位线 {datetime.datetime.fromtimestamp(watermark[0]).isoformat()}）")
        try:
            items = llm_extract(msgs)
        except Exception as e:
            # 失败批不推进水位线，下轮 cron 从本批重扫（安全属性保持；Q9 语义）
            batch_failed = True
            print(f"⚠️ 第 {batch_count} 批 LLM 提取失败: {e}（水位线未推进，下轮重扫）")
            break
        total_items.extend(items)
        watermark = new_wm  # 本批已消费，水位线推进到本批末行（含 id，防同 ts 跳窗）
        if len(total_items) >= MAX_OUTPUT:
            break

    if batch_failed:
        # OCR 2026-10-03: 失败收尾可辨识——打印显式失败摘要 + 非 0 退出码，
        # 且不落盘水位线（值虽未变，但「水位线更新」字样会让 cron 日志误读为正常）
        print(f"❌ 本轮以失败告终（共扫 {batch_count} 批，水位线停 "
              f"{datetime.datetime.fromtimestamp(watermark[0]).isoformat()}），下轮从失败批重扫")
        return 1
    if not total_items:
        print(f"ℹ️ 无新增可提取候选（共扫 {batch_count} 批；水位线 {datetime.datetime.fromtimestamp(watermark[0]).isoformat()}）")
    else:
        date = datetime.date.today().isoformat()
        out = PENDING_DIR / f"{date}.md"
        lines = [f"# 捕获候选 {date}", ""] if not out.exists() else []
        for it in total_items[:MAX_OUTPUT]:
            lines.append(f"- [{it.get('type', '候选')}] {it['text']}")
        with open(out, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"✅ 写入 {len(total_items[:MAX_OUTPUT])} 条候选 → {out}")
        # v3.0.0a1（审计 P0-3）：候选落盘的同时落 session 溯源 sidecar——
        # 门① 写 detail 时回读它填 origin_session_id；sidecar 失败只告警，
        # 不回滚候选（候选丢失不可逆，sidecar 缺失只是可溯源率受损）。
        try:
            sc = write_provenance_sidecar(PENDING_DIR, date, window_prov)
            if sc:
                print(f"✅ 溯源 sidecar → {sc.name}（session {len(window_prov['session_ids'])} 个 / 消息 {window_prov.get('count', 0)} 条）")
        except Exception as e:
            print(f"⚠️ 溯源 sidecar 写入失败（候选不受影响，本轮 origin 将为空）: {e}")

    WATERMARK.write_text(_wm.format_watermark(watermark))
    print(f"水位线更新: {datetime.datetime.fromtimestamp(watermark[0]).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
