#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R8.2 门 · 货架派发与几何分型（``ASSET_DISPATCH_OK``）。

R8.1 把资产**目录**钉住了；这一门管**派发**：到底哪一类货架建出来的是哪一份几何，
以及"默认构建一毫米没动"。

五道子门：

* ``RACK_MESH_DISTINCT_OK``   9 类机理 → 9 个互不相同的**几何指纹**（盒子数 + 每个盒子的
                              尺寸/材质槽）。指纹是算出来的，不是渲染出来用眼睛比的——
                              R4 的「0 巷道却全绿」就是这么栽的。
* ``RACK_MESH_DISTINCT_NEG``  负对照：把所有 source 强行改回 ``unit_rack_mesh``，
                              9 类必须**塌成 1 类**且派发器**直接拒绝**。塌不掉，就说明
                              "分型"只是个说法。
* ``DISPATCH_RESOLVE_OK``     每个 rack_type 都能反查到**同一个**机理的函数；
                              未知类型抛错；没声明 rack_type 一律 legacy。
* ``DISPATCH_WIRED_OK``       build_dc 真的调了 ``rack_assets.resolve``（AST 级取证，
                              不是 grep 关键字），并且有"整份布局无 rack_type 就短路"的分支。
* ``DEFAULT_OFF_OK``          默认数据里一个 rack_type 都没有、设备接线开关为假、
                              现有 build_report 里没有 rack_dispatch 键——三条同时成立，
                              才叫"默认产出与 R8 之前一致"。

自检注入 5 个反例：几何塌陷、source 与机理不一致、未知机型、默认布局混入 rack_type、
设备开关被打开。全部必须被抓。
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import rack_assets as ra                                          # noqa: E402

CONFIG, DATA, OUT = ROOT / "config", ROOT / "data", ROOT / "out"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _racs():
    return jload(CONFIG / "asset_spec.json")


# ---------------------------------------------------------------- gates


def check_distinct(spec, boxes_fn=None, mod=ra):
    """9 类机理必须 9 个指纹。boxes_fn 可注入（负对照用）。"""
    boxes_fn = boxes_fn or (lambda t: mod.geometry(t)["boxes"])
    problems = []
    fps, detail = {}, {}
    for t in spec["rack_assets"]:
        try:
            boxes = boxes_fn(t)
        except Exception as e:                                    # noqa: BLE001
            problems.append("%s 建不出几何：%r" % (t, e))
            continue
        sig = mod.signature_from_boxes(t, boxes)
        fp = mod.hash_of(sig)
        fps.setdefault(fp, []).append(t)
        detail[t] = {"boxes": sig["boxes"], "fp": fp}
        if sig["boxes"] == 0:
            problems.append("%s 的几何是空的" % t)
    for fp, ts in fps.items():
        if len(ts) > 1:
            problems.append("这些机理长得一模一样：%s（fp=%s）" % (",".join(ts), fp))
    return not problems, {"distinct": len(fps), "types": len(spec["rack_assets"]),
                          "by_type": detail}, problems


def check_distinct_neg(spec, mod=ra):
    """负对照：把所有 source 强行改回 selective。

    只有两种可接受结局，其余都算「分型是假的」：

    1. 8 类被派发器**拒绝**（外观与行为不是同一个机理），1 类合法退化成 selective；
    2. 或者全部静默退化成同一个函数（此时 9 类外观塌成 1 类）。

    只要结果里出现第三种机理，说明"强制回退"其实没生效，负对照是空的。
    """
    problems = []
    forced = mod.force_selective(spec)
    reached, refused = set(), 0
    for t in forced["rack_assets"]:
        try:
            d = mod.resolve({"rack_type": t}, forced)
        except Exception:                                         # noqa: BLE001
            refused += 1
            continue
        reached.add(d.get("mechanism"))
    if reached != {"selective_pallet"}:
        problems.append("负对照没塌：强行改回 selective 后仍能到 %s" % sorted(reached))
    if refused != len(forced["rack_assets"]) - 1:
        problems.append("应拒绝 %d 个，实际拒绝 %d 个"
                        % (len(forced["rack_assets"]) - 1, refused))
    return not problems, {"reached": sorted(reached), "refused": refused,
                          "selective_fp": mod.fingerprint("selective_pallet")}, problems


