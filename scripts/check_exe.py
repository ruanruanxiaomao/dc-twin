#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包产物自检：起服务 → 逐个接口 → 关掉。

为什么需要它：`--windowed` 的 exe **没有 stdout**，界面里点一圈也只能证明"我点了"。
唯一可靠的证据是"服务起来了，并且每个接口都返回了预期结构"。所以这里用
`--no-window` 把服务单独拉起来，像用户界面那样逐个打接口，最后核对根目录是不是
exe 同级（而不是临时解包目录）。

用法：
    python scripts/check_exe.py                 # 自查 dist/DC Twin
    python scripts/check_exe.py --app "dist/DC Twin" --shot out/exe_3d.png
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
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def free_port(start: int = 8791) -> int:
    for p in range(start, start + 40):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit("找不到空闲端口")


def get(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def post(url: str, payload: dict, timeout: float = 30.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


#: (路径, 说明, 期望的最小体量 / 关键字段)
GETS = [
    ("/", "首页（10 个页签的壳）", "html"),
    ("/api/bootstrap", "汇总：布局/任务/报告/产物", "mvp"),
    ("/api/paths", "AGV 路径（3D 回放用）", "agents"),
    ("/api/kpi", "指标字典：tick 39 + 解析 25", "tick"),
    ("/api/areas", "21 个功能区", "areas"),
    ("/api/plan.html", "计划书（全屏阅读层）", "plan"),
    ("/api/schema", "配置表单 schema", None),
    ("/api/status", "产物状态（报告清单）", None),
    ("/api/scenarios", "场景清单", None),
    ("/api/scenario/template", "新建场景模板", None),
    ("/api/pnl", "损益表", None),
    ("/api/ledger", "账本", None),
    ("/api/cost-spec", "成本口径", None),
    ("/out/dc_preview.glb", "3D 模型（three.js 加载）", "glb"),
    ("/data/layout.json", "布局（2D 沙盘）", "json"),
]


def describe(key: str | None, body: bytes) -> tuple[bool, str]:
    if key is None:
        return (len(body) > 2, "%d B" % len(body))
    if key == "html":
        ok = b"data-tab=\"plan\"" in body or b"pane-plan" in body
        return (ok, "含计划书面板" if ok else "缺少页签结构")
    if key == "plan":
        ok = len(body) > 50_000 and b"<h2>" in body
        return (ok, "%.0f KB / %d 个 h2" % (len(body) / 1024, body.count(b"<h2>")))
    if key == "glb":
        ok = body[:4] == b"glTF"
        return (ok, "%.0f KB" % (len(body) / 1024))
    if key == "json":
        try:
            json.loads(body)
            return True, "%d B" % len(body)
        except Exception:
            return False, "不是 JSON"
    try:
        d = json.loads(body)
    except Exception:
        return False, "不是 JSON"
    if key == "mvp":
        need = {"layout", "tasks", "reports", "renders", "status_md"}
        miss = need - set(d)
        return (not miss, "缺 %s" % ",".join(sorted(miss)) if miss else "字段齐全")
    if key == "agents":
        n = len(d.get("agents") or [])
        return (n > 0, "%d 台 AGV" % n)
    if key == "tick":
        nt, na = len(d.get("tick") or []), len(d.get("analytic") or [])
        return (nt >= 30 and na >= 20, "tick %d / 解析 %d" % (nt, na))
    if key == "areas":
        n = len(d.get("areas") or [])
        return (n >= 20, "%d 个功能区" % n)
    if key == "env":
        env = d.get("env") or {}
        return (bool(env.get("root")), "root=%s" % env.get("root"))
    return (True, "OK")


def shot(url: str, out: Path) -> str:
    """用 Edge 无头模式截图，证明 3D 真的渲染出来了（而不是接口通了但页面白屏）。"""
    cands = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
    exe = next((c for c in cands if os.path.exists(c)), None)
    if not exe:
        return "跳过（这台机器没有 Edge/Chrome）"
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()                      # 旧图会假装"成功"，先删掉再截
    for attempt in (1, 2):
        # 必须给独立的 user-data-dir：否则浏览器发现已有实例，会把参数转发过去、
        # 自己立刻退出，截图文件自然不出现（第一次踩到时以为是页面报错）。
        profile = Path(tempfile.mkdtemp(prefix="dc_shot_"))
        r = subprocess.run([exe, "--headless=new", "--disable-gpu",
                            "--use-gl=swiftshader", "--user-data-dir=%s" % profile,
                            "--no-first-run", "--window-size=1500,860",
                            "--virtual-time-budget=25000",
                            "--screenshot=%s" % out, url],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=180)
        shutil.rmtree(profile, ignore_errors=True)
        if out.exists() and out.stat().st_size > 1024:
            return "%s（%.0f KB）" % (out.name, out.stat().st_size / 1024)
        print("        | 截图第 %d 次失败（rc=%s）" % (attempt, r.returncode))
    return "截图失败"


def ops_tests(base: str, app: Path) -> list[str]:
    """真正"操作"一遍：重跑数据链、另存场景、删掉场景。

    只读接口全通，不代表能改东西——写盘路径、子进程解释器、相对目录，都只有真正
    跑一次才会暴露（比如打包后 `sys.executable` 指向 exe 自己，拿去执行脚本会再开
    一个窗口，接口照样返回 200）。
    """
    bad = []

    # 1) 数据链重跑（10 步纯 Python，约 10–20 s）
    try:
        post(base + "/api/run", {"scope": "data"})
        t0 = time.time()
        st = "running"
        while time.time() - t0 < 420:
            time.sleep(1.0)
            d = json.loads(get(base + "/api/job")[1])
            st = d.get("status")
            if st != "running":
                break
        ok = st == "ok"
        print("  %-26s %-28s %s %s（%.0f s）" % ("POST /api/run", "数据链重跑 10 步",
                                                 "OK  " if ok else "FAIL", st,
                                                 time.time() - t0))
        if not ok:
            lines = (d.get("lines") or [])[-6:]
            for ln in lines:
                print("        | %s" % str(ln.get("text", ln))[:110])
            bad.append("run:data")
    except Exception as e:                            # noqa: BLE001
        print("  %-26s %-28s FAIL %r" % ("POST /api/run", "数据链重跑", e))
        bad.append("run:data")

    # 2) 场景另存 → 再删除（验证写配置目录、校验、清理都通）
    try:
        src = app / "config" / "scenarios" / "baseline.json"
        scen = json.loads(src.read_text(encoding="utf-8"))
        scen["id"] = "zz_selftest"
        scen["name"] = "自检临时场景"
        r = json.loads(post(base + "/api/scenario/save", {"scenario": scen})[1])
        ok = bool(r.get("ok"))
        print("  %-26s %-28s %s %s" % ("POST /api/scenario/save", "另存场景",
                                       "OK  " if ok else "FAIL",
                                       r.get("path") or (r.get("issues") or [""])[0]))
        if not ok:
            bad.append("scenario:save")
        r2 = json.loads(post(base + "/api/scenario/delete", {"id": "zz_selftest"})[1])
        ok2 = bool(r2.get("ok"))
        print("  %-26s %-28s %s %s" % ("POST /api/scenario/delete", "删除临时场景",
                                       "OK  " if ok2 else "FAIL", r2.get("deleted", "")))
        if not ok2:
            bad.append("scenario:delete")
    except Exception as e:                            # noqa: BLE001
        print("  %-26s %-28s FAIL %r" % ("scenario save/delete", "场景写删", e))
        bad.append("scenario")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="打包产物自检")
    ap.add_argument("--app", default=str(ROOT / "dist" / "DC Twin"), help="打包目录")
    ap.add_argument("--shot", default="", help="截图保存路径（验证 3D 渲染）")
    ap.add_argument("--keep", action="store_true", help="自检后不关服务（调试用）")
    ap.add_argument("--ops", action="store_true",
                    help="连写操作一起验证：重跑数据链、另存/删除场景（约 30 s）")
    a = ap.parse_args(argv)

    app = Path(a.app).resolve()
    exe = app / ("DC Twin.exe" if os.name == "nt" else "DC Twin")
    if not exe.exists():
        print("找不到 exe：%s" % exe, file=sys.stderr)
        return 2

    port = free_port()
    env = dict(os.environ, DC_ROOT=str(app))     # 显式指定，防止误读开发目录
    proc = subprocess.Popen([str(exe), "--no-window", "--port", str(port)],
                            cwd=str(app), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    fails = []
    try:
        t0 = time.time()
        up = False
        while time.time() - t0 < 45:
            try:
                get(base + "/api/status", timeout=3)
                up = True
                break
            except Exception:
                if proc.poll() is not None:
                    print("exe 启动即退出，退出码 %s" % proc.returncode, file=sys.stderr)
                    return 3
                time.sleep(0.4)
        if not up:
            print("45 s 内服务没起来", file=sys.stderr)
            return 4
        print("服务已就绪：%s（启动 %.1f s）\n" % (base, time.time() - t0))

        for path, label, key in GETS:
            try:
                st, body = get(base + path)
                ok, note = describe(key, body)
                if st != 200:
                    ok, note = False, "HTTP %d" % st
            except urllib.error.HTTPError as e:
                ok, note = False, "HTTP %d" % e.code
            except Exception as e:                    # noqa: BLE001
                ok, note = False, repr(e)[:60]
            print("  %-26s %-28s %s %s" % (path, label, "OK  " if ok else "FAIL", note))
            if not ok:
                fails.append(path)

        # 写接口：只做无副作用的（校验/试跑），不去改 config，免得自检污染数据
        tmpl = get(base + "/api/scenario/template")[1]
        scenario = json.loads(tmpl)
        for path, label in (("/api/scenario/validate", "场景校验"),
                            ("/api/scenario/dry-run", "场景试跑")):
            try:
                st, body = post(base + path, {"scenario": scenario})
                d = json.loads(body)
                ok = st == 200 and ("issues" in d or "ok" in d)
                note = "issues=%d" % len(d.get("issues") or []) if "issues" in d else "OK"
            except Exception as e:                    # noqa: BLE001
                ok, note = False, repr(e)[:60]
            print("  %-26s %-28s %s %s" % (path, label, "OK  " if ok else "FAIL", note))
            if not ok:
                fails.append(path)

        # 根目录必须是 exe 同级（否则 out/ 会被写到临时解包目录，用户看不到）
        # 注意：/api/status 只是 reports 子集，env 在 /api/bootstrap 里
        st, body = get(base + "/api/bootstrap")
        envd = json.loads(body).get("env", {})
        root = Path(envd.get("root", ""))
        same = root.resolve() == app
        print("  %-26s %-28s %s %s" % ("(root)", "数据根目录 == exe 同级",
                                       "OK  " if same else "FAIL", root))
        if not same:
            fails.append("root")
        print("  %-26s %-28s    python=%s" % ("(env)", "重跑用解释器",
                                              envd.get("python") or "无（只能看，不能重跑）"))

        if a.ops:
            fails.extend(ops_tests(base, app))

        if a.shot:
            note = shot(base + "/?view=3d", Path(a.shot))
            print("  %-26s %-28s    %s" % ("(3D)", "无头截图", note))
    finally:
        if not a.keep:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()

    print()
    if fails:
        print("EXE_CHECK FAILED：%d 项 —— %s" % (len(fails), ", ".join(fails)))
        return 1
    print("EXE_OK  全部接口通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
