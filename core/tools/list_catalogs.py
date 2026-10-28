#!/usr/bin/env python3
"""list_catalogs —— 扫最近 N 个会话，摘要列出每个会话实际注入的「记忆目录」。

用途：多圈排查（"从 A 目录起会话该看到什么"）与回归（"目录到底有没有变空"）。

用法：
    python tools/list_catalogs.py           # 最近 6 个
    python tools/list_catalogs.py 20        # 最近 20 个
    EXCLUDE_SESSION=<uuid> python tools/list_catalogs.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from memory_ecology import measure  # noqa: E402

NEEDLE = "记忆目录"


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 6
    exclude = os.environ.get("EXCLUDE_SESSION", "")
    shown = 0
    for p in measure.iter_session_files(limit=limit * 6):
        if exclude and exclude in str(p):
            continue
        found = None
        for line in measure.read_session_lines(p):
            if NEEDLE not in line or '"system/message"' not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            msg = (ev.get("data") or {}).get("message") or {}
            body = "".join(b.get("text", "") for b in (msg.get("content") or [])
                           if isinstance(b, dict))
            idx = body.find(NEEDLE)
            if idx < 0:
                continue
            tail = body[idx:].split("\n")
            items = [ln for ln in tail if ln.startswith("- mem-")]
            found = (tail[0][:70], len(items))
            break
        if found is None:
            print("%-44s <无目录>" % p.parent.name[:44])
        else:
            head, n = found
            print("%-44s items=%-3d %s" % (p.parent.name[:44], n, head))
        shown += 1
        if shown >= limit:
            break
    if shown == 0:
        print("（没有可读的会话日志；缺 zstandard 或还没跑过 dsh）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
