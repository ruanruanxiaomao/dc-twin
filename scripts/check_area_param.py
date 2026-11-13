#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R8.6 门 · 厂房面积参数化（``AREA_PARAM_OK``）。

方案书 §16.8 说"改 `wh.size` 应该让壳体/货架格/导航格自动重算"，并把它列为 G8 的
核心。壳体与货架排布确实早就参数化了（§16.1 有取证），但**"参数化"和"参数真的
传导过去"是两件事**——R4 的"0 巷道却全绿"就是在这条缝里栽的。

三道子门：

* ``AREA_PARAM_OK``    放大面积 → `grid.cols/rows` 按 cell_size 线性变、货架数不减、
                       货位随之变；三项必须同时成立，只变壳不变格不算。
* ``AREA_SHRINK_NEG``  缩到装不下 → L2/L7 必须给 issue 且 **退出码非 0**（拒绝，不是告警）。
* ``AREA_RACK_FOLLOW_OK`` 货架与库位数量随面积单调不减（放大不能反而更少）。

自检：把"放大后的 spec"换成"原样 spec"，门必须自己判红——证明它真的在比对，
而不是把恒等式写成断言。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG, OUT = ROOT / "config", ROOT / "out"
TMP = OUT / "exp" / "area"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _run(spec, tag):
    """写 spec → 跑 spec_layout → 返回 (returncode, layout, report)。"""
    import subprocess
    TMP.mkdir(parents=True, exist_ok=True)
    sp = TMP / ("%s_spec.json" % tag)
    sp.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out = TMP / ("%s_layout.json" % tag)
    rep = TMP / ("%s_report.json" % tag)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "spec_layout.py"),
                        "--spec", str(sp), "--out", str(TMP / ("%s_spec_layout.json" % tag)),
                        "--report", str(rep), "--write", str(out), "--quiet"],
                       cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 and not rep.exists():
        raise RuntimeError("spec_layout 崩溃（tag=%s）：%s"
                           % (tag, ((r.stdout or "") + (r.stderr or ""))[-400:]))
    alt = TMP / ("%s_spec_layout.json" % tag)
    if out.exists():
        layout = jload(out)
    elif alt.exists():
        # 校验拒绝时 --write 不落盘（这是对的：越界不许写进 data/），
        # 但门仍需要看到"被拒的那份布局"才能归因，所以从 --out 读。
        layout = jload(alt)
    else:
        layout = None
    report = jload(rep) if rep.exists() else {}
    return r.returncode, layout, report


def _resize(spec, width, depth):
    s = copy.deepcopy(spec)
    wh = s["warehouses"][0]
    wh.setdefault("size", {})["width"] = width
    wh["size"]["depth"] = depth
    return s


def _reflow_from_size(spec):
    """打开"进深预算来自厂房尺寸"：这样面积变化才会真的改变行数/货位。

    不加这一条时，货架行数是 spec 里**写死的整数**——放大厂房只会变大壳子和导航格，
    库位一个不增。这正是 §16.8 缺口④剩下的那一半，所以门必须分别验两条链：
    面积 → 壳体/网格（恒成立），面积 → 库容（需要 reflow + from_size）。
    """
    s = copy.deepcopy(spec)
    s.setdefault("options", {})["reflow_rows"] = True
    s["options"]["area_reflow"] = True
    for wh in s["warehouses"]:
        # 基线是**功能区坐标对应的原始进深**。没有它求解器拒绝推断（shift=0），
        # 这是刻意的：猜一个常量会把四个仓一起改布局（我踩过）。
        wh.setdefault("area_baseline", {})["depth"] = wh["size"]["depth"]
        for g in wh["racks"]:
            g["depth_budget"] = "from_size"
    return s


