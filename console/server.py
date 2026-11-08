#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配送中心数字孪生 —— 本地控制台服务

只依赖 Python 标准库。启动后浏览器打开 http://127.0.0.1:8765/

职责：
  1. 静态服务 console/static 与项目内的 out/、data/ 产物（含路径穿越防护）
  2. /api/bootstrap  汇总布局 / 任务 / 报告 / 产物清单
  3. /api/paths      把 paths.jsonl 压成前端可直接插值的按车关键帧序列
  4. /api/config     读写 data/config.json（写前自动备份）
  5. /api/run        分级重跑：data（纯 Python）/ full（含 Blender）/ validate
  6. /api/job        轮询增量日志与进度
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = HERE / "static"
DATA = ROOT / "data"
OUT = ROOT / "out"

HOST = os.environ.get("DC_CONSOLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("DC_CONSOLE_PORT", "8765"))

# ---------------------------------------------------------------- 子进程环境

def find_python():
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return str(venv)
    venv2 = ROOT / ".venv" / "bin" / "python"
    if venv2.exists():
        return str(venv2)
    return sys.executable


def find_blender():
    cand = [
        os.environ.get("BLENDER_EXE", ""),
        r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
        shutil.which("blender") or "",
    ]
    for c in cand:
        if c and Path(c).exists():
            return c
    return None


PY = find_python()
BLENDER = find_blender()

PY_STEPS = [
    ("gen_data", ["scripts/gen_data.py"], "生成基础数据（布局/库位/SKU）"),
    ("layout_audit", ["scripts/layout_audit.py"], "布局实体冲突审计"),
    ("nav_grid", ["scripts/nav_grid.py"], "构建 0.5 m 导航栅格"),
    ("optimize_slotting", ["scripts/optimize_slotting.py"], "货位优化"),
    ("gen_tasks", ["scripts/gen_tasks.py"], "事件 → 任务 → AGV 派单"),
    ("plan_paths", ["scripts/plan_paths.py"], "闭环三段 A* 路径规划"),
    ("gen_cargo", ["scripts/gen_cargo.py"], "货物状态机"),
    ("traffic_check", ["scripts/traffic_check.py"], "交通冲突校验（全程在场）"),
    ("check_geometry", ["scripts/check_geometry.py"], "几何穿越校验"),
    ("plot_nav_plan", ["scripts/plot_nav_plan.py"], "绘制 2D 路线图"),
]

BLENDER_STEPS = [
    ("build_dc", ["scripts/build_dc.py"], "构建 Blender 场景"),
    ("animate", ["scripts/animate.py"], "写入 AGV 动画关键帧"),
    ("inspect_blend", ["scripts/inspect_blend.py"], "重开审计 .blend"),
    ("export_glb", ["scripts/export_glb.py"], "导出 GLB 预览"),
    ("render", ["scripts/render.py"], "渲染 10 张图（最慢）"),
]

TAIL_STEPS = [
    ("annotate_heatmap", ["scripts/annotate_heatmap.py"], "热力图叠加图例"),
    ("encode_video", ["scripts/encode_video.py"], "编码 MP4"),
    ("parse_artifacts", ["scripts/parse_artifacts.py"], "解析产物清单"),
    ("test_validator_can_fail", ["tests/test_validator_can_fail.py"], "验证器负测试"),
    ("validate", ["scripts/validate.py"], "A1–A21 硬门槛验收"),
    ("report_status", ["scripts/report_status.py"], "生成 CURRENT_STATUS.md"),
]

SCOPES = {
    "data": PY_STEPS,
    "validate": [TAIL_STEPS[3], TAIL_STEPS[4], TAIL_STEPS[5]],
    "full": PY_STEPS + BLENDER_STEPS + TAIL_STEPS,
}

SCOPE_LABEL = {
    "data": "数据层重跑（纯 Python，约 10 秒）",
    "validate": "仅复验（负测试 + A1–A21 + 状态表）",
    "full": "全链路重跑（含 Blender 建模与渲染，最慢）",
}


# ---------------------------------------------------------------- 作业管理

class Job:
    """一次重跑的运行态。线程安全，支持增量拉取日志。"""

    def __init__(self, scope):
        self.scope = scope
        self.steps = SCOPES[scope]
        self.status = "running"          # running | ok | failed | cancelled
        self.current = None              # 当前步骤 key
        self.current_label = ""
        self.done = 0
        self.total = len(self.steps)
        self.started = time.time()
        self.finished = None
        self.returncode = None
        self.lines = []                  # [{i, step, text, level}]
        self.lock = threading.Lock()
        self.proc = None

    def log(self, text, step="", level="info"):
        with self.lock:
            self.lines.append({
                "i": len(self.lines),
                "step": step,
                "text": text,
                "level": level,
                "t": round(time.time() - self.started, 3),
            })

    def snapshot(self, since=0):
        with self.lock:
            return {
                "scope": self.scope,
                "scope_label": SCOPE_LABEL.get(self.scope, self.scope),
                "status": self.status,
                "current": self.current,
                "current_label": self.current_label,
                "done": self.done,
                "total": self.total,
                "steps": [{"key": k, "label": lb} for k, _a, lb in self.steps],
                "elapsed": round((self.finished or time.time()) - self.started, 1),
                "returncode": self.returncode,
                "lines": [l for l in self.lines if l["i"] >= since],
                "next": len(self.lines),
            }


JOB = None
JOB_LOCK = threading.Lock()


def run_job(scope):
    """在后台线程里顺序执行步骤，遇非零退出码立即中止。"""
    global JOB
    job = JOB
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"

    job.log("启动重跑：" + SCOPE_LABEL.get(scope, scope), level="head")
    if scope in ("data", "full"):
        job.log("Python: %s" % PY)
    if scope == "full":
        if BLENDER:
            job.log("Blender: %s" % BLENDER)
        else:
            job.log("未找到 blender.exe，全链路重跑无法完成场景层。"
                    "请设置环境变量 BLENDER_EXE。", level="error")
            job.status = "failed"
            job.finished = time.time()
            return

    for idx, (key, args, label) in enumerate(job.steps):
        job.current = key
        job.current_label = label
        job.done = idx
        job.log("", level="sep")
        job.log("[%d/%d] %s —— %s" % (idx + 1, job.total, key, label), step=key, level="step")

        if key in [s[0] for s in BLENDER_STEPS]:
            if not BLENDER:
                job.log("跳过：未找到 blender.exe", step=key, level="error")
                job.status = "failed"
                job.finished = time.time()
                return
            cmd = [BLENDER, "-b", "--factory-startup", "-P", args[0]]
        else:
            cmd = [PY] + args

        job.log("$ " + " ".join(cmd), step=key, level="cmd")
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            job.proc = proc
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                if line:
                    job.log(line, step=key)
            proc.wait()
            job.proc = None
            job.returncode = proc.returncode
        except Exception as exc:  # noqa: BLE001
            job.log("执行异常：%r" % (exc,), step=key, level="error")
            job.log(traceback.format_exc(), step=key, level="error")
            job.status = "failed"
            job.finished = time.time()
            return

        if proc.returncode != 0:
            job.log("步骤失败：%s（退出码 %s），已中止后续步骤。" % (key, proc.returncode),
                    step=key, level="error")
            job.status = "failed"
            job.finished = time.time()
            return

    job.done = job.total
    job.current = None
    job.current_label = ""
    job.status = "ok"
    job.finished = time.time()
    job.log("", level="sep")
    job.log("全部 %d 个步骤完成，用时 %.1f s。" % (job.total, job.finished - job.started),
            level="ok")


# ---------------------------------------------------------------- 工具

def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return default


def read_jsonl(path):
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception:
        pass
    return rows


def mtime(path):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(Path(path).stat().st_mtime))
    except Exception:
        return ""


