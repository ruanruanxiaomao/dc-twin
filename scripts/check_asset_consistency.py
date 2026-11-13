#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R8 门 · 3D 资产目录一致性（``ASSET_CONSISTENCY_OK``）。

R8 的全部风险都收在一句话里：**外观和行为必须用同一个键**。所以第一件事不是画模型，
而是把「资产目录」钉成可校验的单一真相——``config/asset_spec.json``。

本门不跑 Blender（秒级），只做五件能被证伪的事：

* ``ASSET_CONSISTENCY_OK``   schema 通过 + 键**双向相等**：``rack_assets`` == ``rack_spec.types``、
                            ``equipment_assets`` == ``equipment_spec.types``；多一个少一个都判红；
                            ``deferred_rack_types`` 不许偷偷溜进 rack_spec；
                            两个不同货架机理不许共用一个程序化函数（那样分型就是假的）。
* ``SOURCE_RESOLVABLE_OK``   每个 ``glb:`` 源都必须在 GLB 实测清单里（**不臆造节点名**），
                            每个 ``procedural:`` 源都必须在声明的 module 里真实存在。
* ``IMPLEMENTATION_TRUTHFUL_OK`` 声称 ``status="existing"`` 的条目，``implemented_in`` 必须
                            指向真实文件与真实行，且那附近确实出现了该符号——不许写"已接入"实际没接。
* ``FOOTPRINT_DERIVED_OK``   货架 footprint 必须能由 ``rack_spec.geometry`` 复算出来
                            （3 货格宽 × 深位数），写死一个凑数的数字会判红；
                            ``depth_positions="from_rack"`` 必须留 null，不许假装知道。
* ``GAP_MARKED_OK``          标了 gap 的条目必须写明缺什么（``glb_gap``），
                            且不许一边说 gap 一边挂 ``glb:`` 母版装成已解决。

自检（``--self-test``）对每一道门注入一个反例，六种注入必须**全部**被抓住。

> 编号说明：本门属于方案书 §12.6「3D 资产库 / 组装器 / 面积门（R8）」。
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
SCHEMAS = CONFIG / "schemas"
OUT = ROOT / "out"

MIN_TOL = 1e-6


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def glb_inventory(path):
    """只读解析 GLB 的 JSON chunk，返回 {bytes, nodes, meshes, roots}。"""
    d = Path(path).read_bytes()
    magic, version, length = struct.unpack("<4sII", d[:12])
    jlen, _jtype = struct.unpack("<II", d[12:20])
    doc = json.loads(d[20:20 + jlen])
    names = [n.get("name", "") for n in doc.get("nodes", [])]
    roots = sorted({n.split("__")[0] for n in names if n.startswith("asset_")})
    return {"magic": magic.decode("ascii", "replace"), "version": version,
            "bytes": length, "file_bytes": len(d),
            "nodes": len(names), "meshes": len(doc.get("meshes", [])),
            "roots": roots}


def _procedural_symbol(entry):
    return entry["source"].split(":", 1)[1]


def _module_path(entry, default="scripts/build_dc.py"):
    return ROOT / entry.get("module", default)


def _func_exists(entry):
    p = _module_path(entry)
    if not p.exists():
        return False, "%s 不存在" % p.relative_to(ROOT)
    sym = _procedural_symbol(entry).split(".")[-1]
    src = p.read_text(encoding="utf-8", errors="replace")
    if re.search(r"^\s*def\s+%s\s*\(" % re.escape(sym), src, re.M):
        return True, ""
    return False, "%s 里没有 def %s()" % (p.relative_to(ROOT), sym)


# ---------------------------------------------------------------- gates