def _reflow_bays(spec, width):
    """打开"跨数来自厂房宽度"（R8.10），并显式给宽度基线。

    基线必须**先记旧宽、再改新宽**：反过来记的话 `dx` 恒为 0，门会看到"宽度变了
    但待命位没动"——那正是第一版踩的坑，只是这次连红都红不出来（因为货架上限
    被待命位挡住，看起来"很守法"）。

    只给 W 仓声明 `width_budget`：前置仓宽度没变，它们必须一个数都不动，
    这同时是"默认关闭/未声明不生效"的对照。
    """
    s = copy.deepcopy(spec)
    for wh in s["warehouses"]:
        wh.setdefault("area_baseline", {})["width"] = wh["size"]["width"]
    w0 = s["warehouses"][0]
    w0["size"]["width"] = width
    for g in w0["racks"]:
        g["width_budget"] = "from_size"
    s.setdefault("options", {})["reflow_bays"] = True
    return s


def _grid(layout):
    w = layout["warehouses"][0]
    g = w.get("grid") or {}
    return g.get("cols"), g.get("rows"), g.get("cell_size"), len(w.get("racks") or []), \
        layout["summary"]["locations"]


def check_param(base_layout, big_layout):
    """面积 → 壳体/导航格：必须变；面积 → 货架/货位：**不许丢**。

    注意这里不断言"货位必须变多"。默认布局的货架行数是 spec 里写死的整数，放大厂房
    只会放大壳子与网格——想让它进库容，必须同时给进深预算（见 `check_fill`）。
    把"应该发生"写成断言，和把"没发生"写成通过一样糟，所以两者分开验。
    """
    problems = []
    if base_layout is None or big_layout is None:
        return False, {}, ["布局生成失败（没有 layout 可比）"]
    bc, br, cs, bra, blo = _grid(base_layout)
    gc, gr, gs, gra, glo = _grid(big_layout)
    if cs != gs:
        problems.append("cell_size 变了（%r → %r）：网格不是按同一精度重算" % (cs, gs))
    if (gc, gr) == (bc, br):
        problems.append("放大面积后 grid 没变（%s×%s）——壳体变了、导航格没变" % (bc, br))
    if gra < bra:
        problems.append("放大后货架数反而变少（%s → %s）" % (bra, gra))
    if glo < blo:
        problems.append("放大后货位数反而变少（%s → %s）" % (blo, glo))
    expect_cols = int(round(float(gc or 0)))
    return not problems, {"base": {"grid": [bc, br], "racks": bra, "locations": blo},
                          "big": {"grid": [gc, gr], "racks": gra, "locations": glo},
                          "cols": expect_cols}, problems


def check_fill(rc, layout, report, base_layout):
    """把进深预算设为"填满厂房"（depth_budget=from_size）。

    两种结局都算诚实，只有"悄悄什么都没变"和"崩溃"算失败：

    * 越界被拒（issues>0 / 退出码非 0）：说明**自动填满**撞上月台/输送线，面积要进
      库容必须配功能区重排（D10b 求解器）——这是当前布局的真实结论；
    * 通过且货位变多：说明这一版布局恰好容得下。

    门只拒绝"既没被拒绝、货位也没变"这种静默无效。
    """
    problems = []
    issues = (report or {}).get("issues") or []
    if rc != 0 or issues:
        return True, {"outcome": "rejected_by_validation", "issues": len(issues),
                      "rules": sorted({i.get("rule") for i in issues if isinstance(i, dict)})}, []
    if layout is None:
        problems.append("填满预算既没被拒也没产出布局")
        return False, {}, problems
    base_loc = base_layout["summary"]["locations"] if base_layout else 0
    if layout["summary"]["locations"] <= base_loc:
        problems.append("填满预算通过了，但货位没有增加（%s → %s）——静默无效"
                        % (base_loc, layout["summary"]["locations"]))
    return not problems, {"outcome": "fits", "locations": layout["summary"]["locations"],
                          "base": base_loc}, problems


#: 求解后允许残留的 issue 上限。R8.8 之后是 **0**：整场（北侧前置仓、场地道路、
#: 装卸位、叉车位）随建筑一起北移，放大布局不再有任何结构性冲突。
#:
#: 这个数曾经是 6，因为当时只重排了室内功能带，室外装备停在旧位置被圈进屋里。
#: 把它收回 0 才是"面积真的能交付"的证据；留着 6 等于给同一类问题永久开一道口子。
SOLVE_RESIDUAL_MAX = 0