def build_paths_payload():
    """把逐行位姿压成按车的时间序列，前端只做二分 + 插值。"""
    rows = read_jsonl(DATA / "paths.jsonl")
    if not rows:
        return {"agents": [], "t_end": 0, "count": 0}

    by_agent = {}
    for r in rows:
        by_agent.setdefault(r["agent_id"], []).append(r)

    agents = []
    t_end = 0.0
    for aid in sorted(by_agent):
        seq = sorted(by_agent[aid], key=lambda r: r["t"])
        # 同一 t 上出现多次时只保留最后一次（相位切换点），避免插值除零
        dedup = []
        for r in seq:
            if dedup and abs(r["t"] - dedup[-1]["t"]) < 1e-9:
                dedup[-1] = r
            else:
                dedup.append(r)
        t_end = max(t_end, dedup[-1]["t"])
        agents.append({
            "id": aid,
            "task_id": dedup[0].get("task_id"),
            "event_id": dedup[0].get("event_id"),
            "t": [round(r["t"], 4) for r in dedup],
            "x": [round(r["x"], 4) for r in dedup],
            "y": [round(r["y"], 4) for r in dedup],
            "th": [round(r["theta"], 5) for r in dedup],
            "ph": [r["phase"] for r in dedup],
        })
    return {"agents": agents, "t_end": round(t_end, 4), "count": len(rows)}