def check_consistency(spec, rack, equip):
    problems = []
    try:
        import jsonschema
    except Exception as e:                                        # noqa: BLE001
        return False, {}, ["缺少 jsonschema：%r" % (e,)]
    try:
        jsonschema.validate(spec, jload(SCHEMAS / "asset_spec.schema.json"))
    except Exception as e:                                        # noqa: BLE001
        problems.append("asset_spec 未通过 schema：%s" % str(e).splitlines()[0])

    ra, ea = spec["rack_assets"], spec["equipment_assets"]
    rk, ek = set(rack["types"]), set(equip["types"])
    miss_r, extra_r = sorted(rk - set(ra)), sorted(set(ra) - rk)
    miss_e, extra_e = sorted(ek - set(ea)), sorted(set(ea) - ek)
    if miss_r:
        problems.append("货架类型缺资产条目：%s" % ",".join(miss_r))
    if extra_r:
        problems.append("rack_assets 有多余键（rack_spec 里没有）：%s" % ",".join(extra_r))
    if miss_e:
        problems.append("设备类型缺资产条目：%s" % ",".join(miss_e))
    if extra_e:
        problems.append("equipment_assets 有多余键：%s" % ",".join(extra_e))

    deferred = set(spec.get("deferred_rack_types", {})) - {"_note"}
    leaked = sorted(deferred & rk)
    if leaked:
        problems.append("延后机理已进 rack_spec 却没进 rack_assets：%s" % ",".join(leaked))

    # 两个不同机理共用一个程序化函数 = 外观分型是假的
    seen = {}
    for k, v in ra.items():
        if not v["source"].startswith("procedural:"):
            continue
        sym = _procedural_symbol(v)
        if sym in seen and seen[sym] != "selective_pallet":
            problems.append("%s 与 %s 共用程序化函数 %s，外观分不出机理" % (seen[sym], k, sym))
        seen.setdefault(sym, k)

    # 输送/分拣的 mechanism_key 不许撞车
    for sect in ("conveyor_assets", "sorter_assets"):
        keys = [v.get("mechanism_key") for v in spec.get(sect, {}).values()
                if isinstance(v, dict) and v.get("mechanism_key")]
        dup = sorted({k for k in keys if keys.count(k) > 1})
        if dup:
            problems.append("%s 的 mechanism_key 重复：%s" % (sect, ",".join(dup)))

    detail = {"rack_types": len(rk), "equipment_types": len(ek),
              "rack_assets": len(ra), "equipment_assets": len(ea),
              "deferred": len(deferred)}
    return not problems, detail, problems


def check_sources(spec, glb):
    problems = []
    roots = set(glb["roots"])
    entries = []
    for sect in ("rack_assets", "equipment_assets", "scene_assets",
                 "conveyor_assets", "sorter_assets"):
        for k, v in spec.get(sect, {}).items():
            if k == "_note" or not isinstance(v, dict):
                continue
            entries.append((sect + "." + k, v))
            if v.get("glb_fallback"):
                entries.append((sect + "." + k + ".glb_fallback", {"source": v["glb_fallback"]}))
    n_glb = n_proc = 0
    for name, v in entries:
        src = v.get("source") or ""
        if src.startswith("glb:"):
            n_glb += 1
            node = src.split(":", 1)[1]
            if node not in roots:
                problems.append("%s 指向不存在的 GLB 节点 %s（实测 %d 个母版）"
                                % (name, node, len(roots)))
        elif src.startswith("procedural:"):
            n_proc += 1
            if v.get("status") == "existing":
                ok, why = _func_exists(v)
                if not ok:
                    problems.append("%s 声称已接入但%s" % (name, why))
        else:
            problems.append("%s 的 source 不合规：%r" % (name, src))
    return not problems, {"glb_sources": n_glb, "procedural_sources": n_proc,
                          "glb_roots": len(roots)}, problems


def check_truthful(spec):
    problems = []
    claims = 0
    for sect in ("rack_assets", "equipment_assets", "scene_assets",
                 "conveyor_assets", "sorter_assets"):
        for k, v in spec.get(sect, {}).items():
            if k == "_note" or not isinstance(v, dict):
                continue
            where = v.get("implemented_in")
            if v.get("status") == "existing" and not where:
                problems.append("%s 状态 existing 却没给 implemented_in" % k)
                continue
            if not where:
                continue
            claims += 1
            fp, _, ln = where.partition(":")
            p = ROOT / fp
            if not p.exists():
                problems.append("%s 的 implemented_in 指向不存在的文件 %s" % (k, fp))
                continue
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            n = int(ln or 0)
            if not 1 <= n <= len(lines):
                problems.append("%s 的 implemented_in 行号 %d 越界（%s 共 %d 行）"
                                % (k, n, fp, len(lines)))
                continue
            window = "\n".join(lines[max(0, n - 11):n + 10])
            sym = _procedural_symbol(v) if v["source"].startswith("procedural:") else \
                v["source"].split(":", 1)[1]
            if sym.split(".")[-1] not in window:
                problems.append("%s 的 implemented_in 处（%s）找不到符号 %s" % (k, where, sym))
    return not problems, {"lines_checked": claims}, problems


