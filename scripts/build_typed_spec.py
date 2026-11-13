#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""D11-A：生成"带分型"的关卡/编辑器布局变体（默认出厂不受影响）。

## 它解决什么

R8.2–R8.5 把 9 类货架、4 型输送、7 型分拣的 3D 几何做出来了，但**出厂布局一个
`rack_type` 都没声明**（这是 D4/D5 的默认关闭原则），所以玩家/评审在 3D 里看到的
永远是同一种货架。D11 要"看得见分型"，A 方案是：

* 出厂 `config/layout_spec.json` **保持冻结**（金标准一字节不动）；
* 另出一份 `config/layout_spec_typed.json`，只在**关卡/编辑器预览**里用。

## 为什么是脚本生成而不是手写

手写的 12 组拆分与 9 类分配，改一个 origin 就会和 `bays × pitch` 对不上，而
`spec_layout` 只会在 L3 里报"重叠"——没人能一眼看出是"算错了"还是"写错了"。
脚本把分配规则写死，输出与文件逐字节比对（`--check`），手改立刻红。

## 分配规则（确定性，无 rng）

W 仓 4 组各拆 2 组（bays 平分），每组一类机理，共 8 类；
F1 补第 9 类（push_back），F2/F3 保持 selective（与出厂同形，便于对照）。
输送线 2 条分别 belt / roller；W 的分拣站声明 cross_belt；F 仓输送线补
gravity / accumulation —— 四型在场景里各有实例。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
SRC = CONFIG / "layout_spec.json"
DST = CONFIG / "layout_spec_typed.json"

#: W 仓四个组的拆分与机理分配（按 zone 名，顺序即左右）。
SPLIT = {
    "A": ("selective_pallet", "double_deep"),
    "B": ("drive_in", "drive_through"),
    "C": ("pallet_shuttle", "mobile"),
    "D": ("carton_flow", "pallet_gravity"),
}
#: 前置仓补的机理（F2/F3 不声明，保持出厂同形）。
FORWARD = {"F1": "push_back"}
#: 输送线分型（按声明顺序循环取）。
CV_STYLES = ("belt", "roller", "gravity", "accumulation")


def build(src=None):
    spec = copy.deepcopy(json.loads(Path(src or SRC).read_text(encoding="utf-8")))
    spec.setdefault("options", {})["typed_showcase"] = True
    pitch_cache = {}
    cv_i = 0        # 输送分型按**全仓**顺序循环，否则每个仓都从 belt 重新开始，
                    # 四型永远凑不齐（第一版就是这么写成 2 型的）。
    for wh in spec["warehouses"]:
        racks = wh.get("racks") or []
        if wh["id"] == "W":
            new = []
            for g in racks:
                types = SPLIT.get(g.get("zone"))
                if not types:
                    new.append(g)
                    continue
                bays = int(g.get("bays", 1))
                half = bays // 2
                if half < 1 or bays - half < 1:
                    raise SystemExit("组 %s 的 bays=%d 不足以拆成两组" % (g.get("zone"), bays))
                rl = float((g.get("rack") or {}).get("length")
                           or spec["defaults"]["rack"]["length"])
                gap = float(g.get("bay_gap", 0.6))
                pitch = rl + gap
                pitch_cache[wh["id"]] = pitch
                for k, rt in enumerate(types):
                    gl = copy.deepcopy(g)
                    gl["origin"] = [round(float(g["origin"][0]) + k * half * pitch, 6),
                                    g["origin"][1]]
                    gl["bays"] = half if k == 0 else bays - half
                    gl["zone"] = g["zone"] + str(k + 1)
                    gl.setdefault("rack", {})["rack_type"] = rt
                    # 深位类机理要有明确的 lane_depth，否则展开成 4 深位纯属默认值运气
                    if rt in ("drive_in", "drive_through", "pallet_shuttle",
                              "pallet_gravity", "push_back"):
                        gl["lane_depth"] = int((spec.get("rack_spec_lane_depth") or {}).get(rt, 4))
                    new.append(gl)
            wh["racks"] = new
        else:
            rt = FORWARD.get(wh["id"])
            if rt:
                for g in wh.get("racks") or []:
                    g.setdefault("rack", {})["rack_type"] = rt
                    g["lane_depth"] = int((spec.get("rack_spec_lane_depth") or {}).get(rt, 4))
        # 输送线分型：按顺序循环，保证四型都出现
        # 前置仓没有输送线，W 只有 2 条 → 要想让 4 型都在场，把每条按中点拆成两段。
        # 拆段不新增几何风险（仍在同一条直线上），代价是场景里多两个对象。
        cvs = []
        for cv in wh.get("conveyors") or []:
            path = cv["path"]
            if len(path) == 2:
                (x0, z0), (x1, z1) = path
                mx, mz = (float(x0) + float(x1)) / 2.0, (float(z0) + float(z1)) / 2.0
                for seg, seg_path in enumerate(([[x0, z0], [mx, mz]],
                                                [[mx, mz], [x1, z1]])):
                    c = copy.deepcopy(cv)
                    c["id"] = "%s%d" % (cv["id"], seg + 1)
                    c["path"] = seg_path
                    c["style"] = CV_STYLES[cv_i % len(CV_STYLES)]
                    cv_i += 1
                    cvs.append(c)
            else:
                cv["style"] = CV_STYLES[cv_i % len(CV_STYLES)]
                cv_i += 1
                cvs.append(cv)
        wh["conveyors"] = cvs
        # 分拣站分型：SORTING 站声明交叉带
        for st in wh.get("stations") or []:
            if st.get("kind") == "SORTING":
                st["sorter"] = "crossbelt"
                st["conveyor_style"] = "roller"
    # 只保留运行时真的读的键，避免把 lane_depth 表写进 spec
    spec.pop("rack_spec_lane_depth", None)
    return spec


def main(argv=None):
    ap = argparse.ArgumentParser(description="D11-A：生成带分型的关卡布局变体")
    ap.add_argument("--out", default=str(DST))
    ap.add_argument("--check", action="store_true",
                    help="只比对（文件不存在或不一致时退出码 1）")
    a = ap.parse_args(argv)
    doc = build()
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    out = Path(a.out)
    if a.check:
        if not out.exists():
            print("TYPED_SPEC_MISSING %s" % out)
            return 1
        cur = out.read_text(encoding="utf-8")
        if cur != text:
            print("TYPED_SPEC_DRIFT 手改过 %s（请重跑本脚本）" % out)
            return 1
        print("TYPED_SPEC_OK %s" % out)
        return 0
    out.write_text(text, encoding="utf-8", newline="\n")
    n = sum(len(w.get("racks") or []) for w in doc["warehouses"])
    types = {g.get("rack", {}).get("rack_type")
             for w in doc["warehouses"] for g in w.get("racks") or []}
    print("TYPED_SPEC_WRITTEN %s racks=%d groups=%d types=%d"
          % (out, n, sum(len(w["racks"]) for w in doc["warehouses"]),
             len([t for t in types if t])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
