"""serve_webapp.py — 把静态 3D 网页版起在本地，并打开浏览器。

两种用途：
  1. 本地预览 `docs/app/`（GitHub Pages 上跑的是同一份文件，所见即所得）
  2. 驾驶舱的"3D 网页版"入口在**没有启动完整控制台**时的降级方案

它只依赖标准库：`http.server` + 一个线程。默认 127.0.0.1:8899，不监听 0.0.0.0。

用法：
  python scripts/serve_webapp.py                # 构建（若缺失）+ 起服务 + 开浏览器
  python scripts/serve_webapp.py --no-open      # 只起服务
  python scripts/serve_webapp.py --port 9000 --rebuild
"""
from __future__ import annotations

import argparse
import functools
import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
APP = DOCS / "app"


def is_up(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.25)
        return s.connect_ex((host, port)) == 0


def ensure_built(rebuild: bool = False) -> None:
    if rebuild or not (APP / "index.html").exists():
        sys.path.insert(0, str(ROOT / "scripts"))
        import build_webapp
        m = build_webapp.build(APP)
        print("WEBAPP_BUILT files=%d bytes=%.1fMB"
              % (m["counts"]["files"], m["counts"]["bytes"] / 1048576))


class QuietHandler(SimpleHTTPRequestHandler):
    """默认 handler 每条请求都往 stderr 打一行，驾驶舱里没必要。"""

    def log_message(self, fmt, *args):  # noqa: A003 (stdlib signature)
        pass


def serve(port: int = 8899) -> ThreadingHTTPServer:
    handler = functools.partial(QuietHandler, directory=str(DOCS))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def url_for(port: int, view_3d: bool = True) -> str:
    return "http://127.0.0.1:%d/app/index.html%s" % (port, "?view=3d" if view_3d else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="静态 3D 网页版本地服务")
    ap.add_argument("--port", type=int, default=int(os.environ.get("DC_WEBAPP_PORT", "8899")))
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args(argv)

    ensure_built(a.rebuild)
    if is_up(a.port):
        print("WEBAPP_ALREADY_RUNNING " + url_for(a.port))
        if not a.no_open:
            webbrowser.open(url_for(a.port))
        return 0

    httpd = serve(a.port)
    url = url_for(a.port)
    print("WEBAPP_SERVING %s (Ctrl+C 停止)" % url)
    if not a.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