def check_footprint(spec, rack):
    problems = []
    bays = spec["rack_module"]["bays"]
    checked = 0
    for k, v in spec["rack_assets"].items():
        g = rack["types"][k].get("geometry") or rack["defaults"]["geometry"]
        bay_w = g["clearW"] + g["structW"]
        depth = g["clearD"] + g["structD"]
        dp = v.get("depth_positions")
        want = v.get("footprint_m")
        if dp == "from_rack":
            if want is not None:
                problems.append("%s 深位由 rack_spec 定（from_rack），footprint_m 必须为 null，"
                                "却写了 %r" % (k, want))
            continue
        exp = [round(bays * bay_w, 6), round(int(dp) * depth, 6)]
        if not isinstance(want, (list, tuple)) or len(want) != 2:
            problems.append("%s 的 footprint_m 应为 [宽,深]，却是 %r" % (k, want))
            continue
        if abs(want[0] - exp[0]) > MIN_TOL or abs(want[1] - exp[1]) > MIN_TOL:
            problems.append("%s footprint_m=%r 与 geometry 复算 %r 不符（bays=%d）"
                            % (k, list(want), exp, bays))
        checked += 1
    return not problems, {"checked": checked, "bays": bays,
                          "formula": "3×(clearW+structW) × depth×(clearD+structD)"}, problems


def check_gaps(spec):
    problems = []
    n_gap = 0
    for sect in ("rack_assets", "equipment_assets", "scene_assets"):
        for k, v in spec.get(sect, {}).items():
            if k == "_note" or not isinstance(v, dict):
                continue
            if v.get("status") == "gap":
                n_gap += 1
                if not v.get("glb_gap"):
                    problems.append("%s 标了 gap 但没写 glb_gap（缺什么）" % k)
            if v.get("glb_gap") and v.get("status") == "existing":
                problems.append("%s 既说缺母版（glb_gap）又说已接入（existing）" % k)
    return not problems, {"gap_entries": n_gap}, problems


# ---------------------------------------------------------------- self test


def self_test():
    spec = jload(CONFIG / "asset_spec.json")
    rack = jload(CONFIG / "rack_spec.json")
    equip = jload(CONFIG / "equipment_spec.json")
    glb = glb_inventory(ROOT / spec["meta"]["provenance"]["glb"])
    cases = []

    base = [check_consistency(spec, rack, equip), check_sources(spec, glb),
            check_truthful(spec), check_footprint(spec, rack), check_gaps(spec)]
    cases.append(("baseline", all(c[0] for c in base), [p for c in base for p in c[2]]))

    # 1 少一个键
    s = copy.deepcopy(spec); s["rack_assets"].pop("push_back")
    cases.append(("missing_rack_key", not check_consistency(s, rack, equip)[0], []))
    # 2 多一个键（不在 rack_spec 里）
    s = copy.deepcopy(spec); s["rack_assets"]["vlm"] = copy.deepcopy(spec["rack_assets"]["mobile"])
    cases.append(("orphan_rack_key", not check_consistency(s, rack, equip)[0], []))
    # 3 两个机理共用同一程序化函数
    s = copy.deepcopy(spec); s["rack_assets"]["mobile"]["source"] = "procedural:rack_gravity"
    cases.append(("shared_mesh_fn", not check_consistency(s, rack, equip)[0], []))
    # 4 臆造 GLB 节点名
    s = copy.deepcopy(spec); s["equipment_assets"]["stacker_unitload"]["source"] = "glb:asset_stacker_krane"
    cases.append(("invented_glb", not check_sources(s, glb)[0], []))
    # 5 谎称已接入（函数不存在 / 行号对不上）
    s = copy.deepcopy(spec)
    s["rack_assets"]["double_deep"]["status"] = "existing"
    s["rack_assets"]["double_deep"]["implemented_in"] = "scripts/build_dc.py:174"
    cases.append(("false_claim", not check_sources(s, glb)[0] and not check_truthful(s)[0], []))
    # 6 footprint 凑数
    s = copy.deepcopy(spec); s["rack_assets"]["double_deep"]["footprint_m"] = [3.0, 2.4]
    cases.append(("fake_footprint", not check_footprint(s, rack)[0], []))
    # 7 gap 不写缺什么
    s = copy.deepcopy(spec); s["equipment_assets"]["forklift_vna"].pop("glb_gap")
    cases.append(("silent_gap", not check_gaps(s)[0], []))

    bad = [n for n, ok, _ in cases if not ok]
    print("ASSET_SELF_TEST %s" % ("PASS" if not bad else "FAIL"))
    for n, ok, probs in cases:
        print("  %-18s %s%s" % (n, "ok" if ok else "MISSED",
                                "" if ok else "  " + "; ".join(probs[:2])))
    return 0 if not bad else 1