REPORT_FILES = [
    ("layout_audit", "out/layout_audit.json", "布局审计"),
    ("traffic", "out/traffic_report.json", "交通校验"),
    ("geometry", "out/geometry_report.json", "几何校验"),
    ("route", "out/route_report.json", "路线统计"),
    ("build", "out/build_report.json", "构建报告"),
    ("blend", "out/blend_audit.json", "blend 审计"),
    ("glb", "out/glb_report.json", "GLB 报告"),
    ("render", "out/render_report.json", "渲染报告"),
    ("artifact", "out/artifact_report.json", "产物清单"),
    ("validate", "out/validate_report.json", "验收 A1–A21"),
    ("negative", "out/negative_tests.json", "负测试"),
]


def build_bootstrap():
    layout = read_json(DATA / "layout.json", {}) or {}
    config = read_json(DATA / "config.json", {}) or {}
    tasks = read_json(DATA / "tasks.json", {}) or {}
    cargo = read_json(DATA / "cargo_state.json", {}) or {}
    routes = read_json(DATA / "routes.json", {}) or {}
    metrics = read_json(DATA / "path_metrics.json", {}) or {}
    slotting = read_json(DATA / "slotting.json", {}) or {}

    reports = {}
    for key, rel, label in REPORT_FILES:
        reports[key] = {"label": label, "data": read_json(ROOT / rel)}

    renders = []
    rdir = OUT / "renders"
    if rdir.exists():
        for p in sorted(rdir.glob("*.png")):
            renders.append({"name": p.name, "url": "/out/renders/" + p.name,
                            "size": p.stat().st_size, "mtime": mtime(p)})

    video = None
    mp4 = OUT / "anim_dc.mp4"
    if mp4.exists():
        video = {"url": "/out/anim_dc.mp4", "size": mp4.stat().st_size, "mtime": mtime(mp4)}

    glb = None
    g = OUT / "dc_preview.glb"
    if g.exists():
        glb = {"url": "/out/dc_preview.glb", "size": g.stat().st_size}

    agents_meta = []
    for wh in layout.get("warehouses", []):
        for rb in wh.get("spawn", {}).get("robots", []):
            pos = rb.get("position", [0, 0, 0])
            agents_meta.append({
                "id": rb["id"], "home_id": rb.get("home_id"),
                "warehouse": wh["id"], "floor": rb.get("floor", 1),
                "battery": rb.get("battery"),
                "home": [pos[0], pos[2]],
                "heading": rb.get("heading", 0),
            })

    return {
        "project": config.get("project", "配送中心数字孪生"),
        "config": config,
        "layout": layout,
        "tasks": tasks,
        "cargo": cargo,
        "routes": routes,
        "metrics": metrics,
        "slotting": {
            "heat_by_rack": slotting.get("heat_by_rack", {}),
            "summary": slotting.get("summary", {}),
        },
        "reports": reports,
        "renders": renders,
        "video": video,
        "glb": glb,
        "agents": agents_meta,
        "status_md": read_text(OUT / "CURRENT_STATUS.md"),
        "env": {
            "python": PY,
            "blender": BLENDER or "",
            "root": str(ROOT),
            "has_venv": (ROOT / ".venv").exists(),
        },
        "mtime": {
            "layout": mtime(DATA / "layout.json"),
            "paths": mtime(DATA / "paths.jsonl"),
            "validate": mtime(OUT / "validate_report.json"),
        },
    }