def _defines(path, name):
    tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    return any(isinstance(n, ast.FunctionDef) and n.name == name for n in ast.walk(tree))


def check_resolve(spec, mod=ra):
    problems = []
    ok_types = 0
    for t in spec["rack_assets"]:
        try:
            d = mod.resolve({"rack_type": t}, spec)
        except Exception as ex:                                   # noqa: BLE001
            problems.append("%s 解析失败：%s" % (t, ex))
            continue
        if d.get("mode") != "procedural" or d.get("mechanism") != t:
            problems.append("%s 解析成 %r，与机理不符" % (t, d))
            continue
        fn = d["function"]
        if hasattr(mod, fn):
            ok_types += 1
        elif fn in mod.EXTERNAL_FUNCTIONS:
            if _defines(ROOT / mod.EXTERNAL_FUNCTIONS[fn], fn):
                ok_types += 1
            else:
                problems.append("%s 指向外部函数 %s()，但 %s 里没有定义"
                                % (t, fn, mod.EXTERNAL_FUNCTIONS[fn]))
        else:
            problems.append("%s 指向的函数 %s() 不存在" % (t, fn))
    d = mod.resolve({}, spec)
    if d.get("mode") != "legacy":
        problems.append("没声明 rack_type 却没走 legacy：%r" % d)
    try:
        mod.resolve({"rack_type": "__not_a_type__"}, spec)
        problems.append("未知 rack_type 没有报错（静默回退）")
    except Exception:                                             # noqa: BLE001
        pass
    return not problems, {"resolved": ok_types, "legacy_ok": d.get("mode") == "legacy"}, problems


def check_wired(src_path=None):
    """AST 取证：build_dc 真的调了 resolve，且有短路分支。"""
    p = Path(src_path or (ROOT / "scripts" / "build_dc.py"))
    problems = []
    tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    fn = fns.get("rack_mesh_for")
    if fn is None:
        problems.append("build_dc 里没有 rack_mesh_for()")
        return False, {}, problems
    calls_resolve = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "resolve" for n in ast.walk(fn))
    short_circuit = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id == "_dispatch_active" for n in ast.walk(fn))
    if not calls_resolve:
        problems.append("rack_mesh_for 没有调用 rack_assets.resolve()")
    if not short_circuit:
        problems.append("rack_mesh_for 没有 _dispatch_active() 短路（默认会写 COUNTS，破坏字节一致）")
    imp = fns.get("import_assets")
    equip = False
    if imp is not None:
        equip = any(isinstance(n, ast.Constant) and n.value == "equipment_dispatch"
                    for n in ast.walk(imp))
    if not equip:
        problems.append("import_assets 没有读 equipment_dispatch 开关")
    return not problems, {"calls_resolve": calls_resolve, "short_circuit": short_circuit,
                          "equipment_switch": equip}, problems


def check_default_off(spec, layout=None):
    """默认三条同时成立，才叫"默认产出不变"。"""
    problems = []
    lay = layout if layout is not None else jload(DATA / "layout.json")
    typed = [rk["id"] for wh in lay["warehouses"] for rk in wh["racks"] if "rack_type" in rk]
    if typed:
        problems.append("默认布局里有 %d 个货架带 rack_type（首个 %s）——"
                        "要么重生成数据，要么就该重冻结" % (len(typed), typed[0]))
    act = (spec.get("activate") or {})
    if act.get("equipment_dispatch"):
        problems.append("asset_spec.activate.equipment_dispatch 为真：默认构建会多接母版")
    br = OUT / "build_report.json"
    has_dispatch = False
    if br.exists():
        d = jload(br)
        has_dispatch = "rack_dispatch" in (d.get("counts") or {})
        if has_dispatch:
            problems.append("现有 build_report 里有 rack_dispatch 键——说明它不是默认构建产出的")
    return not problems, {"typed_racks": len(typed), "equipment_dispatch": bool(act.get("equipment_dispatch")),
                          "build_report_clean": not has_dispatch}, problems