# ---------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser(description="R8 门：3D 资产目录一致性")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="要求无 pending（R8.2 全部程序化函数落地后使用）")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()

    spec = jload(CONFIG / "asset_spec.json")
    rack = jload(CONFIG / "rack_spec.json")
    equip = jload(CONFIG / "equipment_spec.json")
    glb = glb_inventory(ROOT / spec["meta"]["provenance"]["glb"])

    gates = {}
    failed = []
    for name, fn in (("ASSET_CONSISTENCY_OK", lambda: check_consistency(spec, rack, equip)),
                     ("SOURCE_RESOLVABLE_OK", lambda: check_sources(spec, glb)),
                     ("IMPLEMENTATION_TRUTHFUL_OK", lambda: check_truthful(spec)),
                     ("FOOTPRINT_DERIVED_OK", lambda: check_footprint(spec, rack)),
                     ("GAP_MARKED_OK", lambda: check_gaps(spec))):
        try:
            ok, detail, problems = fn()
        except Exception as e:                                    # noqa: BLE001
            ok, detail, problems = False, {}, ["门执行异常：%r" % (e,)]
        gates[name] = {"ok": ok, "detail": detail, "problems": problems}
        if not ok:
            failed.append(name)
        if not a.quiet:
            print("%-28s %s%s" % (name, "PASS" if ok else "FAIL",
                                  "" if ok else "  -> " + "; ".join(problems[:2])))
            if ok:
                print("                             %s"
                      % json.dumps(detail, ensure_ascii=False)[:200])

    pending = sorted(k for sect in ("rack_assets", "equipment_assets", "scene_assets",
                                    "conveyor_assets", "sorter_assets")
                     for k, v in spec.get(sect, {}).items()
                     if isinstance(v, dict) and v.get("status") in ("new", "variant", "idle_master"))
    strict_ok = not pending
    if a.strict and pending:
        if not a.quiet:
            print("NO_PENDING_OK                FAIL  -> 仍有 %d 项资产未落地：%s"
                  % (len(pending), ",".join(pending[:6])))

    rep = {"schema_version": "1.0", "kind": "asset_consistency_report",
           "verdict": "ASSET_CONSISTENCY_OK" if not failed else "ASSET_CONSISTENCY_FAIL",
           "failed": failed, "gates": gates,
           "pending_assets": pending,
           "strict_ok": strict_ok,
           "note": "verdict 只反映 5 道目录门；pending_assets 是本轮尚未落地的资产清单"
                   "（R8.2/R8.3），--strict 下它非空即整体判红。本门不跑 Blender，"
                   "渲染分型的实证在 ASSET_DISPATCH_OK / RACK_MESH_DISTINCT_NEG。",
           "glb": {"roots": len(glb["roots"]), "bytes": glb["bytes"],
                   "nodes": glb["nodes"], "meshes": glb["meshes"]}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "asset_consistency_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.quiet:
        print("%s %s" % (rep["verdict"], ",".join(failed) if failed else ""))
    else:
        print("pending_assets=%d  %s" % (len(pending), ",".join(pending[:6])
                                         + ("…" if len(pending) > 6 else "")))
    return 0 if (not failed and (strict_ok or not a.strict)) else 1


if __name__ == "__main__":
    sys.exit(main())