def _agv_radius(spec):
    a = spec["defaults"]["agv"]
    return (a["length"] ** 2 / 4 + a["width"] ** 2 / 4) ** 0.5 + a["safety_margin"]


def _scene_equipment(spec):
    """取 scene 级装备（叉车/卡车）。它们不在 spec 里，是全局配置。"""
    try:
        import scene_config
        c = scene_config.load()["scene"]
    except Exception:                                            # noqa: BLE001
        return []
    fp = c.get("vehicle_footprint_m") or {}
    out = []
    for kind, key in (("forklift", "forklifts"), ("truck", "trucks")):
        fw, fd = fp.get(kind, [2.0, 1.0])
        for i, pt in enumerate(c.get(key) or [], 1):
            out.append({"id": "%s_%02d" % (kind.upper(), i), "kind": kind,
                        "x": float(pt[0]), "z": float(pt[1]),
                        "half": (fw / 2.0, fd / 2.0)})
    return out


def _loc_access_point(layout, loc_id):
    for w in (layout or {}).get("warehouses") or []:
        for l in w.get("locations") or []:
            if l.get("id") == loc_id:
                return l.get("access_point")
        for ch in w.get("charging_stations") or []:
            if ch.get("id") == loc_id:
                return ch.get("access_point")
    return None


def _explain_residual(issue, layout, spec):
    """给残留写**因果**归因：该访问点是否真的落在室外装备的膨胀范围内。

    旧版是子串匹配（message 含 SHELF/CHG 就归因"室外装备"），那是个掩蔽口——
    任何新的 SHELF/CHG L6 都会被同一条通道吞掉，门等于没在看。现在要求：
    1. issue 必须能定位到一个真实的访问点；
    2. 该点必须真的落在某台叉车/卡车的 footprint + AGV 半径内；
    3. 归因文案里要带**具体设备 id 与坐标**，人可复核。
    三条缺一条就返回 None，由 check_solve 判红。
    """
    msg = issue.get("message") or ""
    lid = msg.split()[0] if msg else ""
    ap = _loc_access_point(layout, lid)
    if not ap:
        return None
    try:
        r = _agv_radius(spec or jload(CONFIG / "layout_spec.json"))
    except Exception:                                            # noqa: BLE001
        return None
    for eq in _scene_equipment(spec or jload(CONFIG / "layout_spec.json")):
        hx, hz = eq["half"]
        x0, z0 = eq["x"] - hx - r, eq["z"] - hz - r
        x1, z1 = eq["x"] + hx + r, eq["z"] + hz + r
        if x0 <= float(ap[0]) <= x1 and z0 <= float(ap[1]) <= z1:
            return ("%s 的访问点 (%.1f, %.1f) 落在室外装备 %s (%.1f, %.1f) 的膨胀范围内"
                    "——装备层未重排，属 R8.8"
                    % (lid, ap[0], ap[1], eq["id"], eq["x"], eq["z"]))
    return None


