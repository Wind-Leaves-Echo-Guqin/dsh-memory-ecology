#!/usr/bin/env python3
"""观测舱 v3.2.0（液态玻璃 / iOS 外壳版）双击入口（.pyw → pythonw 运行：无控制台黑框）。

版本号与核心 **3.2.0** 对齐（目录 `gui-v3`，路由 `/v3/`）。

eco_gui.py（8788）承载 v3 前端产物；本入口只负责：
- 8788 服务已在跑 → 直接开 http://127.0.0.1:8788/v3/
- 未在跑 → 分离启动 eco_gui.py --no-browser（仅起服务，不弹旧窗口），待就绪后开 v3 页面
- 打开失败 → ctypes MessageBoxW 弹出原因（pythonw 下没有控制台可看）

相对路径（开源友好）；桌面副本由 install_desktop.py 生成，不在本文件写绝对路径。

历史版本入口 `启动生态观测舱v2.3.pyw` 保留（仍可打开 /v2.3/），
但**这个文件是默认入口**，桌面快捷方式应指向它。
"""
import os
import subprocess
import sys
import time
import traceback
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8788
V3_URL = f"http://127.0.0.1:{PORT}/v3/"


def _alert(text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "观测舱 v3.2.0 启动失败", 0x10)  # MB_ICONERROR
    except Exception:
        pass


def _alive(timeout: float = 1.0) -> bool:
    """8788 上是否已有本观测舱服务（/api/healthz 探针）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/healthz", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _start_service() -> None:
    """分离启动观测舱服务（仅服务，不开旧窗口）；进程随启动器退出而存活。
    PYTHONUTF8=1：Windows GBK 下检索 CLI 子进程输出的中文否则会乱码。"""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([sys.executable, os.path.join(HERE, "eco_gui.py"), "--no-browser"],
                     cwd=HERE, creationflags=flags, env={**os.environ, "PYTHONUTF8": "1"},
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _open(url: str) -> None:
    if not webbrowser.open(url):
        os.startfile(url)  # 无默认浏览器关联时兜底


def main() -> int:
    try:
        if not _alive():
            _start_service()
            for _ in range(40):  # 最多等 ~10s 起服务
                if _alive():
                    break
                time.sleep(0.25)
        _open(V3_URL)
        return 0
    except Exception:
        _alert("打开观测舱 v3.2.0 失败：\n\n" + traceback.format_exc(limit=5)
               + "\n可先双击同目录 生态急救箱.cmd 自检修复。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