# ---------------------------------------------------------------- 渲染层探针

def find_blender():
    """优先用项目统一的解析器（BLENDER_EXE → PATH → 常见安装位置）。"""
    try:
        sys.path.insert(0, str(ROOT))
        from dc_twin.paths import blender_exe
        b = blender_exe()
        if b:
            return b
    except Exception:                                             # noqa: BLE001
        pass
    import shutil
    return shutil.which("blender")


def _source_sha():
    import hashlib
    return hashlib.sha256((ROOT / "scripts" / "rack_assets.py").read_bytes()).hexdigest()


def run_probe():
    """在 Blender 里把 9 类 mesh 真建出来，产 out/rack_mesh_probe.json。"""
    import subprocess
    b = find_blender()
    if not b:
        return False, "找不到 Blender（设 BLENDER 环境变量）"
    r = subprocess.run([b, "-b", "--factory-startup", "-P",
                        str(ROOT / "scripts" / "rack_mesh_probe.py")],
                       capture_output=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    if r.returncode != 0 or "PROBE_OK" not in (r.stdout or ""):
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-3:]
        return False, "探针失败：%s" % " / ".join(tail)
    return True, ""


def check_probe():
    """探针新鲜度 + 9 类 mesh 坐标确实不同。"""
    problems = []
    p = OUT / "rack_mesh_probe.json"
    if not p.exists():
        return False, {}, ["没有 out/rack_mesh_probe.json（先跑 --probe 生成）"]
    d = jload(p)
    if d.get("source_sha") != _source_sha():
        problems.append("探针过期：rack_assets.py 已改，探针还是旧的（重跑 --probe）")
    geoms = {k: v.get("geom_sha") for k, v in (d.get("mechanisms") or {}).items()}
    if len(geoms) != 9:
        problems.append("探针只覆盖 %d 类机理" % len(geoms))
    if len(set(geoms.values())) != len(geoms):
        dup = sorted(k for k in geoms if list(geoms.values()).count(geoms[k]) > 1)
        problems.append("Blender 里建出来的 mesh 有重复：%s" % ",".join(dup))
    return not problems, {"blender": d.get("blender"), "mechanisms": len(geoms),
                          "distinct_geoms": len(set(geoms.values()))}, problems



# ------------------------------------------------- 带机理的整场构建（R8.3）

TYPED_TYPES = ("double_deep", "drive_in", "drive_through", "pallet_shuttle",
               "mobile", "carton_flow", "pallet_gravity")
TYPED_DIR = OUT / "exp" / "typed"
TYPED_GOLDEN = ROOT / "tests" / "golden" / "typed_3d.json"


def make_typed_spec(base=None):
    """把每个货架组轮着声明一种机理。顺序固定 —— 否则每次跑出来的场景都不一样。"""
    spec = copy.deepcopy(base or jload(CONFIG / "layout_spec.json"))
    i = 0
    for wh in spec["warehouses"]:
        for g in wh["racks"]:
            t = TYPED_TYPES[i % len(TYPED_TYPES)]
            g["rack_type"] = t
            if t in ("drive_in", "drive_through", "pallet_shuttle", "pallet_gravity"):
                g.setdefault("lane_depth", 4)
            i += 1
    return spec, i


