"""integrity — O1 live 树完整性自检（设计稿 v0.1-20261003）。

清单 = 生成物（publish_compat.py --deploy-live 部署成功后生成 _integrity.json），
体检（eco_health_check §10）只读比对：

  files:   部署集 sha256（EOL 归一，与 deploy 漂移检查同口径）
           —— 手改 live / 拷贝错位 → 文件漂移 🔴
  anchors: Hermes 本体补丁锚点（agent-hooks/README.md「已知事项」记载的两个
           本地补丁，此前只能人肉 grep 核对）——Hermes 升级冲掉补丁 → 🔴 指名

缺清单 = 提示不扣分（旧部署 / 开发树）；锚点缺失或文件漂移 = 🔴（体检扣分）。
锚点用「精确行片段」而非文件 hash：Hermes 升级会合法地改其它行，只有补丁
本身被冲掉才该报警。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path

from . import fs as _fs

_CRLF = b"\r\n"
_LF = b"\n"
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# Hermes 本体补丁锚点（升级后若被冲掉，体检 🔴 指名；施补方法见 agent-hooks/README.md）
ANCHORS = [
    {
        "file": "hermes-agent/agent/shell_hooks.py",
        "snippet": '"session_id": kwargs.get("session_id") or kwargs.get("parent_session_id") or ""',
        "note": "pre_llm_call payload 透传 parent_session_id",
    },
    {
        "file": "hermes-agent/hermes_cli/main.py",
        "snippet": '_AGENT_COMMANDS = {None, "chat", "acp", "rl", "serve"}',
        "note": "CLI 顶层命令集加入 serve",
    },
]


def _norm_bytes(p: Path) -> bytes:
    """读文件并归一 LF（与 deploy_live._norm_bytes 同口径）。

    读失败抛 OSError 由调用方定语义——绝不静默降级为 b""（会把「读不到」
    冒充「空文件」，产生假漂移/假锚点缺失，OCR 2026-10-03 修复）。"""
    return p.read_bytes().replace(_CRLF, _LF)


def _sha(p: Path) -> str:
    return hashlib.sha256(_norm_bytes(p)).hexdigest()


def write_manifest(live_root: Path, rels: list[str], tag: str = "") -> Path:
    """部署成功后生成 _integrity.json。rels = 部署集的 live 侧相对路径。

    以 live 实际落盘文件为准计算 sha（复制后校验，顺带发现复制损坏）。
    部署集文件缺失/不可读 → 抛异常（部署失败信号，绝不写空 sha 留盲区）。"""
    files = {}
    for rel in rels:
        p = live_root / rel
        if not p.is_file():
            raise RuntimeError(f"部署集文件缺失，拒绝生成清单: {rel}")
        files[rel] = _sha(p)
    manifest = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "tag": tag,
        "schema_version": 1,
        "files": files,
        "anchors": ANCHORS,
    }
    out = live_root / "_integrity.json"
    # 原子写（OCR 二审 2026-10-03：中断不留半截清单被误当损坏）
    _fs.atomic_write(out, json.dumps(manifest, ensure_ascii=False, indent=2))
    return out


def verify_manifest(live_root: Path) -> dict:
    """只读校验（体检用）。

    - 清单缺失 → {"present": False}（旧部署/开发树，体检不扣分）
    - 清单损坏/类型非法 → {"present": False, "corrupt": True}（体检 🔴，
      与「缺清单」可区分——调用方须先查 corrupt）
    - 清单存在但读失败 → {"present": False, "unreadable": True}（I/O 异常
      不冒充损坏，OCR 二审 2026-10-03）
    - 文件读失败按「漂移」计（无法证实完整即不完整）；锚点文件读失败
      按缺失计；sha 非法（非 64 位 hex）同样按漂移计"""
    p = live_root / "_integrity.json"
    if not p.is_file():
        return {"present": False}
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"present": False, "unreadable": True}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("manifest 非对象")
        files = data.get("files")
        anchors = data.get("anchors")
        if not isinstance(files, dict) or not isinstance(anchors, list):
            raise ValueError("files/anchors 类型非法")
    except Exception:
        return {"present": False, "corrupt": True}
    missing_files = []
    for rel, sha in files.items():
        f = live_root / str(rel)
        if not isinstance(sha, str) or not _HEX_RE.match(sha):
            missing_files.append(str(rel))  # 空/非法 sha（盲区）→ 如实报为漂移
            continue
        try:
            if _sha(f) != sha:
                missing_files.append(str(rel))
        except OSError:
            missing_files.append(str(rel))
    anchor_misses = []
    for a in anchors:
        if not isinstance(a, dict):
            anchor_misses.append({"file": "?", "note": "锚点条目非法"})
            continue
        f = live_root / str(a.get("file", ""))
        snippet = str(a.get("snippet", "")).replace("\r\n", "\n")
        try:
            ok = f.is_file() and snippet.encode("utf-8") in _norm_bytes(f)
        except OSError:
            ok = False
        if not ok:
            anchor_misses.append({"file": str(a.get("file", "?")),
                                  "note": a.get("note", "")})
    return {"present": True, "file_count": len(files),
            "anchor_count": len(anchors),
            "missing_files": missing_files, "anchor_misses": anchor_misses}