def check_solve(rc, layout, report, base_layout, spec=None):
    """D10b 求解器：面积放大后必须**结构冲突清零**且**库容真增**。

    三条一起断言，少一条这门就是空的：

    1. L3（重叠）/ L4（AP 落进实体）**必须为 0** —— 这两类是"布局自相矛盾"，
       一条都不该留；
    2. issue 总量相对"未求解的 from_size"（1022 条）必须降 99% 以上 —— 证明求解器
       不是把冲突挪了个地方；
    3. 库位必须严格多于基准 —— 否则"求解成功"只是把越界换成了不增长。

    残留的 L6 允许存在，但**必须逐条归因**，且数量有上限。
    """
    problems = []
    issues = (report or {}).get("issues") or []
    rules = [i.get("rule") for i in issues if isinstance(i, dict)]
    # 校验层拒绝写盘是**正确**的（越界不许进 data/）。所以这里不要求 rc==0，
    # 而要求"被拒的理由必须是已知且已归因的那一类"。若拒绝理由里夹着 L3/L4
    # 或无法归因的条目，下面的断言会各自判红——门不是在给退出码放水。
    if rc != 0 and not issues:
        problems.append("求解被拒但没给任何 issue（拒绝理由都说不清）")
    for bad in ("L3", "L4"):
        if bad in rules:
            problems.append("%s 仍有 %d 条：结构冲突没清零" % (bad, rules.count(bad)))
    total = len(issues)
    if total > SOLVE_RESIDUAL_MAX:
        problems.append("残留 %d 条 issue，超过已归因上限 %d" % (total, SOLVE_RESIDUAL_MAX))
    unexplained = [i for i in issues
                   if isinstance(i, dict) and not _explain_residual(i, layout, spec)]
    if unexplained:
        problems.append("%d 条残留无法归因：%s"
                        % (len(unexplained), (unexplained[0].get("message") or "")[:40]))
    if layout is None:
        problems.append("求解没有产出布局")
        return False, {"issues": total}, problems
    base_loc = base_layout["summary"]["locations"] if base_layout else 0
    loc = layout["summary"]["locations"]
    if loc <= base_loc:
        problems.append("求解后库位没有增加（%s → %s）" % (base_loc, loc))
    shifts = [r.get("shift") for wh in layout["warehouses"]
              for r in (wh.get("area_reflow") or [])]
    if not any(abs(x or 0) > 1e-9 for x in shifts):
        problems.append("功能带没有平移（area_baseline 缺失或 shift=0）——面积没传导")
    return not problems, {"issues_before": 1022, "issues_after": total,
                          "rules": sorted(set(rules)),
                          "residual": [i.get("message") for i in issues][:8],
                          "locations": loc, "base_locations": base_loc,
                          "band_shifts": [round(x, 2) for x in shifts if x]}, problems


def check_deliver(rc, layout, report, base_layout):
    """放大后的布局必须**能真的交付**：0 issue、exit 0、写盘成功、场地跟着长大。

    这是 R8.8 的验收条件，也是"面积→库容"这句话的兑现口径。之前 AREA_SOLVE_OK
    只证明"算得出来"（1022→1），但那份布局被校验拒绝、写不进 data/——数字漂亮、
    交付不了。现在要求整份布局可落盘。
    """
    problems = []
    issues = (report or {}).get("issues") or []
    if issues:
        problems.append("仍有 %d 条 issue（要求 0）：%s"
                        % (len(issues), (issues[0].get("message") or "")[:50]))
    if rc != 0:
        problems.append("校验仍拒绝（退出码 %s）——布局写不进 data/" % rc)
    if layout is None:
        problems.append("没有产出布局")
        return False, {"issues": len(issues)}, problems
    base_loc = base_layout["summary"]["locations"] if base_layout else 0
    loc = layout["summary"]["locations"]
    if loc <= base_loc:
        problems.append("库位没有增加（%s → %s）" % (base_loc, loc))
    # 场地必须跟着长大：建筑北移而 site 不动，等于把停车场埋进屋里
    base_depth = (base_layout or {}).get("size", {}).get("depth") or 0
    depth = (layout.get("size") or {}).get("depth") or 0
    if depth < base_depth:
        problems.append("site 进深没跟着长（%s → %s）" % (base_depth, depth))
    fwd = [w["origin"][1] for w in layout["warehouses"][1:]]
    base_fwd = [w["origin"][1] for w in (base_layout or {}).get("warehouses", [])[1:]]
    if fwd and base_fwd and fwd[0] <= base_fwd[0]:
        problems.append("北侧前置仓没有北移（z %s → %s）——新墙会把它们圈进 W 里"
                        % (base_fwd[0], fwd[0]))
    return not problems, {"issues": 0, "locations": loc, "base_locations": base_loc,
                          "site_depth": depth, "base_site_depth": base_depth,
                          "forward_z": fwd[:3], "base_forward_z": base_fwd[:3]}, problems


