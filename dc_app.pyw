#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DC Twin — 单一窗口客户端（把驾驶舱与 Web 控制台合成一个界面）。

设计取舍：
  · **一套界面，不是两套**：桌面 Tk 驾驶舱的页面（总览/布局/场景/KPI/计划书/工具箱）
    与 Web 控制台（2D/3D + 车辆/订单/验收/参数/产物/运营/场景/指标/功能区/计划书）
    合并后只保留 Web 这一套——它已经覆盖全部内容，且 3D 只有浏览器能跑。
  · **原生窗口**：pywebview 用系统 WebView2 开一个无地址栏的窗口，看起来就是一个
    桌面应用，而不是"在浏览器里打开一个网页"。
  · **服务进程内起**：直接在同一个进程里开 ThreadingHTTPServer，退出窗口即释放端口，
    不会留下僵尸进程。
  · **降级不失败**：没有 pywebview / WebView2 时，自动用 Edge/Chrome 的 --app 模式
    （同样无地址栏），再不行才交给默认浏览器。

用法：
  python dc_app.pyw                # 正常启动（无控制台窗口用 pythonw）
  python dc_app.pyw --tk           # 回退到旧的 Tk 驾驶舱
  python dc_app.pyw --port 8770
  python dc_app.pyw --no-window    # 只起服务，方便调试
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 打包后 `__file__` 在临时解包目录里，产物与源码同级于 exe，不在那儿。
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else HERE
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

TITLE = "配送中心数字孪生 · DC Twin"


def _start_server(port: int):
    """在进程内起控制台服务，返回 (httpd, port)。端口占用时顺延。"""
    from http.server import ThreadingHTTPServer
    import console.server as srv

    for p in range(port, port + 8):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), srv.Handler)
        except OSError:
            continue
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, p
    raise SystemExit("8765–8772 全部被占用，请先关掉旧的 console.server")


def _browser_exe() -> str | None:
    """找一个支持 --app 的 Chromium 内核浏览器（窗口无地址栏）。"""
    cands = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/microsoft-edge", "/usr/bin/google-chrome", "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def _menu(window, url_base: str):
    """原生菜单：把"看得全"的入口放在同一层，而不是藏在网页角落。"""

    def js(code):
        try:
            window.evaluate_js(code)
        except Exception:
            pass

    menu = [
        ("视图", [
            ("3D 透视", lambda: js("switchView('3d')")),
            ("2D 俯视", lambda: js("switchView('2d')")),
            ("适应窗口", lambda: js("document.getElementById('btnFit3d').click()")),
            ("—", None),
            ("指标字典", lambda: js("switchTab('kpi')")),
            ("功能区", lambda: js("switchTab('areas')")),
            ("计划书", lambda: js("switchTab('plan')")),
        ]),
        ("操作", [
            ("重跑场景链", lambda: js("switchTab('params')")),
            ("产物目录", lambda: subprocess.Popen(
                ["explorer", str(APP_DIR / "out")] if os.name == "nt"
                else ["open", str(APP_DIR / "out")])),
            ("重新载入", lambda: window.load_url(url_base)),
        ]),
        ("帮助", [
            ("在浏览器中打开", lambda: webbrowser.open(url_base)),
        ]),
    ]
    return menu


def run_window(url: str, width: int, height: int) -> int:
    try:
        import webview  # noqa: F401
    except Exception:
        return run_app_window(url, width, height)

    import webview

    window = webview.create_window(TITLE, url, width=width, height=height,
                                   min_size=(1100, 680), text_select=True)
    try:
        webview.start(menu=_menu(window, url), debug=False)
    except Exception as exc:                      # WebView2 缺失等
        print("pywebview 启动失败：%s；改用 --app 窗口" % exc, file=sys.stderr)
        return run_app_window(url, width, height)
    return 0


def run_app_window(url: str, width: int, height: int) -> int:
    exe = _browser_exe()
    if not exe:
        webbrowser.open(url)
        print("已用默认浏览器打开：%s" % url)
        return 0
    profile = Path(tempfile.gettempdir()) / "dc_twin_webprofile"   # 别在应用目录里拉屎
    cmd = [exe, "--app=%s" % url, "--window-size=%d,%d" % (width, height),
           "--user-data-dir=%s" % profile, "--no-first-run",
           "--no-default-browser-check"]
    print("以应用窗口启动：%s" % url)
    return subprocess.run(cmd).returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=TITLE)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--tk", action="store_true", help="回退到旧的 Tk 驾驶舱")
    ap.add_argument("--no-window", action="store_true", help="只起服务")
    ap.add_argument("--width", type=int, default=1580)
    ap.add_argument("--height", type=int, default=960)
    a = ap.parse_args(argv)

    if a.tk:
        from dc_studio.app import main as tk_main
        return tk_main()

    httpd, port = _start_server(a.port)
    url = "http://127.0.0.1:%d/?view=3d" % port
    print("%s  →  %s" % (TITLE, url))
    if a.no_window:
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            httpd.shutdown()
        return 0

    try:
        return run_window(url, a.width, a.height)
    finally:
        httpd.shutdown()


def _ensure_streams():
    """窗口模式（pythonw / --windowed exe）没有 stdout，`print()` 会直接抛。

    把输出重定向到一个临时日志：既不会因为打印而崩，出问题时还能翻到原因。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None
    log = Path(tempfile.gettempdir()) / "dc_twin_app.log"
    f = open(log, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f
    print("\n--- %s ---" % time.strftime("%Y-%m-%d %H:%M:%S"))
    return f


def _fatal(exc: BaseException) -> None:
    """双击启动失败时必须看得见，否则就是"点了没反应"。"""
    import traceback
    tb = traceback.format_exc()
    print(tb, file=sys.stderr)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            "启动失败：\n\n%s\n\n完整日志：%s"
            % (str(exc)[:600], Path(tempfile.gettempdir()) / "dc_twin_app.log"),
            "DC Twin", 0x10)
    except Exception:                              # 非 Windows 或没有 ctypes
        pass


if __name__ == "__main__":
    _LOG = _ensure_streams()
    try:
        sys.exit(main())
    except BaseException as exc:                   # noqa: BLE001
        _fatal(exc)
        sys.exit(1)