CONFIG_SCHEMA = [
    {"path": "seed", "label": "随机种子", "type": "int", "min": 1, "max": 2 ** 31 - 1,
     "hint": "改变 SKU 周转率与库位分配的合成随机性"},
    {"path": "agv.speed_mps", "label": "AGV 速度 (m/s)", "type": "float", "min": 0.3, "max": 3.0, "step": 0.1,
     "hint": "A7 要求移动段速度 ∈[0.8,1.5]，超出区间验收会 FAIL"},
    {"path": "agv.omega_rad_s", "label": "角速度 (rad/s)", "type": "float", "min": 0.2, "max": 4.0, "step": 0.1},
    {"path": "agv.dwell_pick_s", "label": "取放停靠 (s)", "type": "float", "min": 0.0, "max": 10.0, "step": 0.5},
    {"path": "agv.max_schedule_wait_s", "label": "最大调度等待 (s)", "type": "float", "min": 30.0, "max": 900.0, "step": 10.0},
    {"path": "animation.fps", "label": "动画帧率", "type": "int", "min": 2, "max": 30},
    {"path": "animation.traffic_dt_s", "label": "交通采样步长 (s)", "type": "float", "min": 0.01, "max": 0.5, "step": 0.01},
    {"path": "render.width", "label": "渲染宽度", "type": "int", "min": 320, "max": 3840},
    {"path": "render.height", "label": "渲染高度", "type": "int", "min": 240, "max": 2160},
    {"path": "render.samples", "label": "渲染采样", "type": "int", "min": 1, "max": 128},
    {"path": "render.anim_width", "label": "动画宽度", "type": "int", "min": 320, "max": 1920},
    {"path": "render.anim_height", "label": "动画高度", "type": "int", "min": 240, "max": 1080},
    {"path": "render.anim_samples", "label": "动画采样", "type": "int", "min": 1, "max": 64},
]