def _bays_of(layout):
    """把一份布局的跨数与归因取出来：{zone: (bays, blocked_by)} + 待命位 x 列表。"""
    wh = next((w for w in layout["warehouses"] if w["id"] == "W"), None)
    if wh is None:
        return {}, []
    recs = {r["zone"]: (int(r.get("bays_derived") or 0), r.get("blocked_by") or "")
            for r in (wh.get("area_solve_bays") or [])}
    xs = sorted({round(float(r["position"][0]), 3) for r in (wh["spawn"] or {}).get("robots") or []})
    return recs, xs


def check_bays(rc, wide, report, narrow, base_layout):
    """R8.10 宽度轴：**只改宽度**，右列跨数必须真的跟着涨，左列必须一个都不动。

    为什么必须做两次（同宽 100 对照 vs 宽 120）：求解器一旦打开，**左列也会从
    声明的 10 跨填到 13 跨**——那是"填满已有空隙"，与宽度无关。只看一次 120 的
    结果，会把"本来就会发生的填满"当成"宽度红利"，门就成了摆设。所以基准是
    `narrow`（同样打开求解、宽度不变），只有 120 相对 100 的**增量**才是宽度的功劳。

    右列/左列不按 zone 名硬编码，按 `blocked_by` 判：被 `east_wall` 或某个
    `spawn_XX` 挡住的是右列（东墙/待命位），被 `sibling_zone_X` 挡住的是左列。
    布局改名时门不会跟着瞎。
    """
    problems = []
    issues = (report or {}).get("issues") or []
    if rc != 0:
        problems.append("宽度求解后校验仍拒绝（退出码 %s）——布局写不进 data/" % rc)
    if issues:
        problems.append("仍有 %d 条 issue（要求 0）：%s"
                        % (len(issues), (issues[0].get("message") or "")[:50]))
    if wide is None or narrow is None:
        problems.append("宽度求解没有产出布局")
        return False, {"issues": len(issues)}, problems
    wr, wxs = _bays_of(wide)
    nr, nxs = _bays_of(narrow)
    if not wr:
        problems.append("布局里没有 area_solve_bays 记录——宽度轴根本没接上")
        return False, {"wide": wr}, problems
    if set(wr) != set(nr):
        problems.append("两次求解的组集合不同（%s vs %s）" % (sorted(wr), sorted(nr)))
    east = [z for z, (_b, who) in wr.items()
            if who.startswith("east_wall") or who.startswith("spawn_")]
    west = [z for z, (_b, who) in wr.items() if who.startswith("sibling_zone_")]
    if not east:
        problems.append("没有任何组以东墙/待命位为上限——宽度根本没参与求解")
    gain, same = {}, {}
    for z in east:
        gain[z] = wr[z][0] - nr.get(z, (0, ""))[0]
        if gain[z] <= 0:
            problems.append("宽度 +20 后右列 zone=%s 跨数没涨（%s → %s）"
                            % (z, nr.get(z, (0, ""))[0], wr[z][0]))
    for z in west:
        same[z] = wr[z][0] - nr.get(z, (0, ""))[0]
        if same[z] != 0:
            problems.append("宽度 +20 后左列 zone=%s 跨数变了（%s → %s）"
                            "——宽度红利不该落到被兄弟组挡住的列"
                            % (z, nr.get(z, (0, ""))[0], wr[z][0]))
    if wxs and nxs and max(wxs) <= max(nxs):
        problems.append("待命位没跟着东墙走（x %s → %s）：货架会上来把发车点吃掉"
                        % (nxs[-1], wxs[-1]))
    wl = wide["summary"]["locations"]
    nl = narrow["summary"]["locations"]
    if wl <= nl:
        problems.append("库位没有增加（%s → %s）——宽度没进库容" % (nl, wl))
    return not problems, {"east_gain": gain, "west_delta": same,
                          "wide_bays": {z: wr[z][0] for z in wr},
                          "narrow_bays": {z: nr[z][0] for z in nr},
                          "spawn_x": {"narrow": nxs[-1:], "wide": wxs[-1:]},
                          "locations": {"narrow": nl, "wide": wl},
                          "issues": len(issues)}, problems