def build_typed_layout():
    """生成 spec → 展开成布局。返回 (layout, layout_sha, 组数)。"""
    import hashlib
    TYPED_DIR.mkdir(parents=True, exist_ok=True)
    spec, groups = make_typed_spec()
    sp = TYPED_DIR / "typed_spec.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    r = _py(["scripts/spec_layout.py", "--spec", str(sp),
             "--out", str(TYPED_DIR / "spec_layout.json"),
             "--report", str(TYPED_DIR / "report.json"),
             "--write", str(TYPED_DIR / "typed_layout.json"), "--quiet"])
    if r.returncode != 0:
        raise RuntimeError("spec_layout 失败：%s" % ((r.stdout or "") + (r.stderr or ""))[-400:])
    rep = jload(TYPED_DIR / "report.json")
    return TYPED_DIR / "typed_layout.json", rep.get("layout_sha256"), groups


def _py(args):
    import subprocess
    return subprocess.run([sys.executable] + args, cwd=str(ROOT),
                          capture_output=True, encoding="utf-8", errors="replace")


def run_typed_build():
    """Blender 里把带机理的整场建出来；返回摘要（含 glb 哈希）。"""
    import hashlib
    import subprocess
    b = find_blender()
    if not b:
        return None, "找不到 Blender（设 BLENDER 环境变量）"
    layout, sha, groups = build_typed_layout()
    r = subprocess.run([b, "-b", "--factory-startup", "-P",
                        str(ROOT / "scripts" / "typed_build_probe.py"), "--",
                        str(layout), str(TYPED_DIR / "scene")],
                       capture_output=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    if r.returncode != 0 or "TYPED_BUILD_OK" not in (r.stdout or ""):
        return None, "typed build 失败：%s" % ((r.stdout or "") + (r.stderr or ""))[-500:]
    summ = jload(TYPED_DIR / "scene" / "typed_summary.json")
    glb = TYPED_DIR / "scene" / "dc_preview.glb"
    summ["glb_structure"] = glb_structure_digest(glb)
    import hashlib as _h
    summ["glb_sha256"] = _h.sha256(glb.read_bytes()).hexdigest()
    summ["glb_bytes"] = glb.stat().st_size
    summ["layout_sha256"] = sha
    summ["groups"] = groups
    return summ, ""


def glb_structure_digest(path):
    """GLB 的**结构指纹**：mesh 名 + primitive 数 + 顶点数 + 材质名。

    **为什么不是 sha256(file)**：同一份数据连跑两次，实测（Blender 5.2.41）：

    * 默认场景：JSON chunk 完全一致，二进制 1864/665196 字节不同，全部落在 7 个
      **索引缓冲**里；排序后完全相同 → 只是三角化顺序不同，几何没变；
    * 带机理场景：两次运行相差 1 个**未被引用的 accessor**（889 vs 888，字节
      1185888 vs 1185036），但 mesh / primitive / 顶点数 / 材质逐个相同。

    也就是说字节哈希此刻不可用（会把"当时怎么调度"写进金标准），而结构指纹稳定。
    把不稳定当成"金标准"的下场是门永远红、且红得没信息量；把不稳定**记下来**、
    锚住真正稳定的那一层，才是这一层能给的证据。字节级复现列为 R8.4 待修。
    """
    import hashlib
    import struct
    d = Path(path).read_bytes()
    jlen, _jt = struct.unpack("<II", d[12:20])
    doc = json.loads(d[20:20 + jlen])
    rows = []
    for m in doc.get("meshes", []):
        prims = []
        for pr in m.get("primitives", []):
            pos = (pr.get("attributes") or {}).get("POSITION")
            acc = doc["accessors"][pos] if isinstance(pos, int) else {}
            prims.append((pr.get("mode", 4), acc.get("count"), acc.get("type")))
        rows.append((m.get("name"), tuple(prims)))
    rows.sort()
    mats = sorted(x.get("name", "") for x in doc.get("materials", []))
    nodes = sorted(n.get("name", "") for n in doc.get("nodes", []))
    payload = json.dumps({"meshes": rows, "materials": mats,
                          "nodes": len(nodes)}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _anchors(summ):
    return {
        "dispatch": summ.get("dispatch") or {},
        "fallback": summ.get("fallback"),
        "typed_racks": summ.get("typed_racks"),
        "pallets_built": summ.get("pallets_built"),
        "objects": summ.get("objects"),
        "meshes": summ.get("meshes"),
        "glb_structure": summ.get("glb_structure"),
        "glb_nodes": (summ.get("glb") or {}).get("nodes"),
        "glb_meshes": (summ.get("glb") or {}).get("meshes"),
        "layout_sha256": summ.get("layout_sha256"),
        "master_props": summ.get("master_props") or {},
        "master_props_unplaced": summ.get("master_props_unplaced") or [],
        "glb_sha256": summ.get("glb_sha256"),
    }


REQUIRED_MASTERS = ("asset_scanner_arch", "asset_forklift_loaded",
                    "asset_amr_cart", "asset_stacker_crane")


def check_masters(summ):
    """闲置母版必须真的**摆进场景**，不是只出现在 asset_meshes 里。"""
    problems = []
    placed = summ.get("master_props") or {}
    for n in REQUIRED_MASTERS:
        if not placed.get(n):
            problems.append("%s 没摆进场景（只在 asset_meshes 里不算接入）" % n)
    unplaced = set(summ.get("master_props_unplaced") or [])
    if "asset_gantry_crane" not in unplaced:
        problems.append("龙门吊没有落脚点（这套布局无堆场），应显式记为未摆放而不是随便摆")
    return not problems, {"placed": placed, "unplaced": sorted(unplaced)}, problems


def compare_anchor(summ, golden):
    """双锚比对：counts 与 GLB 哈希。任一漂移都判红，并说出漂在哪。"""
    problems = []
    got = _anchors(summ)
    want = golden.get("anchors") or {}
    for k in ("dispatch", "fallback", "typed_racks", "pallets_built",
              "objects", "meshes", "glb_structure",
              "glb_nodes", "glb_meshes", "layout_sha256",
              "master_props", "master_props_unplaced", "glb_sha256"):
        if got.get(k) != want.get(k):
            problems.append("%s 变了：%r → %r" % (k, want.get(k), got.get(k)))
    return not problems, {"comparison": "counts + glb_sha256(已规整) + structure", "checked": len(want)}, problems


# ---------------------------------------------------------------- self test


def self_test():
    spec = _racs()
    cases = []

    base = [check_distinct(spec), check_distinct_neg(spec), check_resolve(spec),
            check_wired(), check_default_off(spec)]
    cases.append(("baseline", all(c[0] for c in base)))

    # 1 几何塌陷：把 push_back 换成 selective 的几何（真的走注入，不是喂个坏对象）
    def collapsed_boxes(t):
        return ra.selective_pallet(4) if t == "push_back" else ra.geometry(t)["boxes"]
    cases.append(("geometry_collapsed", not check_distinct(spec, collapsed_boxes)[0]))

    # 1b 负对照失效：force_selective 假装成功但其实没改，NEG 必须自己判红
    class NoForce:
        pass
    nf = NoForce()
    for name in ("geometry", "signature_from_boxes", "hash_of", "fingerprint",
                 "signature", "resolve"):
        setattr(nf, name, getattr(ra, name))
    nf.force_selective = lambda s: copy.deepcopy(s)
    nf.EXTERNAL_FUNCTIONS = ra.EXTERNAL_FUNCTIONS
    cases.append(("neg_control_is_hollow", not check_distinct_neg(spec, nf)[0]))

    # 2 source 与机理不一致
    s = copy.deepcopy(spec)
    s["rack_assets"]["double_deep"]["source"] = "procedural:rack_gravity"
    cases.append(("source_mismatch", not check_resolve(s)[0]))

    # 3 未知机型
    s = copy.deepcopy(spec)
    s["rack_assets"]["__not_a_type__"] = copy.deepcopy(s["rack_assets"]["mobile"])
    cases.append(("unknown_type_added", not check_distinct(s)[0] or not check_resolve(s)[0]))

    # 4 默认布局混入 rack_type
    lay = jload(DATA / "layout.json")
    lay2 = copy.deepcopy(lay)
    lay2["warehouses"][0]["racks"][0]["rack_type"] = "double_deep"
    cases.append(("typed_default_layout", not check_default_off(spec, lay2)[0]))

    # 4b 锚点必须能抓漂移：counts 与结构指纹各注入一次
    fake = {"dispatch": {"a": 1}, "fallback": 0, "typed_racks": 2, "pallets_built": 3,
            "objects": 4, "meshes": 5, "glb_structure": "x",
            "glb": {"nodes": 6, "meshes": 7}, "layout_sha256": "L"}
    gold = {"anchors": _anchors(fake)}
    cases.append(("anchor_matches", compare_anchor(fake, gold)[0]))
    bad = copy.deepcopy(fake); bad["dispatch"] = {"a": 2}
    cases.append(("anchor_count_drift", not compare_anchor(bad, gold)[0]))
    bad = copy.deepcopy(fake); bad["glb_structure"] = "y"
    cases.append(("anchor_glb_drift", not compare_anchor(bad, gold)[0]))

    # 4c 母版：漏摆一个必须被抓
    summ = {"master_props": {n: 1 for n in REQUIRED_MASTERS},
            "master_props_unplaced": ["asset_gantry_crane"]}
    cases.append(("masters_ok", check_masters(summ)[0]))
    miss = {"master_props": {"asset_scanner_arch": 1},
            "master_props_unplaced": ["asset_gantry_crane"]}
    cases.append(("master_missing", not check_masters(miss)[0]))
    sneaky = {"master_props": {n: 1 for n in REQUIRED_MASTERS},
              "master_props_unplaced": []}
    cases.append(("gantry_sneaked_in", not check_masters(sneaky)[0]))

    # 5 设备开关被打开
    s = copy.deepcopy(spec)
    s.setdefault("activate", {})["equipment_dispatch"] = True
    cases.append(("equipment_switch_on", not check_default_off(s, lay)[0]))

    bad = [n for n, ok in cases if not ok]
    print("ASSET_DISPATCH_SELF_TEST %s" % ("PASS" if not bad else "FAIL"))
    for n, ok in cases:
        print("  %-22s %s" % (n, "ok" if ok else "MISSED"))
    return 0 if not bad else 1


# ---------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser(description="R8.2 门：货架派发与几何分型")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--typed-build", action="store_true",
                    help="带 rack_type 的整场构建并与 tests/golden/typed_3d.json 比对（约 60 s）")
    ap.add_argument("--freeze", action="store_true",
                    help="与 --typed-build 连用：把本次结果写成新的锚点（受控重冻结，需人看着跑）")
    ap.add_argument("--probe", action="store_true",
                    help="跑/校验 Blender 渲染层探针（约 10 s，需要本机 Blender）")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()

    spec = _racs()
    gates, failed = {}, []
    for name, fn in (("RACK_MESH_DISTINCT_OK", lambda: check_distinct(spec)),
                     ("RACK_MESH_DISTINCT_NEG", lambda: check_distinct_neg(spec)),
                     ("DISPATCH_RESOLVE_OK", lambda: check_resolve(spec)),
                     ("DISPATCH_WIRED_OK", lambda: check_wired()),
                     ("DEFAULT_OFF_OK", lambda: check_default_off(spec))):
        try:
            ok, detail, problems = fn()
        except Exception as e:                                    # noqa: BLE001
            ok, detail, problems = False, {}, ["门执行异常：%r" % (e,)]
        gates[name] = {"ok": ok, "detail": detail, "problems": problems}
        if not ok:
            failed.append(name)
        if not a.quiet:
            print("%-26s %s%s" % (name, "PASS" if ok else "FAIL",
                                  "" if ok else "  -> " + "; ".join(problems[:2])))
            if ok:
                print("                           %s"
                      % json.dumps(detail, ensure_ascii=False)[:220])

    if a.probe:
        ok_run, why = run_probe()
        if not ok_run:
            gates["RACK_MESH_PROBE_OK"] = {"ok": False, "detail": {}, "problems": [why]}
            failed.append("RACK_MESH_PROBE_OK")
            if not a.quiet:
                print("%-26s FAIL  -> %s" % ("RACK_MESH_PROBE_OK", why))
        else:
            ok, detail, problems = check_probe()
            gates["RACK_MESH_PROBE_OK"] = {"ok": ok, "detail": detail, "problems": problems}
            if not ok:
                failed.append("RACK_MESH_PROBE_OK")
            if not a.quiet:
                print("%-26s %s%s" % ("RACK_MESH_PROBE_OK", "PASS" if ok else "FAIL",
                                      "" if ok else "  -> " + "; ".join(problems[:2])))

    if a.typed_build:
        summ, why = run_typed_build()
        if summ is None:
            gates["TYPED_BUILD_OK"] = {"ok": False, "detail": {}, "problems": [why]}
            failed.append("TYPED_BUILD_OK")
            if not a.quiet:
                print("%-26s FAIL  -> %s" % ("TYPED_BUILD_OK", why))
        else:
            anchors = _anchors(summ)
            if a.freeze:
                TYPED_GOLDEN.parent.mkdir(parents=True, exist_ok=True)
                TYPED_GOLDEN.write_text(json.dumps(
                    {"schema_version": "1.0", "kind": "typed_3d_golden",
                     "note": "带 rack_type 的整场：counts + GLB 结构指纹双锚（字节哈希不可复现，见 ADR-12）。默认构建的锚点不在这里。",
                     "anchors": anchors}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                ok, detail, problems = True, {"frozen": str(TYPED_GOLDEN.relative_to(ROOT))}, []
            elif TYPED_GOLDEN.exists():
                ok, detail, problems = compare_anchor(summ, jload(TYPED_GOLDEN))
            else:
                ok, detail, problems = False, {}, [
                    "没有 tests/golden/typed_3d.json（确认结果符合预期后加 --freeze 冻结一次）"]
            gates["TYPED_BUILD_OK"] = {"ok": ok, "detail": detail, "problems": problems}
            mok, mdetail, mproblems = check_masters(summ)
            gates["IDLE_MASTER_WIRED_OK"] = {"ok": mok, "detail": mdetail,
                                             "problems": mproblems}
            if not mok:
                failed.append("IDLE_MASTER_WIRED_OK")
            if not a.quiet:
                print("%-26s %s%s" % ("IDLE_MASTER_WIRED_OK", "PASS" if mok else "FAIL",
                                      "" if mok else "  -> " + "; ".join(mproblems[:2])))
            if not ok:
                failed.append("TYPED_BUILD_OK")
            if not a.quiet:
                print("%-26s %s%s" % ("TYPED_BUILD_OK", "PASS" if ok else "FAIL",
                                      "" if ok else "  -> " + "; ".join(problems[:2])))
                print("                           %s" % json.dumps(anchors, ensure_ascii=False)[:220])

    rep = {"schema_version": "1.0", "kind": "asset_dispatch_report",
           "verdict": "ASSET_DISPATCH_OK" if not failed else "ASSET_DISPATCH_FAIL",
           "failed": failed, "gates": gates,
           "note": "几何指纹=盒子数+尺寸/材质槽摘要；不依赖渲染。设备母版接线（idle master）"
                   "本轮只做到'接进构建'，摆放属 R8.3。"}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "asset_dispatch_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.quiet:
        print("%s %s" % (rep["verdict"], ",".join(failed) if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
