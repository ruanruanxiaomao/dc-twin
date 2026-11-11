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

# 打包（PyInstaller）后 `__file__` 落在临时解包目录里，不能用来定位产物：
# 代码在 exe 的归档里，但 data/out/runs/scripts 必须留在 **exe 同级目录**，
# 因为重跑时要写盘，而且用户要能直接看到 out/ 里的模型与渲染图。
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    ROOT = Path(os.environ.get("DC_ROOT") or Path(sys.executable).resolve().parent)
    STATIC = ROOT / "console" / "static"
else:
    ROOT = HERE.parent
    STATIC = HERE / "static"
DATA = ROOT / "data"
OUT = ROOT / "out"
sys.path.insert(0, str(ROOT / "scripts"))
import scenario_tools  # noqa: E402  (C1: user-defined scenario tools)

HOST = os.environ.get("DC_CONSOLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("DC_CONSOLE_PORT", "8765"))

# ---------------------------------------------------------------- 子进程环境

def find_python():
    """找一个能跑 scripts/*.py 的解释器。

    打包版里 `sys.executable` 是 **这个 GUI 应用自己**，拿它去执行脚本只会再开
    一个窗口（甚至因为参数不认识直接退出），所以必须另找真实解释器；找不到就
    返回空串，让重跑报出可读的原因，而不是抛一堆看不懂的 traceback。
    """
    env = os.environ.get("DC_PYTHON", "").strip()
    if env and Path(env).exists():
        return env
    for venv in (ROOT / ".venv" / "Scripts" / "python.exe",
                 ROOT / ".venv" / "bin" / "python"):
        if venv.exists():
            return str(venv)
    if not FROZEN:
        return sys.executable
    for name in ("python.exe", "python3.exe", "python", "python3"):
        found = shutil.which(name)
        if found and "WindowsApps" not in found:      # 商店别名会弹应用商店
            return found
    return ""


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
                "lines": [ln for ln in self.lines if ln["i"] >= since],
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
    if scope in ("data", "full") and not PY:
        job.log("未找到可用的 Python 解释器，无法执行 scripts/*.py。\n"
                "打包版需要一个本机 Python：把 DC_PYTHON 设为 python.exe 的完整路径，"
                "或让 python 在 PATH 里，然后重开应用。", level="error")
        job.status = "failed"
        job.finished = time.time()
        return
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


def _latest_sim_kpi():
    """取最近一次 tick 仿真的 KPI（runs/<scenario>/<ts>/sim_kpi.json）。"""
    runs = sorted((ROOT / "runs").glob("*/*/sim_kpi.json"))
    if not runs:
        return None, []
    p = runs[-1]
    try:
        metrics = read_json(p, {}).get("metrics", {})
    except Exception:
        return None, []
    rows = []
    for k, v in metrics.items():
        if isinstance(v, dict):
            rows.append({"key": k, **{kk: v.get(kk) for kk in
                                      ("value", "unit", "definition", "formula", "source")}})
        else:
            rows.append({"key": k, "value": v})
    return str(p.parent.relative_to(ROOT)), rows


def build_kpi_payload():
    """两套指标并存：解析层 M/M/c（out/kpi.json）+ tick 层实测（sim_kpi.json）。"""
    a_runs = sorted((ROOT / "runs").glob("baseline/*/kpi.json"))
    analytic = read_json(a_runs[-1], {}) if a_runs else {}
    a_metrics = analytic.get("metrics", {})
    a_rows = [{"key": k, **{kk: v.get(kk) for kk in
                            ("value", "unit", "definition", "formula", "source")}}
              if isinstance(v, dict) else {"key": k, "value": v}
              for k, v in a_metrics.items()]
    run, t_rows = _latest_sim_kpi()
    return {"analytic": a_rows, "tick": t_rows, "tick_run": run,
            "model": analytic.get("model")}


def _anchor_index():
    """锚点 id → {kind, xy, label}。

    18 个功能区是"逻辑区"（没有 rect，只挂靠货架/月台/工位/充电桩）。前端要在
    2D/3D 里把它们画出来，就必须知道锚点在哪——以前只给了 anchor_resolved 的
    类型（"ZONE"/"RACK"），没有坐标，所以界面上根本看不见。
    """
    lay = read_json(DATA / "layout.json", {}) or {}
    idx = {}
    for wh in lay.get("warehouses", []):
        for z in wh.get("zones", []):
            poly = z.get("polygon") or []
            if not poly:
                continue
            idx[z["id"]] = {"kind": "ZONE", "wh": wh["id"], "label": z.get("name", z["id"]),
                            "xy": [sum(p[0] for p in poly) / len(poly),
                                   sum(p[1] for p in poly) / len(poly)]}
        for r in wh.get("racks", []):
            pos = r.get("position") or [0, 0, 0]
            idx[r["id"]] = {"kind": "RACK", "wh": wh["id"], "label": r.get("zone", ""),
                            "xy": [pos[0], pos[2]]}
        for d in wh.get("docks", []):
            rect = d.get("rect") or [0, 0, 0, 0]
            idx[d["id"]] = {"kind": "DOCK", "wh": wh["id"], "label": d.get("kind", ""),
                            "xy": [(rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2]}
        for s in wh.get("stations", []):
            rect = s.get("rect") or [0, 0, 0, 0]
            idx[s["id"]] = {"kind": "STATION", "wh": wh["id"], "label": s.get("kind", ""),
                            "xy": [(rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2]}
        for c in wh.get("charging_stations", []):
            pos = c.get("position") or [0, 0, 0]
            idx[c["id"]] = {"kind": "CHARGER", "wh": wh["id"], "label": "充电",
                            "xy": [pos[0], pos[2]]}
    return idx


def _area_xy(a, idx):
    """给每个功能区算出可绘制的坐标：物理区取矩形中心，逻辑区取锚点。"""
    rect = a.get("rect")
    if rect:
        return [(rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2], "RECT"
    hit = idx.get(a.get("anchor") or "")
    if hit:
        return hit["xy"], hit["kind"]
    return None, "UNKNOWN"


def build_areas_payload():
    d = read_json(DATA / "areas.json", {}) or {}
    idx = _anchor_index()
    areas = []
    for a in d.get("areas", []):
        xy, how = _area_xy(a, idx)
        areas.append(dict(a, xy=xy, anchor_how=how,
                          anchor_xy=(idx.get(a["anchor"]) or {}).get("xy")
                          if a.get("anchor") else None))
    return {"summary": d.get("summary", {}), "areas": areas}


def _variant_engine():
    """把 build_variant.py 当模块加载（只读常量，无副作用），复用它的排布算法。

    为什么复用而不是重写：`pack()` 决定"选中的区能不能塞进 W 仓北侧"，界面上
    预览的深度必须和真正生成变体时一致，否则预览就成了猜。
    """
    import importlib.util
    path = ROOT / "scripts" / "build_variant.py"
    spec = importlib.util.spec_from_file_location("dc_variant_runtime", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_areas_catalog():
    eng = _variant_engine()
    catalog, _raw = eng.load_catalog()
    base = eng.jload(eng.BASE_SPEC)
    wh = next((w for w in base["warehouses"] if w["id"] == "W"), None)
    sel = eng.jload(eng.SELECTION) if Path(eng.SELECTION).exists() else {}
    others = [float(w["origin"][1]) for w in base["warehouses"] if w["id"] != "W"]
    tpls = []
    for key, t in sorted(catalog.items(), key=lambda kv: -kv[1]["priority"]):
        proc = t.get("process") or {}
        slots = proc.get("parallel_slots") or 1
        mean = (proc.get("service_time_s") or {}).get("mean") or 0
        tpls.append({
            "template": key, "kind": t.get("kind"), "name": t.get("name"),
            "group": t.get("group", ""), "priority": t.get("priority"),
            "size": t.get("size"), "capacity": t.get("capacity"),
            "slots": slots, "service_mean_s": mean,
            "throughput_per_h": round(slots * 3600 / mean, 1) if mean else None,
        })
    return {
        "templates": tpls,
        "presets": dict(eng.PRESETS),
        "selection": {"enabled": sel.get("enabled") or [],
                      "placement": sel.get("placement") or {},
                      "expand": sel.get("expand") or {}},
        "limits": {
            "width": wh["size"]["width"] if wh else 0,
            "base_depth": wh["size"]["depth"] if wh else 0,
            "depth_limit": (min(others) - 2.0) if others else None,
            "has_variant": Path(eng.VARIANT_SPEC).exists(),
        },
    }


def plan_areas(enabled, target_depth=None):
    """纯计算：给定选中的模板，算出排布、占地与所需深度（不写任何文件）。"""
    eng = _variant_engine()
    catalog, _raw = eng.load_catalog()
    base = eng.jload(eng.BASE_SPEC)
    sel = eng.jload(eng.SELECTION) if Path(eng.SELECTION).exists() else {}
    place = sel.get("placement") or {}
    exp = sel.get("expand") or {}
    wh = next((w for w in base["warehouses"] if w["id"] == "W"), None)
    if wh is None:
        return {"ok": False, "error": "基础 spec 里没有 W 仓"}

    base_depth = float(wh["size"]["depth"])
    x_margin = float(place.get("x_margin_m", 4.0))
    gap = float(place.get("gap_m", 2.0))
    offset = float(place.get("band_start_offset_m", 2.0))
    north_aisle = float(exp.get("north_aisle_m", 2.0))
    target = float(target_depth) if target_depth else base_depth + 18

    picked = [catalog[k] for k in enabled if k in catalog]
    items = sorted(picked, key=lambda t: -t["priority"])
    band_z0 = base_depth + offset
    placed, depth_used = eng.pack(items, (x_margin, band_z0, wh["size"]["width"] - x_margin),
                                  x_margin, gap)
    need = base_depth + offset + depth_used + north_aisle
    others = [float(w["origin"][1]) for w in base["warehouses"] if w["id"] != "W"]
    limit = (min(others) - 2.0) if others else None

    rows = []
    for p in placed:
        proc = p.get("process") or {}
        slots = proc.get("parallel_slots") or 1
        mean = (proc.get("service_time_s") or {}).get("mean") or 0
        rows.append({
            "template": p["template"], "kind": p.get("kind"), "name": p.get("name"),
            "group": p.get("group", ""), "rect": p["rect"], "size": p["size"],
            "capacity": p.get("capacity"),
            "throughput_per_h": round(slots * 3600 / mean, 1) if mean else None,
        })
    unknown = [k for k in enabled if k not in catalog]
    return {
        "ok": limit is None or need <= limit + 1e-9,
        "placed": rows,
        "unknown": unknown,
        "depth": {"base": base_depth, "needed": round(need, 2), "target": round(target, 2),
                  "limit": limit, "grew": need > target},
        "band": {"x0": x_margin, "x1": round(wh["size"]["width"] - x_margin, 2),
                 "z0": round(band_z0, 2)},
        "totals": {"count": len(rows),
                   "area_m2": round(sum((r["rect"][2] - r["rect"][0]) *
                                        (r["rect"][3] - r["rect"][1]) for r in rows), 1),
                   "throughput_per_h": round(sum(r["throughput_per_h"] or 0
                                                 for r in rows), 1)},
    }


def apply_areas(enabled):
    """写 selection + 跑 build_variant --build（生成 layout_spec_areas.json 与
    data/areas_variant.json）。真正的几何校验交给它自己的 validate()。"""
    eng = _variant_engine()
    sel = eng.jload(eng.SELECTION) if Path(eng.SELECTION).exists() else {}
    sel = dict(sel)
    sel.update({"schema_version": "1.0", "warehouse": "W", "enabled": list(enabled)})
    Path(eng.SELECTION).parent.mkdir(parents=True, exist_ok=True)
    Path(eng.SELECTION).write_text(json.dumps(sel, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
    py = PY or sys.executable
    cmd = [py, "scripts/build_variant.py", "--build"]
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "error": repr(exc), "log": []}
    tail = [ln for ln in (p.stdout or "").splitlines() if ln.strip()][-14:]
    return {"ok": p.returncode == 0, "returncode": p.returncode, "log": tail,
            "spec": "config/layout_spec_areas.json"}



_PLAN_CACHE = {"mtime": 0.0, "html": ""}


def build_plan_html():
    """计划书 → HTML（按 mtime 缓存；转一次约 0.3 s）。"""
    src = ROOT / "docs" / "DC_MASTER_PLAN.md"
    if not src.exists():
        return "<!doctype html><meta charset='utf-8'><p>计划书尚未生成</p>"
    mt = src.stat().st_mtime
    if _PLAN_CACHE["mtime"] != mt:
        sys.path.insert(0, str(ROOT / "scripts"))
        import md2html
        _PLAN_CACHE["html"] = md2html.convert(src.read_text(encoding="utf-8"))
        _PLAN_CACHE["mtime"] = mt
    return _PLAN_CACHE["html"]


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


# ---------------------------------------------------------------- B/C 阶段 API helpers

def _load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def pnl_run_map():
    """run_id -> run_dir, strictly from the generated index (no arbitrary paths)."""
    idx = _load(OUT / "pnl_index.json", {}) or {}
    return {r["run_id"]: Path(r["run_dir"]) for r in idx.get("runs", [])}


def pnl_payload(run_id=None, include_all=False):
    runs = pnl_run_map()
    if include_all:
        rows = []
        for rid, d in runs.items():
            p = _load(d / "pnl.json", {}) or {}
            rows.append({"run_id": rid, "engine": p.get("engine"),
                         "prices_source": p.get("prices_source"),
                         "revenue": p.get("revenue_total"),
                         "variable": p.get("variable_cost_total"),
                         "fixed": p.get("fixed_cost_total"),
                         "profit": p.get("operating_profit"),
                         "orders": p.get("orders_completed")})
        return {"ok": True, "runs": rows}
    if run_id not in runs:
        return {"ok": False, "issues": ["unknown run_id: %s" % run_id],
                "available": sorted(runs)}
    d = runs[run_id]
    return {"ok": True, "run_id": run_id,
            "pnl": _load(d / "pnl.json", {}),
            "meta": _load(d / "ledger_meta.json", {}),
            "md": (d / "pnl.md").read_text(encoding="utf-8") if (d / "pnl.md").exists() else ""}


def ledger_payload(run_id, account=None, limit=200):
    runs = pnl_run_map()
    if run_id not in runs:
        return {"ok": False, "issues": ["unknown run_id: %s" % run_id]}
    path = runs[run_id] / "ledger.jsonl"
    try:
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "issues": ["cannot read ledger: %r" % (exc,)]}
    if account:
        rows = [r for r in rows if r["account"].startswith(account)]
    return {"ok": True, "run_id": run_id, "total": len(rows),
            "entries": rows[:max(1, min(limit, 1000))]}


COST_FIELDS = [
    ("revenue.per_order.INBOUND", 0, 1e6), ("revenue.per_order.OUTBOUND", 0, 1e6),
    ("revenue.per_order.PICK", 0, 1e6), ("revenue.per_order.REPLENISH", 0, 1e6),
    ("revenue.per_order.TRANSFER", 0, 1e6), ("revenue.per_line_extra", 0, 1e4),
    ("revenue.sla_penalty_per_tardy_s", 0, 1e4), ("revenue.lost_order_penalty", 0, 1e4),
    ("variable_costs.energy_tariff_per_kwh", 0, 1e3),
    ("variable_costs.consumables_per_order", 0, 1e3),
    ("fixed_costs_per_day.labor.operators", 0, 1000),
    ("fixed_costs_per_day.labor.hourly_rate", 0, 1e4),
    ("fixed_costs_per_day.labor.supervision_ratio", 0, 10),
    ("fixed_costs_per_day.robot_depreciation.capex_per_unit", 0, 1e9),
    ("fixed_costs_per_day.robot_depreciation.life_h", 1, 1e6),
    ("fixed_costs_per_day.robot_depreciation.units", 1, 10000),
    ("fixed_costs_per_day.maintenance_pct_of_capex_per_year", 0, 1),
    ("fixed_costs_per_day.space_rent_per_m2_month", 0, 1e4),
    ("fixed_costs_per_day.it_systems_per_day", 0, 1e5),
]


def patch_cost_spec(patch):
    path = ROOT / "config" / "cost_spec.json"
    cur = _load(path)
    if cur is None:
        return {"ok": False, "issues": ["cannot read cost_spec.json"]}
    allowed = {f[0]: f for f in COST_FIELDS}
    applied, rejected = {}, []
    for key, value in (patch or {}).items():
        if key == "prices_source":
            if value not in ("demo", "user", "live"):
                rejected.append({"path": key, "reason": "enum 只允许 demo/user/live"})
            else:
                cur["prices_source"] = value
                applied[key] = value
            continue
        field = allowed.get(key)
        if field is None:
            rejected.append({"path": key, "reason": "不在可编辑白名单内"})
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            rejected.append({"path": key, "reason": "不是数字"})
            continue
        if not (field[1] <= v <= field[2]):
            rejected.append({"path": key, "reason": "超出范围 [%s,%s]" % field[1:]})
            continue
        if "operators" in key or "units" in key or "life_h" in key:
            v = int(round(v))
        set_nested(cur, key, v)
        applied[key] = v
    if not applied:
        return {"ok": False, "applied": applied, "rejected": rejected}
    try:
        shutil.copyfile(path, ROOT / "config" / "cost_spec.bak.json")
        path.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "issues": ["写入失败: %r" % (exc,)]}
    return {"ok": True, "applied": applied, "rejected": rejected,
            "note": "价格已更新；需重跑 ledger.py --all --rebuild 与 pnl_report.py --all 才会反映到报表"}


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
            if path == "/api/kpi":
                return self.send_json(build_kpi_payload())
            if path == "/api/areas":
                return self.send_json(build_areas_payload())
            if path == "/api/areas/catalog":
                return self.send_json(build_areas_catalog())
            if path == "/api/areas/variant":
                d = read_json(DATA / "areas_variant.json", {}) or {}
                idx = _anchor_index()
                rows = []
                for a in d.get("areas", []):
                    xy, how = _area_xy(a, idx)
                    rows.append(dict(a, xy=xy, anchor_how=how))
                return self.send_json({"summary": d.get("summary", {}), "areas": rows,
                                       "exists": bool(d)})
            if path in ("/api/plan.html", "/api/plan"):
                body = build_plan_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return None
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
            if path == "/api/scenarios":
                return self.send_json({"ok": True, "scenarios": scenario_tools.list_scenarios(),
                                       "builtin_frozen": list(scenario_tools.BUILTIN)})
            if path == "/api/scenario/get":
                return self.send_json(scenario_tools.get_scenario((qs.get("id") or [""])[0]))
            if path == "/api/scenario/template":
                sid = (qs.get("id") or ["my_scenario"])[0]
                return self.send_json({"ok": True,
                                       "template": scenario_tools.default_template(sid)})
            if path == "/api/pnl":
                return self.send_json(pnl_payload(
                    (qs.get("run") or [None])[0], (qs.get("all") or ["0"])[0] == "1"))
            if path == "/api/ledger":
                return self.send_json(ledger_payload(
                    (qs.get("run") or [None])[0], (qs.get("account") or [None])[0],
                    int((qs.get("limit") or ["200"])[0])))
            if path == "/api/cost-spec":
                return self.send_json({"ok": True, "spec": _load(ROOT / "config" / "cost_spec.json"),
                                       "editable": [f[0] for f in COST_FIELDS] + ["prices_source"]})
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
            if path == "/api/cost-spec":
                return self.send_json(patch_cost_spec(payload.get("patch")))
            if path == "/api/areas/plan":
                return self.send_json(plan_areas(payload.get("enabled") or [],
                                                 payload.get("target_depth")))
            if path == "/api/areas/apply":
                return self.send_json(apply_areas(payload.get("enabled") or []))
            if path == "/api/scenario/validate":
                issues, warnings = scenario_tools.validate_scenario(payload.get("scenario") or {})
                return self.send_json({"ok": not issues, "issues": issues,
                                       "warnings": warnings})
            if path == "/api/scenario/dry-run":
                return self.send_json(scenario_tools.dry_run(
                    payload.get("scenario") or {}, float(payload.get("horizon") or 600)))
            if path == "/api/scenario/save":
                return self.send_json(scenario_tools.save_scenario(payload.get("scenario") or {}))
            if path == "/api/scenario/delete":
                return self.send_json(scenario_tools.delete_scenario(payload.get("id") or ""))
            if path == "/api/scenario/run":
                return self.send_json(scenario_tools.run_scenario(
                    payload.get("id") or "", payload.get("horizon")))
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
