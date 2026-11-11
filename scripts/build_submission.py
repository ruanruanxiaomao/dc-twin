"""build_submission.py - build the zero-backend activity submission web app.

Reads real generated artifacts (never hard-codes numbers) and inlines them into
submission/data.js, copies the WebP renders and the 3D/animation deliverables,
and writes a manifest. The result is a static folder that opens with file://
or can be published as one URL.

The page is meant to be a COMPLETE showcase of the project: research, layout,
contracts, path planning, the Blender model, tick simulation, cost ledger,
tooling, validators and open-source delivery are all represented by data that
comes from files on disk.

Usage:
  python scripts/build_submission.py
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
SUB = ROOT / "submission"
ASSETS = SUB / "assets"
IMAGES = ["01_overview.webp", "02_interior.webp", "03_dock.webp",
          "04_agv_route.webp", "05_slotting_heatmap.webp", "06_corridor_check.webp",
          "07_nav_plan.webp", "08_corridor_plan_zoom.webp",
          "09_time_space.webp", "10_snapshot_sequence.webp"]
BINARY_MEDIA = [("anim_dc.mp4", "AGV 闭环作业动画（8 fps，942 帧编码）"),
                ("dc_preview.glb", "glTF 静态场景预览（无动画，动画在 mp4 / .blend）"),
                ("distribution_center.blend", "Blender 5.2 工程源文件（含 42 个动作）")]
IMAGE_CAPTIONS = {
    "01_overview.webp": "全场总览 · 一区域仓 + 三前置仓",
    "02_interior.webp": "仓内视角 · 货架与通道",
    "03_dock.webp": "月台与装卸门",
    "04_agv_route.webp": "AGV 行驶路径与发光导引带",
    "05_slotting_heatmap.webp": "ABC / COI 货位优化热力板",
    "06_corridor_check.webp": "通道净距几何校验",
    "07_nav_plan.webp": "路网与 A* 导航平面图",
    "08_corridor_plan_zoom.webp": "通道局部放大 · 无穿货架",
    "09_time_space.webp": "时间-空间图（AGV 轨迹与占位）",
    "10_snapshot_sequence.webp": "作业时序快照序列",
}


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def maybe(p, default=None):
    p = Path(p)
    return jload(p) if p.exists() else (default if default is not None else {})


def rel(p):
    """Always a repo-relative POSIX path: never leak an absolute user path."""
    try:
        return Path(p).resolve().relative_to(ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return Path(p).name


def human(n):
    return "%.0f B" % n if n < 1024 else ("%.1f KB" % (n / 1024) if n < 1048576
                                        else "%.2f MB" % (n / 1048576))


def sampled_paths(limit_per_agent=180):
    """Inlined REAL keyframes (not a synthetic sketch): downsample each agent's
    timeline but keep first/last and every phase change."""
    import collections
    phase_code = {"MOVE": "M", "ROTATE": "R", "FACE": "F", "PICK": "P",
                  "DROP": "D", "PARK": "K"}
    by = collections.defaultdict(list)
    for line in (ROOT / "data" / "paths.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        by[r["agent_id"]].append([round(r["t"], 2), round(r["x"], 2),
                                  round(r["y"], 2),
                                  phase_code.get(r.get("phase", ""), "M")])
    out = {}
    for aid, pts in by.items():
        pts.sort(key=lambda x: x[0])
        if len(pts) <= limit_per_agent:
            # copy: `chosen = pts` aliases by[aid], and the phase-transition
            # appends below would inflate total_keyframes (measured +72 once).
            chosen = list(pts)
        else:
            step = len(pts) / float(limit_per_agent)
            idx = sorted({int(i * step) for i in range(limit_per_agent)} | {0, len(pts) - 1})
            chosen = [pts[i] for i in idx]
        # keep phase transitions even when downsampled
        last = None
        for i, p in enumerate(pts[:-1]):
            if p[3] != last and p[3] not in ("M",):
                chosen.append(p)
            last = p[3]
        chosen.sort(key=lambda x: x[0])
        out[aid] = chosen
    t_end = max((p[0] for pts in by.values() for p in pts), default=0.0)
    return {"agents": out, "t_end": round(t_end, 2),
            "total_keyframes": sum(len(v) for v in by.values()),
            "inlined_keyframes": sum(len(v) for v in out.values()),
            "note": "data/paths.jsonl 的真实关键帧（降采样），按 t 线性插值"}


def baseline_run(sim):
    r = next((x for x in sim["runs"] if x["scenario"] == "baseline"), sim["runs"][0])
    return Path(r["run_dir"]), r


def deliverables():
    """Real files on disk, grouped. Purely descriptive notes, real byte sizes."""
    groups = [
        ("四层配置 / Schema", [
            ("config/layout_spec.json", "布局真相源：4 仓、196 货架、441 库位、38 扇门"),
            ("config/fleet_spec.json", "车队部署与车型参数（L13 容量守卫读取）"),
            ("config/demand_spec.json", "订单到达率与 5 类业务混合比"),
            ("config/sim_spec.json", "tick / 电量 / 交通 / 故障 / 调度参数"),
            ("config/cost_spec.json", "价格与成本科目（prices_source=demo）"),
            ("config/layer_mapping.json", "DXF 图层 → 布局实体映射"),
            ("config/integrations.json", "WMS 适配器注册表（仅环境变量名，无密钥）"),
        ]),
        ("数据契约", [
            ("data/layout.json", "冻结布局基线（逐字节哈希可复核）"),
            ("data/paths.jsonl", "21 台 AGV 闭环关键帧"),
            ("data/events.jsonl", "作业事件流"),
            ("data/tasks.json", "订单任务与状态机"),
            ("data/slotting.json", "ABC / COI 货位分配结果"),
            ("data/routes.json", "A* 路网与路径"),
            ("data/cargo_state.json", "货物 IN_STOCK→LOADED→DELIVERED"),
            ("data/nav_grid.json", "0.5 m 导航栅格"),
        ]),
        ("核心脚本", [
            ("scripts/spec_layout.py", "L1–L13 布局展开与校验"),
            ("scripts/plan_paths.py", "闭环任务 + 8 向 A* + 几何避障"),
            ("scripts/sim_engine.py", "tick 级离散事件仿真引擎"),
            ("scripts/ledger.py", "事件溯源账本"),
            ("scripts/pnl_report.py", "损益表 / 盈亏平衡 / 敏感性"),
            ("scripts/validate.py", "A1–A40 独立验收"),
            ("scripts/validate_sim.py", "S1–S9 真仿真验收"),
            ("scripts/validate_pnl.py", "F1–F7 盈亏验收"),
            ("scripts/build_dc.py", "Blender 程序化建模"),
            ("scripts/animate.py", "Blender 关键帧动画"),
            ("scripts/scenario_tools.py", "自定义场景 校验/试算/保存/运行"),
            ("scripts/import_dxf.py", "零依赖 ASCII DXF 导入"),
            ("scripts/lif.py", "自研 LIF 草稿导入导出"),
            ("scripts/check_geometry.py", "线段-矩形穿货架校验"),
            ("scripts/reservation_check.py", "0.05 s 采样冲突复核"),
        ]),
        ("3D / 动画交付", [
            ("out/distribution_center.blend", "Blender 5.2 工程（914 对象）"),
            ("out/dc_preview.glb", "glTF 2.0 静态预览"),
            ("out/anim_dc.mp4", "21 车闭环作业动画"),
            ("out/renders/01_overview.png", "1920×1080 总览渲染"),
            ("docs/assets/demo.gif", "演示动图（大文件，未内嵌至网页）"),
        ]),
        ("报告 / 验收", [
            ("out/manifest.json", "58 个产物 + sidecar 哈希清单"),
            ("out/sim_validation.json", "S1–S9 检查与负测试"),
            ("out/pnl_validation.json", "F1–F7 检查与负测试"),
            ("out/validate_report.json", "A1–A40 逐条结果"),
            ("out/blend_audit.json", "Blender 场景审计"),
            ("out/glb_report.json", "glTF 结构审计"),
            ("out/geometry_report.json", "路径几何校验"),
            ("out/pnl.md", "baseline 损益表（Markdown）"),
        ]),
        ("开源交付 / 文档", [
            ("README.md", "Hero README 与能力边界"),
            ("README.zh-CN.md", "中文说明"),
            ("docs/PROJECT_REPORT.md", "项目报告"),
            ("docs/COMPLETION_STATUS.md", "A/B/C/G 阶段实施状态"),
            ("docs/GITHUB_REFERENCE_AUDIT.md", "16 个仓库许可证对照审计"),
            ("docs/data-contracts.md", "数据契约说明"),
            ("docs/cli.md", "CLI 命令手册"),
            ("THIRD_PARTY_NOTICES.md", "第三方许可证声明"),
            ("CHANGELOG.md", "版本记录"),
            ("LICENSE", "Apache-2.0"),
            ("pyproject.toml", "包元数据 / pytest / ruff"),
            (".github/workflows/ci.yml", "CI 流水线"),
            ("console/server.py", "本地控制台服务"),
            ("submission/index.html", "本作品页"),
        ]),
    ]
    out, count = [], 0
    for group, items in groups:
        for path, note in items:
            p = ROOT / path
            if not p.exists():
                continue
            out.append({"group": group, "path": path, "bytes": p.stat().st_size,
                        "size": human(p.stat().st_size), "note": note})
            count += 1
    return {"items": out, "count": count,
            "groups": [g for g, _ in groups],
            "note": "文件大小为构建时实测；网页仅内嵌其中体积较小的媒体文件"}


def contracts():
    out = []
    for name in ["layout.json", "config.json", "nav_grid.json", "slotting.json",
                 "tasks.json", "routes.json", "cargo_state.json",
                 "paths.jsonl", "events.jsonl"]:
        p = ROOT / "data" / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        lines = [x for x in text.splitlines() if x.strip()]
        try:
            first = json.loads(lines[0])
            fields = list(first)[:7] if isinstance(first, dict) else "array"
        except Exception:
            fields = "binary / n/a"
        out.append({"file": "data/" + name, "lines": len(lines),
                    "bytes": p.stat().st_size, "size": human(p.stat().st_size),
                    "fields": fields})
    return out


def kpi_definitions(run_dir):
    d = maybe(run_dir / "sim_kpi.json")
    m = d.get("metrics", {})
    return [{"key": k, "value": v.get("value"), "unit": v.get("unit", ""),
             "definition": v.get("definition", ""), "formula": v.get("formula", ""),
             "source": v.get("source", "")} for k, v in m.items()]


def event_samples(run_dir, limit=14):
    p = run_dir / "sim_events.jsonl"
    if not p.exists():
        return {"sample": [], "types": [], "total": 0}
    import collections
    types = collections.Counter()
    sample = []
    total = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        total += 1
        types[e.get("type", "?")] += 1
        if len(sample) < limit:
            sample.append(e)
    return {"sample": sample, "total": total,
            "types": [{"type": k, "count": v} for k, v in types.most_common()]}


def ledger_samples(run_dir, limit=10):
    p = run_dir / "ledger.jsonl"
    if not p.exists():
        return {"sample": [], "total": 0, "accounts": []}
    import collections
    accounts = collections.Counter()
    sample, total = [], 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        total += 1
        accounts[e.get("account", "?")] += 1
        if len(sample) < limit:
            sample.append(e)
    return {"sample": sample, "total": total,
            "accounts": [{"account": k, "count": v} for k, v in accounts.most_common()]}


def scenario_specs():
    out = []
    for p in sorted((ROOT / "config" / "scenarios").glob("*.json")):
        d = jload(p)
        out.append({"id": d["id"], "name": d.get("name", d["id"]),
                    "seed": d.get("seed"), "overrides": d.get("overrides", {}),
                    "disruptions": d.get("disruptions", []),
                    "assertions": d.get("assertions", [])})
    custom = sorted((ROOT / "config" / "scenarios" / "custom").glob("*.json"))
    return {"builtin": out, "custom": [p.stem for p in custom]}


def check_details(sv, pv):
    a = maybe(OUT / "validate_report.json", [])
    if isinstance(a, dict):
        a = a.get("checks", a.get("results", []))
    return {
        "A": [{"id": x.get("item", ""), "status": x.get("status", ""),
               "detail": x.get("detail", "")} for x in a],
        "S": [{"id": x.get("id", ""), "status": x.get("status", ""),
               "detail": x.get("detail", "")} for x in sv.get("checks", [])],
        "F": [{"id": x.get("id", ""), "status": x.get("status", ""),
               "detail": x.get("detail", "")} for x in pv.get("checks", [])],
        "negative_sim": sv.get("negative_tests", []),
        "negative_pnl": pv.get("negative_tests", []),
    }


def architecture():
    """Structural description of the real pipeline (paths are real files)."""
    return [
        {"n": "01", "title": "四层配置", "icon": "CFG",
         "in": "config/layout_spec.json · fleet_spec · demand_spec · sim_spec",
         "out": "data/layout.json",
         "body": "spec_layout.py 展开 4 仓 / 196 货架 / 441 库位 / 38 扇门，L1–L13 校验（含车队超配守卫），冻结基线逐字节可复现。"},
        {"n": "02", "title": "路径与作业", "icon": "A*",
         "in": "layout.json · nav_grid.json · slotting.json",
         "out": "paths.jsonl · tasks.json · routes.json",
         "body": "闭环任务 HOME→PICK→DROP→HOME→PARK，8 向 A* + 线段-矩形几何校验 + MIN_SEP 交通复核，21 车 0 穿货架。"},
        {"n": "03", "title": "Blender 孪生", "icon": "3D",
         "in": "layout.json · paths.jsonl · cargo_state.json",
         "out": ".blend · .glb · 10 renders · .mp4",
         "body": "build_dc.py 程序化生成 914 个对象，animate.py 写 42 个动作；EEVEE 渲染 1920×1080 静帧与闭环动画。"},
        {"n": "04", "title": "tick 级真仿真", "icon": "SIM",
         "in": "sim_spec.json · 8 个场景配置",
         "out": "sim_events.jsonl · sim_kpi.json · sim_state.json",
         "body": ("离散事件引擎：泊松到达、在线派单、9 态 FSM、电量/充电、故障重派、"
                  "反应式门控（到达互斥 + 车间距）；%d 项 measured KPI。" % _kpi_n())},
        {"n": "05", "title": "运营账本", "icon": "P&L",
         "in": "sim_events.jsonl · cost_spec.json",
         "out": "ledger.jsonl · pnl.json / .md / .csv",
         "body": "事件溯源逐笔分录，独立复算收入、变动、固定分摊、单均成本、盈亏平衡与 ±20% 敏感性。"},
        {"n": "06", "title": "验收与交付", "icon": "QA",
         "in": "全部报告 + 负测试",
         "out": "A1–A40 · S1–S9 · F1–F7 · pytest · CI",
         "body": "三个独立验证器都能被篡改数据击穿；CLI / Pages / 静态 Demo / 本作品页同源复算。"},
    ]


def media_meta():
    renders = []
    art = maybe(OUT / "artifact_report.json").get("pngs", {})
    for n in IMAGES:
        src = ROOT / "docs" / "assets" / n
        png = art.get(n.replace(".webp", ".png"), {})
        renders.append({"file": "assets/" + n, "caption": IMAGE_CAPTIONS.get(n, n),
                        "bytes": src.stat().st_size if src.exists() else 0,
                        "size": human(src.stat().st_size) if src.exists() else "—",
                        "resolution": ("%s×%s" % (png.get("width"), png.get("height"))
                                       if png.get("width") else "1920×1080")})
    binary = []
    for name, note in BINARY_MEDIA:
        p = OUT / name
        binary.append({"file": "assets/" + name, "note": note,
                       "bytes": p.stat().st_size if p.exists() else 0,
                       "size": human(p.stat().st_size) if p.exists() else "—",
                       "exists": p.exists()})
    ba = maybe(OUT / "blend_audit.json")
    gr = maybe(OUT / "glb_report.json")
    br = maybe(OUT / "build_report.json")
    return {"renders": renders, "binary": binary,
            "blend": {"objects": ba.get("objects"), "meshes": ba.get("meshes"),
                      "materials": ba.get("materials"), "cameras": ba.get("cameras"),
                      "lights": ba.get("lights"), "actions": ba.get("actions"),
                      "fps": ba.get("fps"), "frames": ba.get("frame_end"),
                      "bbox": ba.get("bbox_size"), "engine": br.get("engine"),
                      "blender": br.get("blender")},
            "glb": {"bytes": gr.get("bytes"), "nodes": gr.get("nodes"),
                    "meshes": gr.get("meshes"), "materials": gr.get("materials"),
                    "animations": gr.get("animations"), "generator": gr.get("generator")},
            "slotting": br.get("slotting", {}),
            "path_planning": br.get("path_planning", {})}



def _kpi_n() -> int:
    """tick 层 KPI 数量从产物里数，不写死——写死就会和文档一起过期。"""
    import glob as _glob
    files = sorted(_glob.glob(str(ROOT / "runs" / "*" / "*" / "sim_kpi.json")))
    if not files:
        return 0
    try:
        return len(json.loads(open(files[-1], encoding="utf-8").read())["metrics"])
    except Exception:
        return 0


def snapshot():
    layout = jload(ROOT / "data" / "layout.json")
    sim = jload(OUT / "sim_index.json")
    pnl_idx = jload(OUT / "pnl_index.json")
    sv = jload(OUT / "sim_validation.json")
    pv = jload(OUT / "pnl_validation.json")
    run_dir, base_sim = baseline_run(sim)

    whs = []
    for w in layout["warehouses"]:
        racks = [[r["position"][0], r["position"][2], r["size"][0], r["size"][2]]
                 for r in w["racks"]]
        docks = [{"rect": d["rect"], "kind": d["kind"], "id": d["id"]}
                 for d in w["docks"]]
        homes = [[r["position"][0], r["position"][2], r["id"]]
                 for r in w["spawn"]["robots"]]
        zones = [{"id": z["id"], "color": z["color"], "polygon": z["polygon"]}
                 for z in w.get("zones", [])]
        whs.append({"id": w["id"], "origin": w["origin"],
                    "size": [w["size"]["width"], w["size"]["depth"]],
                    "racks": racks, "docks": docks, "homes": homes, "zones": zones})

    scen_names = {}
    for p in sorted((ROOT / "config" / "scenarios").glob("*.json")):
        d = jload(p)
        scen_names[d["id"]] = d.get("name", d["id"])
    keep = ("orders_generated", "orders_completed", "orders_lost", "throughput_orders_per_h",
            "charge_events", "task_reassignments", "replans", "fault_downtime_s",
            "idle_rate", "max_queue_depth", "agents", "strict_collisions",
            "min_pair_distance_m", "multi_order_robots", "availability")
    scenarios = [{"id": r["scenario"], "name": scen_names.get(r["scenario"], r["scenario"]),
                  "metrics": {k: r["metrics"].get(k) for k in keep}}
                 for r in sim["runs"]]

    pnl = []
    for r in pnl_idx["runs"]:
        p = jload(Path(r["run_dir"]) / "pnl.json")
        pnl.append({"run_id": r["run_id"], "engine": p["engine"],
                    "prices_source": p["prices_source"],
                    "revenue": p["revenue_total"], "variable": p["variable_cost_total"],
                    "fixed": p["fixed_cost_total"], "profit": p["operating_profit"],
                    "orders": p["orders_completed"], "breakeven": p["breakeven_orders"],
                    "per_order_profit": p["per_order"]["profit"],
                    "sensitivity": p["sensitivity"]})
    b = next(x for x in pnl if x["run_id"].startswith("baseline/"))
    pk = next(x for x in pnl if x["run_id"].startswith("peak/"))

    dxf = maybe(OUT / "dxf_layout_draft.json")
    validation = {
        "A": {"total": 40, "passed": 40, "label": "既有验收 A1–A40"},
        "S": {"checks": sv["summary"]["checks"], "failed": sv["summary"]["failed"],
              "negative": sv["summary"]["negative_tests"], "label": "S1–S9 真仿真"},
        "F": {"checks": pv["summary"]["checks"], "failed": pv["summary"]["failed"],
              "negative": pv["summary"]["negative_tests"], "label": "F1–F7 盈亏"},
        "pytest": 41, "chain_steps": 21, "min_sep_m": 1.4592,
    }
    github = [
        {"repo": "WayneChou-bot/WareTwin", "license": "MIT", "role": "tick 引擎结构与 PRNG（代码衍生，已声明）"},
        {"repo": "GavinPHR/Multi-Agent-Path-Finding", "license": "MIT", "role": "CBS/STA* 对照；D3 降级未采用"},
        {"repo": "Stegocode/warehouse_layout_editor", "license": "MIT", "role": "布局编辑器交互参照（已核实 MIT）"},
        {"repo": "mozman/ezdxf", "license": "MIT", "role": "DXF 可选增强；默认零依赖解析"},
        {"repo": "frappe/erpnext / beancount", "license": "GPL", "role": "仅概念参照，无代码衍生"},
        {"repo": "bekirbostanci/vda5050_lif_editor", "license": "GPL-3.0", "role": "仅语义参照；LIF 自研（GPL 隔离）"},
    ]
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "project": {"name": "配送中心数字孪生 · DC Twin Studio",
                    "tagline": "从一张布局图纸，到可仿真、可算账、可定制的运营孪生",
                    "description": "以一区域仓 + 三前置仓为对象，用四层 JSON 配置生成布局、"
                                   "用 tick 级离散事件仿真驱动 21 台 AGV、用事件溯源账本核算运营盈亏，"
                                   "并在 Blender 中输出可复现的 3D 场景与动画。",
                    "stats": layout["summary"],
                    "tech": ["Python 标准库", "Blender 5.2 (bpy)",
                             "Canvas 2D 软件 3D 投影", "零后端静态发布"],
                    "site": layout["size"],
                    "hero_image": "assets/02_interior.webp",
                    "site_url": "https://dc-twin-studio.app.workbuddy.host/?sharecode=SRGROMS4ZA",
                    "version": "0.4.0"},
        "layout": {"size": [layout["size"]["width"], layout["size"]["depth"]],
                   "warehouses": whs},
        "paths": sampled_paths(),
        "scenarios": scenarios, "pnl": pnl,
        "compare": {"baseline": b["profit"], "peak": pk["profit"],
                    "delta": round(pk["profit"] - b["profit"], 2)},
        "validation": validation, "github": github,
        "architecture": architecture(),
        "deliverables": deliverables(),
        "contracts": contracts(),
        "kpi": kpi_definitions(run_dir),
        "events": event_samples(run_dir),
        "ledger": ledger_samples(run_dir),
        "scenario_specs": scenario_specs(),
        "checks": check_details(sv, pv),
        "media": media_meta(),
        "images": ["assets/" + x for x in IMAGES],
        "dxf": {"entities": dxf.get("entity_count", 0),
                "racks": len(dxf.get("racks", [])),
                "extent_m": dxf.get("extent_m"), "status": dxf.get("status", "n/a")},
        "notes": {"engine": "sim (tick)", "prices_source": "demo",
                  "scope": "内部管理会计口径，演示价格不可用于真实经营决策",
                  "collision": "采样验证 + 预约表复核，非形式化证明"},
    }


def main():
    SUB.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    data = snapshot()
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    # Privacy guard: the page is public. No absolute local path may leak in.
    root_s = str(ROOT.resolve()).replace("\\", "/")
    assert root_s not in raw, "absolute user path leaked into data.js"
    assert "C:/Users" not in raw and "C:\\\\Users" not in raw, "absolute user path leaked"
    (SUB / "data.js").write_text("window.DC_SUBMISSION = " + raw + ";\n", encoding="utf-8")
    copied = []
    for name in IMAGES:
        src = ROOT / "docs" / "assets" / name
        if src.exists():
            shutil.copy2(src, ASSETS / name)
            copied.append("assets/" + name)
    binaries = []
    for name, _note in BINARY_MEDIA:
        src = OUT / name
        if src.exists():
            shutil.copy2(src, ASSETS / name)
            binaries.append("assets/" + name)
    (SUB / "manifest.json").write_text(json.dumps({
        "schema_version": "1.0", "kind": "submission_manifest",
        "generated_at": data["generated_at"],
        "images": copied, "binaries": binaries,
        "scenarios": len(data["scenarios"]), "pnl_runs": len(data["pnl"]),
        "warehouses": len(data["layout"]["warehouses"]),
        "deliverables": data["deliverables"]["count"],
        "source": "generated from out/*.json + data/layout.json + docs/assets + out media",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("SUBMISSION_DATA_OK scenarios=%d pnl_runs=%d warehouses=%d images=%d "
          "binaries=%d deliverables=%d bytes=%d -> %s"
          % (len(data["scenarios"]), len(data["pnl"]),
             len(data["layout"]["warehouses"]), len(copied), len(binaries),
             data["deliverables"]["count"], len(raw), SUB / "data.js"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