def check_shrink(rc, report):
    problems = []
    issues = report.get("issues") or []
    if rc == 0:
        problems.append("缩小到装不下却退出码 0（把越界当成了告警）")
    if not issues:
        problems.append("缩小到装不下没有产生任何 issue")
    rules = sorted({i.get("rule") for i in issues if isinstance(i, dict)})
    return not problems, {"returncode": rc, "issues": len(issues), "rules": rules}, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="R8.6 门：厂房面积参数化")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    spec = jload(CONFIG / "layout_spec.json")
    wh = spec["warehouses"][0]
    W, D = int(wh["size"]["width"]), int(wh["size"]["depth"])

    stale = set(TMP.glob("*_layout.json")) | set(TMP.glob("*_spec_layout.json")) \
        | set(TMP.glob("*_report.json"))
    for f in sorted(stale):
        f.unlink(missing_ok=True)       # 不留旧产物冒充本次结果
    rc0, base, _ = _run(spec, "base")
    rc1, big, rep1 = _run(_resize(spec, W + 20, D + 14), "big")
    rc3, fill, rep3 = _run(_resize(_reflow_from_size(spec), W + 20, D + 14), "fill")
    rc4, narrow_bays, rep4 = _run(_reflow_bays(spec, W), "bays_narrow")
    rc5, wide_bays, rep5 = _run(_reflow_bays(spec, W + 20), "bays_wide")
    rc2, small, rep2 = _run(_resize(spec, max(20, W // 3), max(20, D // 3)), "small")

    if a.self_test:
        # 反例：拿原样 spec 当"放大后"，check_param 必须自己判红
        ok_self, _, probs_self = check_param(base, base)
        ok_shrink, _, probs_shrink = check_shrink(0, {"issues": []})
        ok_fill, _, _ = check_fill(0, base, {"issues": []}, base)
        # 求解器负对照：拿"没求解过的基准布局"当求解结果，必须自己判红
        # （它没有 area_reflow 记录 → 面积没传导）
        ok_solve, _, _ = check_solve(0, base, {"issues": []}, base)
        # 求解器专用反例：门对"冲突没降下来"和"库位没涨"必须各自判红，
        # 否则 AREA_SOLVE_OK 只是把 from_size 的老结论换了个名字。
        fake_layout = copy.deepcopy(base)
        fake_layout["warehouses"][0]["area_reflow"] = [{"shift": 14.0}]
        bad_rules = {"issues": [{"rule": "L3", "message": "A vs B OVERLAP"}]}
        ok_bad_rules, _, _ = check_solve(0, fake_layout, bad_rules, base)
        no_gain = copy.deepcopy(fake_layout)
        no_gain["summary"]["locations"] = base["summary"]["locations"]
        ok_no_gain, _, _ = check_solve(0, no_gain, {"issues": []}, base)
        # 归因必须是因果的：随便编一条"离装备很远"的 SHELF L6，子串匹配会放行，
        # 因果判定必须判红——这是防掩蔽口的关键反例。
        if fill:
            far_id = next((l["id"] for w in fill["warehouses"]
                           for l in w.get("locations") or []), None)
            fake_issue = {"issues": [{"rule": "L6", "message": "%s AP not walkable" % far_id}]}
            ok_causal, _, _ = check_solve(0, fill, fake_issue, base, spec)
        # 交付门反例：拿"被拒的那份"喂进去必须红
        ok_deliver, _, _ = check_deliver(1, base, {"issues": [{"rule": "L6",
                                                               "message": "x"}]}, base)
        # 宽度轴负对照 1：拿"没求解过的基准布局"当求解结果 → 没有记录必须红
        ok_bays_none, _, _ = check_bays(0, base, {"issues": []}, base, base)
        # 宽度轴负对照 2：同样打开求解但**宽度没变**（两次都是 100）→ 右列没涨必须红。
        # 这条才是防"只要开了开关就绿"的关键：它证明门比的是增量，不是开关状态。
        ok_bays_flat, _, _ = check_bays(0, narrow_bays, {"issues": []},
                                        narrow_bays, base)
        good = ((not ok_self) and (not ok_shrink) and (not ok_fill)
                and (not ok_solve) and (not ok_bad_rules) and (not ok_no_gain)
                and (not ok_causal) and (not ok_deliver)
                and (not ok_bays_none) and (not ok_bays_flat)
                and rc0 == 0 and rc1 == 0 and rc4 == 0 and rc5 == 0)
        print("AREA_SELF_TEST %s" % ("PASS" if good else "FAIL"))
        print("  no_change_detected  %s" % ("ok" if not ok_self else "MISSED"))
        print("  silent_shrink       %s" % ("ok" if not ok_shrink else "MISSED"))
        print("  silent_fill         %s" % ("ok" if not ok_fill else "MISSED"))
        print("  solve_without_shift %s" % ("ok" if not ok_solve else "MISSED"))
        print("  solve_l3_not_cleared %s" % ("ok" if not ok_bad_rules else "MISSED"))
        print("  solve_no_capacity   %s" % ("ok" if not ok_no_gain else "MISSED"))
        print("  deliver_rejected    %s" % ("ok" if not ok_deliver else "MISSED"))
        print("  residual_attribution_causal %s"
              % ("ok" if not ok_causal else "MISSED"))
        print("  bays_not_solved     %s" % ("ok" if not ok_bays_none else "MISSED"))
        print("  bays_no_width_gain  %s" % ("ok" if not ok_bays_flat else "MISSED"))
        return 0 if good else 1

    gates, failed = {}, []
    for name, fn in (("AREA_PARAM_OK", lambda: check_param(base, big)),
                     ("AREA_FILL_OK", lambda: check_fill(rc3, fill, rep3, base)),
                     ("AREA_SOLVE_OK", lambda: check_solve(rc3, fill, rep3, base, spec)),
                     ("AREA_DELIVER_OK", lambda: check_deliver(rc3, fill, rep3, base)),
                     ("AREA_BAYS_OK", lambda: check_bays(rc5, wide_bays, rep5,
                                                         narrow_bays, base)),
                     ("AREA_SHRINK_NEG", lambda: check_shrink(rc2, rep2))):
        try:
            ok, detail, problems = fn()
        except Exception as e:                                    # noqa: BLE001
            ok, detail, problems = False, {}, ["门执行异常：%r" % (e,)]
        gates[name] = {"ok": ok, "detail": detail, "problems": problems}
        if not ok:
            failed.append(name)
        if not a.quiet:
            print("%-20s %s%s" % (name, "PASS" if ok else "FAIL",
                                  "" if ok else "  -> " + "; ".join(problems[:2])))
            if ok:
                print("                     %s" % json.dumps(detail, ensure_ascii=False)[:200])

    rep = {"schema_version": "1.0", "kind": "area_param_report",
           "verdict": "AREA_PARAM_OK" if not failed else "AREA_PARAM_FAIL",
           "failed": failed, "gates": gates,
           "sizes": {"base": [W, D], "big": [W + 20, D + 14],
                     "bays": {"narrow": [W, D], "wide": [W + 20, D]},
                     "small": [max(20, W // 3), max(20, D // 3)]},
           "note": "验五件事：参数传导到壳体/货架/导航格、越界必须拒绝、"
                   "D10b 求解器让\"填满进深\"变成 0 结构性冲突且库容真增、"
                   "残留 L6 必须逐条归因（场外装备未重排，属 R8.8）、"
                   "R8.10 宽度轴只对东列放跨（同宽对照做基准，待命位锚东墙）。"
                   "交互重摆（D10a 拖拽）未在此声称。"}
    TMP.mkdir(parents=True, exist_ok=True)
    (OUT / "area_param_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.quiet:
        print("%s %s" % (rep["verdict"], ",".join(failed) if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
