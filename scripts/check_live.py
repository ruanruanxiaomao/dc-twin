#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_live.py —— LIVE_OK：用真浏览器点一遍直播模式（P2 的验收门）。

为什么要真浏览器：`test_frontend_wiring.py` 只能证明"源码里出现了该有的字符串"。
它证明不了"点了开局按钮 HUD 会动"、也证明不了"点暂停时钟真的停"。
本机没有 tabbit / Playwright，所以这里用 Edge/Chrome 的 `--remote-debugging-port`
+ `scripts/live_probe.mjs`（Node 22 自带 WebSocket，零第三方依赖）驱动点击流。

覆盖（方案书 §8.3 的前端边界用例）：
  开局 → HUD 数字 → 时钟在走 → 暂停停住 → 恢复推进 → 2× 倍速到服务端
  → 存档写槽 → 读档回暂停 → 连续买车打到现金转负（破产边界）

用法：
    python scripts/check_live.py                # 起 dev 服务 + 无头浏览器
    python scripts/check_live.py --serve-only   # 只验证服务起得来
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def free_port(start=8901) -> int:
    for p in range(start, start + 60):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit("找不到空闲端口")


def find_node():
    exe = shutil.which("node")
    return exe


def wait_http(url, timeout=60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:                            # noqa: BLE001
            time.sleep(0.3)
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="浏览器实测直播模式（LIVE_OK）")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--serve-only", action="store_true")
    ap.add_argument("--keep", action="store_true", help="跑完不关服务（调试）")
    a = ap.parse_args(argv)

    browser = next((b for b in BROWSERS if os.path.exists(b)), None)
    if not browser:
        print("跳过：没找到 Edge/Chrome", file=sys.stderr)
        return 2
    node = find_node()
    if not node:
        print("跳过：没找到 node（live_probe.mjs 需要 Node 22+）", file=sys.stderr)
        return 2

    port = a.port or free_port()
    cdp = free_port(port + 1)
    env = dict(os.environ, DC_CONSOLE_PORT=str(port))
    srv = subprocess.Popen([sys.executable, "-m", "console.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    profile = Path(tempfile.mkdtemp(prefix="dc_live_"))
    proc = None
    rc = 0
    try:
        if not wait_http(base + "/api/status"):
            print("服务 60 s 内没起来", file=sys.stderr)
            return 4
        print("服务已就绪：%s\n" % base)
        if a.serve_only:
            print("LIVE_SERVE_OK（只验证服务）")
            return 0

        proc = subprocess.Popen([browser, "--headless=old", "--disable-gpu",
                                 "--remote-debugging-port=%d" % cdp,
                                 "--user-data-dir=%s" % profile, "--no-first-run",
                                 "--no-default-browser-check", "--window-size=1440,900",
                                 "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 等 CDP 端点可用
        ready = False
        t0 = time.time()
        while time.time() - t0 < 40:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % cdp, timeout=2):
                    ready = True
                    break
            except Exception:                        # noqa: BLE001
                time.sleep(0.3)
        if not ready:
            print("浏览器调试端口没起来（%d）" % cdp, file=sys.stderr)
            return 5

        print("浏览器已就绪，开始点击流：")
        out = subprocess.run([node, str(ROOT / "scripts" / "live_probe.mjs"), base, str(cdp)],
                             cwd=str(ROOT), capture_output=True, text=True, timeout=300)
        print(out.stdout.rstrip())
        if out.returncode != 0:
            if out.stderr:
                print(out.stderr.rstrip(), file=sys.stderr)
            rc = 1
        else:
            print("\nLIVE_OK  浏览器点击流全部通过")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(profile, ignore_errors=True)
        if not a.keep:
            srv.terminate()
            try:
                srv.wait(timeout=8)
            except subprocess.TimeoutExpired:
                srv.kill()
    return rc


if __name__ == "__main__":
    sys.exit(main())