def get_nested(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_nested(d, path, value):
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "DCConsole/1.0"

    def log_message(self, fmt, *args):  # 静音默认访问日志
        pass

    # ---- 响应工具
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, msg, code=400):
        self.send_json({"ok": False, "error": msg}, code)

    def send_file(self, path: Path, cache="no-cache"):
        if not path.exists() or not path.is_file():
            self.send_error_json("not found: " + path.name, 404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".mp4": "video/mp4",
            ".glb": "model/gltf-binary",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def safe_path(self, rel):
        """把 URL 路径映射到磁盘，并阻止跳出项目目录。"""
        rel = rel.lstrip("/").replace("\\", "/")
        target = (ROOT / rel).resolve()
        try:
            target.relative_to(ROOT)
        except ValueError:
            return None
        return target

    def static_path(self, rel):
        """静态资源单独映射到 console/static，避免与 out/data 混淆。"""
        rel = rel.lstrip("/").replace("\\", "/")
        target = (STATIC / rel).resolve()
        try:
            target.relative_to(STATIC)
        except ValueError:
            return None
        return target

    # ---- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path in ("/", "/index.html"):
                return self.send_file(STATIC / "index.html")
            if path == "/api/bootstrap":
                return self.send_json(build_bootstrap())
            if path == "/api/paths":
                return self.send_json(build_paths_payload())
            if path == "/api/schema":
                return self.send_json({"fields": CONFIG_SCHEMA,
                                       "scopes": {k: {"label": v, "steps": len(SCOPES[k])}
                                                  for k, v in SCOPE_LABEL.items()}})
            if path == "/api/job":
                since = int((qs.get("since") or ["0"])[0] or 0)
                with JOB_LOCK:
                    job = JOB
                if job is None:
                    return self.send_json({"status": "idle", "lines": [], "next": 0})
                return self.send_json(job.snapshot(since))
            if path == "/api/status":
                return self.send_json(build_bootstrap()["reports"])
            if path.startswith("/static/"):
                target = self.static_path(path[len("/static/"):])
                if target is None:
                    return self.send_error_json("forbidden", 403)
                return self.send_file(target)
            if path.startswith("/out/") or path.startswith("/data/"):
                return self.send_file(self.safe_path(path) or Path("/nonexistent"))
            if path == "/favicon.ico":
                return self.send_json({}, 204)
            return self.send_error_json("unknown route: " + path, 404)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.send_error_json("server error: %r" % (exc,), 500)

    # ---- POST
    def do_POST(self):
        global JOB
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return self.send_error_json("请求体不是合法 JSON", 400)

        try:
            if path == "/api/config":
                return self.handle_config(payload)
            if path == "/api/run":
                return self.handle_run(payload)
            if path == "/api/cancel":
                with JOB_LOCK:
                    job = JOB
                if job and job.proc:
                    try:
                        job.proc.kill()
                    except Exception:
                        pass
                    job.status = "cancelled"
                    job.finished = time.time()
                    job.log("用户取消。", level="error")
                    return self.send_json({"ok": True})
                return self.send_error_json("当前没有运行中的作业", 400)
            return self.send_error_json("unknown route: " + path, 404)
        except Exception as exc:  # noqa: BLE001
            return self.send_error_json("server error: %r" % (exc,), 500)

    # ---- 业务
    def handle_config(self, payload):
        cfg_path = DATA / "config.json"
        cur = read_json(cfg_path)
        if cur is None:
            return self.send_error_json("读不到 data/config.json", 500)

        patch = payload.get("patch") or {}
        applied, rejected = {}, []
        for key, value in patch.items():
            field = next((f for f in CONFIG_SCHEMA if f["path"] == key), None)
            if field is None:
                rejected.append({"path": key, "reason": "不在可编辑白名单内"})
                continue
            try:
                if field["type"] == "int":
                    v = int(round(float(value)))
                else:
                    v = float(value)
            except (TypeError, ValueError):
                rejected.append({"path": key, "reason": "不是数字"})
                continue
            if field.get("min") is not None and v < field["min"]:
                rejected.append({"path": key, "reason": "低于下限 %s" % field["min"]})
                continue
            if field.get("max") is not None and v > field["max"]:
                rejected.append({"path": key, "reason": "高于上限 %s" % field["max"]})
                continue
            set_nested(cur, key, v)
            applied[key] = v

        warnings = []
        speed = get_nested(cur, "agv.speed_mps")
        if speed is not None and not (0.8 <= speed <= 1.5):
            warnings.append("速度 %.2f m/s 超出 A7 的 [0.8, 1.5] 区间，验收会 FAIL" % speed)

        backup = DATA / "config.bak.json"
        try:
            shutil.copyfile(cfg_path, backup)
            with open(cfg_path, "w", encoding="utf-8") as fh:
                json.dump(cur, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        except Exception as exc:  # noqa: BLE001
            return self.send_error_json("写入 config.json 失败：%r" % (exc,), 500)

        return self.send_json({
            "ok": True, "applied": applied, "rejected": rejected,
            "warnings": warnings, "backup": "data/config.bak.json",
            "config": cur,
        })

    def handle_run(self, payload):
        global JOB
        scope = payload.get("scope", "data")
        if scope not in SCOPES:
            return self.send_error_json("未知 scope: %s（可选 data / validate / full）" % scope, 400)

        with JOB_LOCK:
            if JOB is not None and JOB.status == "running":
                return self.send_error_json("已有作业在运行，请等待完成或先取消", 409)
            JOB = Job(scope)
            t = threading.Thread(target=run_job, args=(scope,), daemon=True)
            t.start()

        return self.send_json({
            "ok": True, "scope": scope,
            "total": len(SCOPES[scope]),
            "label": SCOPE_LABEL[scope],
        })


def main():
    print("=" * 66)
    print(" 配送中心数字孪生 —— 本地控制台")
    print("=" * 66)
    print(" 项目根目录 : %s" % ROOT)
    print(" Python     : %s" % PY)
    print(" Blender    : %s" % (BLENDER or "未找到（全链路重跑不可用，可设 BLENDER_EXE）"))
    print(" 服务地址   : http://%s:%d/" % (HOST, PORT))
    print("-" * 66)
    print(" Ctrl+C 停止服务")
    print("=" * 66)

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
