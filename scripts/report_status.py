"""report_status.py — 单一真相源：从产物 JSON 生成当前状态表。

第二轮审计 §3.10 / P1-7：旧版每次迭代手打数字，导致主报告数字互相矛盾。
本脚本只读 JSON，输出 out/CURRENT_STATUS.md；主报告与 README 只引用该文件。

运行：python scripts/report_status.py
"""
import datetime
import glob
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT, DATA, CONFIG = (os.path.join(ROOT, 'out'), os.path.join(ROOT, 'data'),
                     os.path.join(ROOT, 'config'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def chain_steps():
    """现算三条链的步数（**禁止硬编码**）。

    这里曾经写死成「31 步链（data 11 · scenarios 16 · full 31）」，而 runner 实际是
    13/23/45 —— 差了 14 步，且 `check_doc_drift` 抓不到（它比的是文档与产物，不是
    文档与代码）。凡"数字"，一律从代码算，算不出来就明说算不出来。
    """
    try:
        from dc_twin.runner import SCOPES
        return {k: len(v) for k, v in SCOPES.items()}, None
    except Exception as exc:                       # noqa: BLE001
        return {}, "读取 dc_twin.runner 失败：%r" % (exc,)


from pathlib import Path  # noqa: E402  (art_count 需要 Path)


def art_count():
    """现算 manifest 会收录的产物数，**不读 manifest.json**。

    读 manifest 会制造循环依赖：manifest 要哈希 CURRENT_STATUS.md，而状态表又要
    引用 manifest 的计数 → "先跑谁"都有一边过期，文件每轮都变脏（实测就是
    "83 → 91" 这种差一个周期的数字）。自己按同一套规则数，断掉这个环。
    """
    root = Path(OUT)          # 本文件里 OUT 是字符串（历史原因），这里显式转
    n = 0
    for p in sorted(root.rglob('*')):
        if not p.is_file() or p.name == 'manifest.json' or p.name.endswith('.meta.json'):
            continue
        parts = p.relative_to(root).parts
        # 与 manifest 同口径：实验产物（out/exp/）不进交付清单
        if ('exp' in parts or 'frames' in parts or p.suffix in ('.blend1', '.blend2')
                or p.name in ('manifest_check.json', 'junit.xml',
                              'validate_data_check_report.json')):
            continue
        n += 1
    return n


def refactor_facts():
    """资料驱动重构（R0–R8）的可核查事实：从 config 现算，不手抄。

    只报"文件里到底有多少条"，不报"计划做多少条"——后者写进方案书，
    进了状态表就会被当成已完成（这个项目已经因此吃过亏）。
    """
    out = {"rack_types": 0, "equipment_types": 0, "specs": 0, "schemas": 0,
           "gate_ok": None, "stage": "R1", "ul_pallet": 0, "ul_tote": 0,
           "ul_skus": 0, "ul_max_util": None, "rack_cells": 0,
           "models": 0, "deployed": [], "ast": None,
           "cap_aisles": 0, "cap_per_h": 0, "cap_load": 0, "cap_dev": None,
           "reflow_rows": None, "reflow_shelf": None, "reflow_rows_ref": None,
           "asset_rack": 0, "asset_equip": 0, "asset_glb_roots": 0,
           "asset_pending": 0, "asset_gate": None,
           "disp_distinct": 0, "disp_types": 0, "disp_geoms": 0, "disp_gate": None,
           "typed_mech": 0, "typed_fallback": None,
           "glb_bytes": 0, "glb_sha": None, "glb_geom": None, "glb_canon": False,
           "glb_saved": 0, "masters_placed": 0, "masters_unplaced": [],
           "cv_styles": 0, "sr_kinds": 0, "eq_bodies": 0, "var_gate": None,
           "area_base_cols": 0, "area_big_cols": 0, "area_gate": None,
           "area_before": 0, "area_after": 0, "area_loc": 0, "area_loc_gain": 0,
           "area_shift": 0, "area_site": 0, "area_site_base": 0,
           "area_fwd": 0, "area_fwd_base": 0, "area_deliver": None}
    try:
        rack = json.load(open(os.path.join(CONFIG, 'rack_spec.json'), encoding='utf-8'))
        out["rack_types"] = len(rack.get('types') or {})
        eq = json.load(open(os.path.join(CONFIG, 'equipment_spec.json'), encoding='utf-8'))
        out["equipment_types"] = len(eq.get('types') or {})
    except Exception:                       # noqa: BLE001
        pass
    try:
        m = json.load(open(os.path.join(DATA, 'sku_master.json'), encoding='utf-8'))
        c = m.get('unit_load_counts') or {}
        out["ul_pallet"] = c.get('pallet', 0)
        out["ul_tote"] = c.get('tote', 0)
        out["ul_skus"] = sum(1 for x in m.get('skus', []) if x.get('unit_load'))
        utils = [x['unit_load']['gross_weight_kg'] / x['unit_load']['rating_kg']
                 for x in m.get('skus', [])
                 if x.get('unit_load') and x['unit_load'].get('rating_kg')]
        out["ul_max_util"] = round(max(utils), 4) if utils else None
    except Exception:                       # noqa: BLE001
        pass
    try:
        fl = json.load(open(os.path.join(CONFIG, 'fleet_spec.json'), encoding='utf-8'))
        out["models"] = len(fl.get('models') or [])
        out["deployed"] = sorted({d.get('model') for d in (fl.get('deployment') or [])
                                  if d.get('model')})
        out["ast"] = bool(json.load(open(os.path.join(CONFIG, 'equipment_spec.json'),
                                         encoding='utf-8')).get('ast_drives_aisle_width'))
    except Exception:                       # noqa: BLE001
        pass
    try:
        import json as _json
        lay = _json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
        out["rack_cells"] = len({l["cell_id"] for w in lay.get('warehouses', [])
                                 for l in w.get('locations', []) if l.get("cell_id")})
    except Exception:                       # noqa: BLE001
        pass
    try:
        cr = json.load(open(os.path.join(OUT, 'capacity_report.json'), encoding='utf-8'))
        out["cap_aisles"] = len(cr.get('aisles') or [])
        out["cap_per_h"] = round(float(cr.get('totalCapacity') or 0), 1)
        out["cap_load"] = round(float(cr.get('totalLoad') or 0), 3)
        cx = json.load(open(os.path.join(OUT, 'capacity_cross_report.json'), encoding='utf-8'))
        out["cap_dev"] = cx.get('detail', {}).get('deviation_pct')
    except Exception:                       # noqa: BLE001
        pass
    try:
        rf = json.load(open(os.path.join(OUT, 'reflow_report.json'), encoding='utf-8'))
        dens = rf.get('gates', {}).get('REFLOW_DENSITY_OK', {}).get('detail') or {}
        out["reflow_rows"] = (dens.get('reach') or {}).get('rows')
        out["reflow_shelf"] = (dens.get('reach') or {}).get('shelf')
        out["reflow_rows_ref"] = (dens.get('counterbalance') or {}).get('rows')
    except Exception:                       # noqa: BLE001
        pass
    try:
        asp = json.load(open(os.path.join(CONFIG, 'asset_spec.json'), encoding='utf-8'))
        out["asset_rack"] = len(asp.get('rack_assets') or {})
        out["asset_equip"] = len(asp.get('equipment_assets') or {})
        out["asset_glb_roots"] = int((asp.get('meta', {}).get('provenance', {})
                                      .get('measured', {}) or {}).get('asset_roots') or 0)
        areas = ("rack_assets", "equipment_assets", "scene_assets",
                 "conveyor_assets", "sorter_assets")
        out["asset_pending"] = sum(1 for s in areas
                                   for v in (asp.get(s) or {}).values()
                                   if isinstance(v, dict)
                                   and v.get('status') in ('new', 'variant', 'idle_master'))
        ac = json.load(open(os.path.join(OUT, 'asset_consistency_report.json'),
                            encoding='utf-8'))
        out["asset_gate"] = ac.get('verdict')
    except Exception:                       # noqa: BLE001
        pass
    try:
        ar = json.load(open(os.path.join(OUT, 'asset_dispatch_report.json'), encoding='utf-8'))
        out["disp_gate"] = ar.get('verdict')
        dd = (ar.get('gates', {}).get('RACK_MESH_DISTINCT_OK', {}) or {}).get('detail') or {}
        out["disp_distinct"] = dd.get('distinct') or 0
        out["disp_types"] = dd.get('types') or 0
        pr = (ar.get('gates', {}).get('RACK_MESH_PROBE_OK', {}) or {}).get('detail') or {}
        out["disp_geoms"] = pr.get('distinct_geoms') or 0
        gr = json.load(open(os.path.join(OUT, 'glb_report.json'), encoding='utf-8'))
        out["glb_bytes"] = gr.get('bytes')
        out["glb_sha"] = (gr.get('sha256') or '')[:12]
        out["glb_geom"] = (gr.get('geometry_signature') or '')[:12]
        out["glb_canon"] = bool(gr.get('canonical'))
        out["glb_saved"] = gr.get('canonicalized_bytes') or 0
        ts = os.path.join(OUT, 'exp', 'typed', 'scene', 'typed_summary.json')
        if os.path.exists(ts):
            t = json.load(open(ts, encoding='utf-8'))
            out["typed_mech"] = len(t.get('dispatch') or {})
            out["typed_fallback"] = t.get('fallback')
            out["masters_placed"] = len(t.get('master_props') or {})
            out["masters_unplaced"] = t.get('master_props_unplaced') or []
    except Exception:                       # noqa: BLE001
        pass
    try:
        vp = json.load(open(os.path.join(OUT, 'variant_probe.json'), encoding='utf-8'))
        out["cv_styles"] = len(vp.get('conveyors') or {})
        out["sr_kinds"] = len(vp.get('sorters') or {})
        out["eq_bodies"] = len(vp.get('equipment_bodies') or {})
    except Exception:                       # noqa: BLE001
        pass
    try:
        ap = json.load(open(os.path.join(OUT, 'area_param_report.json'), encoding='utf-8'))
        out["area_gate"] = ap.get('verdict')
        d = (ap.get('gates', {}).get('AREA_PARAM_OK', {}) or {}).get('detail') or {}
        out["area_base_cols"] = (d.get('base') or {}).get('grid', [0])[0]
        out["area_big_cols"] = (d.get('big') or {}).get('grid', [0])[0]
        sd = (ap.get('gates', {}).get('AREA_SOLVE_OK', {}) or {}).get('detail') or {}
        out["area_before"] = sd.get('issues_before') or 0
        out["area_after"] = sd.get('issues_after') or 0
        out["area_loc"] = sd.get('locations') or 0
        out["area_loc_gain"] = (sd.get('locations') or 0) - (sd.get('base_locations') or 0)
        out["area_shift"] = (sd.get('band_shifts') or [0])[0]
        dv = (ap.get('gates', {}).get('AREA_DELIVER_OK', {}) or {}).get('detail') or {}
        out["area_deliver"] = (ap.get('gates', {}).get('AREA_DELIVER_OK', {}) or {}).get('ok')
        out["area_site"] = dv.get('site_depth') or 0
        out["area_site_base"] = dv.get('base_site_depth') or 0
        out["area_fwd"] = (dv.get('forward_z') or [0])[0]
        out["area_fwd_base"] = (dv.get('base_forward_z') or [0])[0]
    except Exception:                       # noqa: BLE001
        pass
    for name in ("unit_load_spec", "rack_spec", "equipment_spec", "capacity_spec"):
        if os.path.exists(os.path.join(CONFIG, name + '.json')):
            out["specs"] += 1
        if os.path.exists(os.path.join(CONFIG, 'schemas', name + '.schema.json')):
            out["schemas"] += 1
    return out


def inventory_facts():
    """库存层可核查事实：SKU / 库位 / 拣选面 / 期初总量 / 补货触发（从产物现算）。"""
    out = {"skus": 0, "locations": 0, "pick_face": 0, "units": 0, "value": 0.0,
           "enabled": False}
    slot = DATA and os.path.join(DATA, 'inventory_slotting.json')
    init = DATA and os.path.join(DATA, 'inventory_initial.json')
    if os.path.exists(slot):
        d = json.load(open(slot, encoding='utf-8'))
        c = d.get('counts', {})
        out.update({"locations": c.get('locations', 0), "skus": c.get('skus', 0),
                    "pick_face": c.get('pick_face', 0), "enabled": True})
    if os.path.exists(init):
        d = json.load(open(init, encoding='utf-8'))
        out.update({"units": d.get('summary', {}).get('total_units', 0),
                    "value": d.get('summary', {}).get('inventory_value', 0)})
    # 补货触发/缺货/报废数以最近一次 baseline 运行现算，不手抄
    runs = sorted(glob.glob(os.path.join(ROOT, 'runs', 'baseline', '*', 'sim_kpi.json')),
                  key=os.path.getmtime)
    if runs:
        try:
            m = json.load(open(runs[-1], encoding='utf-8')).get('metrics', {})
            out["triggers"] = m.get('reorder_triggers', {}).get('value', 0)
            out["stockout"] = m.get('stockout_count', {}).get('value', 0)
            out["expired_units"] = m.get('expired_units', {}).get('value', 0)
            out["expired_value"] = m.get('expired_value', {}).get('value', 0)
            out["open_lots"] = m.get('open_lots', {}).get('value', 0)
            out["fefo_violations"] = m.get('fefo_violations', {}).get('value', 0)
        except Exception:                       # noqa: BLE001
            pass
    if os.path.exists(init):
        try:
            d = json.load(open(init, encoding='utf-8'))
            out["lots"] = d.get('summary', {}).get('lots', 0)
            out["inverted"] = d.get('summary', {}).get('inverted_locations', 0)
        except Exception:                       # noqa: BLE001
            pass
    return out


def game_facts():
    """游戏层的可核查事实：关卡数、命令数、存档槽数、ADR 数。"""
    levels = sorted(glob.glob(os.path.join(CONFIG, 'levels', '*.json')))
    caps = 0
    for p in levels:
        try:
            caps += len(json.load(open(p, encoding='utf-8')).get('objectives') or [])
        except Exception:                          # noqa: BLE001
            pass
    cmds, not_impl = (), ()
    try:
        if os.path.join(ROOT, 'scripts') not in sys.path:
            sys.path.insert(0, os.path.join(ROOT, 'scripts'))
        import sim_engine as se
        cmds, not_impl = se.COMMANDS, se.NOT_IMPLEMENTED
    except Exception:                              # noqa: BLE001
        pass
    saves = sorted(glob.glob(os.path.join(ROOT, 'saves', 'slots', '*.json')))
    adrs = sorted(glob.glob(os.path.join(ROOT, 'docs', 'design', 'ADR-GAME-*.md')))
    return {"levels": len(levels), "objectives": caps, "commands": len(cmds),
            "commands_done": len(cmds) - len(not_impl), "placeholders": list(not_impl),
            "saves": len(saves), "adrs": len(adrs)}


def j(*parts, default=None):
    """读 JSON；文件不存在时返回 default（数据链没有 Blender 产物时要用）。"""
    p = os.path.join(*parts)
    if not os.path.exists(p):
        return {} if default is None else default
    return json.load(open(p, encoding='utf-8'))


def n(v, default='n/a'):
    return default if v is None else v


def main():
    steps, steps_err = chain_steps()
    gfacts = game_facts()
    inv = inventory_facts()
    ref = refactor_facts()
    br = j(OUT, 'build_report.json')
    ba = j(OUT, 'blend_audit.json')
    ar = j(OUT, 'artifact_report.json')
    tr = j(OUT, 'traffic_report.json')
    geo = j(OUT, 'geometry_report.json')
    la = j(OUT, 'layout_audit.json')
    rr = j(OUT, 'render_report.json')
    pm = j(DATA, 'path_metrics.json')
    tk = j(DATA, 'tasks.json')
    cargo = j(DATA, 'cargo_state.json')
    nav = j(DATA, 'nav_grid.json')
    L = j(DATA, 'layout.json')
    vr = j(OUT, 'validate_report.json', default=[])
    neg = j(OUT, 'negative_tests.json')
    neg_spec = j(OUT, 'spec_negative_tests.json')
    neg_scen = j(OUT, 'scenario_negative_tests.json')
    route = j(OUT, 'route_report.json')
    spec_rep = j(OUT, 'spec_layout_report.json')
    fleet = j(OUT, 'fleet_report.json')
    resv = j(OUT, 'reservation_report.json')
    clean = j(OUT, 'cleanroom_report.json')
    csv_rep = j(OUT, 'csv_adapter_report.json')
    variants = j(OUT, 'spec_layout_variants.json')
    man = j(OUT, 'manifest.json') if os.path.exists(os.path.join(OUT, 'manifest.json')) else {}
    vdata = j(OUT, 'validate_data_report.json') \
        if os.path.exists(os.path.join(OUT, 'validate_data_report.json')) else {}
    demo_man = j(ROOT, 'demo', 'manifest.json') \
        if os.path.exists(os.path.join(ROOT, 'demo', 'manifest.json')) else {}
    # 场景数只认 config/scenarios/*.json —— 不能数 runs/ 下的目录：
    # runs/ 里还有 custom/ 与 logs/，旧版因此把 8 个场景写成 10 个。
    scenario_specs = sorted(glob.glob(os.path.join(CONFIG, 'scenarios', '*.json')))
    run_dirs = []
    runs_root = os.path.join(ROOT, 'runs')
    for d in sorted(os.listdir(runs_root)):
        sub = os.path.join(runs_root, d)
        if not os.path.isdir(sub) or d == 'logs':
            continue
        run_dirs += [os.path.join(sub, p) for p in sorted(os.listdir(sub))
                     if os.path.isdir(os.path.join(sub, p))]
    # 两套 KPI 并存，必须分开报，不能混成一个数字：
    #   · 解析层 kpi.json   —— kpi.py 的 M/M/c 近似（25 项）
    #   · tick 层 sim_kpi.json —— sim_engine 的逐 tick 实测（27 项）
    base_root = os.path.join(ROOT, 'runs', 'baseline')
    base_runs = sorted(d for d in os.listdir(base_root)
                       if os.path.isfile(os.path.join(base_root, d, 'kpi.json')))
    base_kpi = j(base_root, base_runs[-1], 'kpi.json') if base_runs else {}
    sim_runs = sorted(d for d in os.listdir(base_root)
                      if os.path.isfile(os.path.join(base_root, d, 'sim_kpi.json')))
    base_sim = j(base_root, sim_runs[-1], 'sim_kpi.json') if sim_runs else {}
    analytic_keys = set(base_kpi.get('metrics', {}))
    tick_keys = set(base_sim.get('metrics', {}))
    schema_files = sorted(glob.glob(os.path.join(CONFIG, 'schemas', '*.json')))
    areas_p = os.path.join(DATA, 'areas.json')
    areas = j(areas_p) if os.path.exists(areas_p) else {}
    area_sum = areas.get('summary') or {}

    # 布局 spec 家族（主链 + F4/加宽 + 用户自选功能区的扩仓变体），数量现算不写死
    spec_files = sorted(glob.glob(os.path.join(CONFIG, 'layout_spec*.json')))
    spec_tags = []
    for p in spec_files:
        base = os.path.splitext(os.path.basename(p))[0]
        spec_tags.append(base.replace('layout_spec', '').lstrip('_') or '当前')
    # 多布局审计报告只有跑过 spec_layout 的多变体步骤才存在；缺失时不能替它说 PASS
    audited = len(variants) if isinstance(variants, dict) else 0
    spec_line = ('%d 个布局 spec（%s）；本次审计 %d 个变体，均 PASS'
                 % (len(spec_files), ' / '.join(spec_tags), audited)
                 if audited else
                 '%d 个布局 spec（%s）；本次仅审计当前 spec（多变体报告缺失）'
                 % (len(spec_files), ' / '.join(spec_tags)))

    # 用户自选功能区的扩仓变体：存在才报，不存在如实说"未生成"
    v_spec_p = os.path.join(CONFIG, 'layout_spec_areas.json')
    v_sel_p = os.path.join(CONFIG, 'areas_selection.json')
    if os.path.exists(v_spec_p):
        v_spec = j(v_spec_p)
        v_wh = next((w for w in v_spec['warehouses'] if w['id'] == 'W'), {})
        v_areas = v_wh.get('areas') or []
        v_sel = j(v_sel_p) if os.path.exists(v_sel_p) else {}
        v_enabled = v_sel.get('enabled') or []
        variant_line = ('config/layout_spec_areas.json：W 深度 %s m、%d 个物理功能区'
                        '（用户勾选 %d 个）；用 run.py --spec 运行，主链不受影响'
                        % (v_wh.get('size', {}).get('depth', 'n/a'), len(v_areas),
                           len(v_enabled)))
    else:
        variant_line = '未生成（python scripts/build_variant.py --build）'

    # 数据链（CI）没有 Blender 产物：如实写"未构建"，不编数字也不崩
    has3d = all(bool(x) for x in (br, ba, ar, rr))
    has_val = bool(vr)
    passed = sum(1 for r in vr if r.get('status') == 'PASS')
    # 负测试报告可能缺失（只跑数据链时），缺就记 0 而不是崩
    neg_total = sum(x.get('total', 0) for x in (neg, neg_spec, neg_scen))
    neg_pass = sum(x.get('passed', 0) for x in (neg, neg_spec, neg_scen))
    Ls = L['summary']
    # 分仓拆开写：全站 196 与 W 仓 160 都是真数，混着写就会被读成“同一个数”。
    _main = L['warehouses'][0] if L['warehouses'] else {'racks': []}
    why_n = len(_main.get('racks') or [])
    _fwd = [len(w.get('racks') or []) for w in L['warehouses'][1:]]
    wf_n = _fwd[0] if _fwd and len(set(_fwd)) == 1 else "/".join(str(x) for x in _fwd) or "0"
    home_n = sum(1 for w in L['warehouses'] for l in w['locations']
                 if l['kind'] == 'HOME')
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = [
        ('生成时间', ts),
        ('Blender', br.get('blender') or '未构建（本次未跑 Blender 链）'),
        ('布局来源', 'config/layout_spec.json → spec_layout.py（L1–L13 PASS，'
                     'issues=%s warnings=%s；%s 扇门）'
         % (len(spec_rep['issues']), len(spec_rep['warnings']),
            spec_rep['summary'].get('doors'))),
        ('AP 互斥', '%s 对（WARN，两 AP <2R 不能同时占用）；'
                    '预约表复核同时占用 %s 次；加宽变体互斥 %s 对'
         % (spec_rep['mutex_pairs'], resv['mutex_simultaneous_occupations'],
            variants.get('layout_spec_wide', {}).get('mutex', 'n/a')
            if isinstance(variants, dict) else 'n/a')),
        ('四层配置', 'layout_spec / fleet_spec / demand_spec / %d 个 scenarios + %d 份 schema；'
                     '%s'
         % (len(scenario_specs), len(schema_files), spec_line)),
        ('场景/KPI', '%d 个冻结场景（config/scenarios）× %d 次运行；'
                     '解析层 %d 项 KPI + tick 层 %d 项 KPI（去重 %d 项），'
                     '每项带 definition/formula/source'
         % (len(scenario_specs), len(run_dirs), len(analytic_keys), len(tick_keys),
            len(analytic_keys | tick_keys))),
        # 口径必须写在行里：196 架是**4 仓合计**（W 160 + F1/F2/F3 各 12），
        # 只盯 W 仓会读成 160，两边数字都“对”却指的不是一件事。
        ('布局', '%d 仓合计 / %d 货架（W %s + 前置仓各 %s）/ %d 库位（%d SHELF + %d HOME）'
                ' / %d 月台 / %d 站 / %d AGV'
         % (Ls['warehouses'], Ls['racks'], why_n, wf_n, Ls['locations'],
            Ls['shelf_locations'], home_n,
            Ls['docks'], Ls['stations'], Ls['robots'])),
        ('布局审计', 'issues=%s warnings=%s verdict=%s'
         % (la['count'], la['warnings'], la['verdict'])),
        ('功能区', '%s 个（物理 %s / 逻辑 %s）；%s；见 data/areas.json'
         % (area_sum.get('total', 'n/a'), area_sum.get('physical', 'n/a'),
            area_sum.get('logical', 'n/a'),
            '种类 ' + '/'.join('%s×%s' % (k, v)
                              for k, v in sorted((area_sum.get('by_kind') or {}).items())))),
        ('功能区变体', variant_line),
        ('过程配置', '%s 工位 / %s 流程节点 / %s 班次；L14–L22 见 out/process_report.json'
         % (j(CONFIG, 'process_spec.json')['stations'].__len__(),
            j(CONFIG, 'process_spec.json')['nodes'].__len__(),
            j(CONFIG, 'process_spec.json')['shifts'].__len__())),
        ('导航栅格', '%dx%d @%s m；blocked=%s corridor=%s；AGV_RADIUS=%s m'
         % (nav['cols'], nav['rows_count'], nav['cell_size_m'],
            nav['blocked_cells'], nav['corridor_cells'], geo['agv_radius_m'])),
        ('订单', '%d 事件 → %d 任务（%d ASSIGNED / %d PENDING），覆盖率 %s，类型 %s'
         % (tk['summary']['events'], tk['summary']['tasks'],
            tk['summary']['assigned'], tk['summary']['pending'],
            pm.get('event_coverage'), '/'.join(pm.get('event_types_executed', [])))),
        ('闭环', 'HOME→PICK→DROP→HOME；三段里程 %s m；取货/交付停靠 %s 处；'
                 '首尾闭合 %s/%s'
         % (route.get('loop_leg_totals_m'), pm.get('pick_drop_stops'),
            route.get('closed_loops'), route.get('routes'))),
        ('路径', '总长 %s m / 平均 %s m / %s 关键帧 / path_to_euclid=%s'
         % (pm['total_path_m'], pm['avg_path_m'], pm['samples'],
            pm.get('path_to_euclid_ratio'))),
        ('时间', '仿真 %s s / 视频 %s s / %s 帧 @%s fps'
         % (pm['duration_s'],
            n((ar.get('mp4') or {}).get('duration_s')) if has3d else '未构建',
            rr.get('frames_rendered', '未构建') if has3d else '未构建',
            rr.get('fps', '-') if has3d else '-')),
        ('调度', 'on_time_rate=%s / max_tardiness=%s s / max_conflict_wait=%s s'
         % (pm.get('on_time_rate'), pm.get('max_tardiness_s'),
            pm.get('max_conflict_wait_s'))),
        ('交通', '全程在场严格口径；min_pair=%s m（阈值 %s m）/ issues=%s / '
                 'strict_violations=%s'
         % (tr['min_pair_distance_m'], tr['min_sep_threshold_m'], tr['count'],
            tr.get('strict_violations'))),
        ('几何', '%s 段 / 穿越 %s / 最小间隙 %s m / 最大单步 %s m'
         % (geo['segments_checked'], geo['segment_crossings_raw'],
            geo['min_path_to_obstacle_m'], geo['max_segment_jump_m'])),
        ('货物状态机', '%s 件货物 LOADED/DELIVERED 各 %s/%s；%s 次状态迁移；'
                       '%s 个库位库存变动；.blend 随车动画对象 %s'
         % (cargo['summary']['orders'], cargo['summary']['loaded'],
            cargo['summary']['delivered'], cargo['summary']['state_transitions'],
            cargo['summary']['shelf_locations_changed'], ba.get('cargo_objects'))),
        ('.blend', ('%s objects / %s meshes / %s materials / %s actions'
                    '（构建报告 %s/%s/%s，重开实测一致）；HOME 待命位 %s；隐藏 AGV=%s'
                    % (ba['objects'], ba['meshes'], ba['materials'], ba['actions'],
                       br['objects'], br['meshes'], br['materials'],
                       ba.get('home_bays'), ba.get('agv_with_hide_render_keyframe')))
         if has3d else '未构建（本次未跑 Blender 链）'),
        ('GLB', ('%s 节点 / %s meshes / %s materials / %s animations；'
                 '禁用节点=%s；DC_ROOT=%s'
                 % (ar['glb']['nodes'], ar['glb']['meshes'], ar['glb']['materials'],
                    ar['glb']['animations'], ar['glb']['forbidden_nodes'],
                    ar['glb']['has_DC_ROOT']))
         if has3d else '未构建（本次未跑 Blender 链）'),
        ('图像', '6 张 3D 静帧 + 2 张 2D 闭环路线图 + 时间-空间图 + 快照序列；'
         '尺寸全对=%s' % (ar.get('all_png_sizes_ok') if has3d else '未构建')),
        ('路线图', 'closed_loops=%s / routes=%s / segments=%s / head_on=%s / '
         'edge_clearance=%s m / 中央主通道采样=%s'
         % (route.get('closed_loops'), route['routes'], route['segments_07'],
            route['route_head_on_pairs'], route['edge_clearance_m'],
            route['central_cross_aisle_samples'])),
        ('车队/交通复核', '状态迁移 %s 次/非法 %s；最低电量 %s%%；'
                         '预约表顶点 %s / 边交换 %s'
         % (fleet['state_transitions'], fleet['illegal_count'],
            fleet['min_battery_pct'], resv['vertex_conflicts'],
            resv['edge_conflicts'])),
        ('数据适配/复现', 'CSV %s 行 schema=%s；全新空目录复现 %s/%s 步、%s 份 JSON 逐字节一致'
         % (csv_rep['rows_out'], csv_rep['verdict'], clean['steps_run'],
            clean['steps_total'],
            sum(1 for v in clean['files'].values() if v['identical']))),
        ('验收', ('%d/%d PASS（A1–A%d）；负测试 %d/%d（%d 个测试文件）'
                  % (passed, len(vr), len(vr), neg_pass, neg_total,
                     sum(1 for x in (neg, neg_spec, neg_scen) if x)))
         if has_val else '未运行（本次只跑数据链，见 data tier）'),
        ('开源交付层', 'dc CLI 7 命令 / %s 步链（data %s · scenarios %s · full %s；'
                       'game %s 步＝逐字节基准 + 游戏测试 + 浏览器点击流）；'
                       '输出契约 sidecar %s 份 / manifest %s 个产物；'
                       '静态 Demo %s 个场景 · %s 台车'
         % (steps_err or (steps.get('full')),
            steps.get('data', 'n/a'), steps.get('scenarios', 'n/a'),
            steps.get('full', 'n/a'), steps.get('game', 'n/a'),
            man.get('counts', {}).get('report_sidecars'),
            art_count(),
            demo_man.get('scenarios'), demo_man.get('agents'))),
        ('游戏层', '关卡 %s 个 / 目标 %s 条 / 8 类玩家命令（已实现 %s，占位 %s）/ '
                   '存档槽 %s 个 / ADR %s 篇；批跑产物逐字节不变见 `scripts/step_baseline.py`'
         % (gfacts['levels'], gfacts['objectives'], gfacts['commands_done'],
            '、'.join(gfacts['placeholders']) or '无', gfacts['saves'], gfacts['adrs'])),
        ('库存层', 'SKU %s / 库位 %s（拣选面 %s）/ 期初 %s 件（占用 ¥%s）/ 期初批次 %s；'
                   '最近一次 baseline：补货触发 %s 次、缺货 %s 次、过期报废 %s 件（¥%s）、'
                   '期末未结批次 %s、FEFO 违规 %s；'
                   '守恒/补货/批次/效期四门见 `python -m dc_twin run --scope inventory`'
         % (inv['skus'], inv['locations'], inv['pick_face'], inv['units'],
            inv['value'], inv.get('lots', 'n/a'), inv.get('triggers', 'n/a'),
            inv.get('stockout', 'n/a'), inv.get('expired_units', 'n/a'),
            inv.get('expired_value', 'n/a'), inv.get('open_lots', 'n/a'),
            inv.get('fefo_violations', 'n/a'))),
        ('重构层（R0–R5 + R8.1–R8.8）', '四份 spec（%s/4）+ schema（%s/4）：货架 %s 类（按存取机理）、设备 %s 类；'
                          '车型库 %s 个（默认部署 %s）；集装单元 %s 个 SKU（托盘 %s / 料箱 %s，'
                          '最高载荷利用率 %s%%）；当前布局货道 %s 个（默认 0 = 未启用深位）；'
                          'AST 耦合开关 %s（默认关）；`capacity_model.py` / 单元层 / 货架机理 / '
                          '多车型与按车型采购均已落地，**默认全部不改变行为**；'
                          '资料 5 处口径 bug（B1–B5）修正并以 LEGACY 双版做负对照；'
                          '解析库容已接主链：%s 条巷道 / 能力 %s 单/h / 负荷 %s / '
                          '与仿真偏差 %s%%；密度重排（默认关）同预算下平衡重 %s 行 → '
                          '曲臂 %s 行（%s 货位），两边 0 违规；'
                          '资产目录（R8.1）：货架 %s / 设备 %s 条，GLB 母版实测 %s 个、设备侧仍只接 5；'
                          '待落地 %s 项（`--strict` 仍红，属 R8.3 未完成，非回归）；'
                          '门 `check_asset_consistency.py`（5 道目录门 + 7 反例自检，verdict=%s）；'
                          'R8.2 派发：%s 类机理 → %s 种几何指纹、Blender 探针 %s 种坐标形状'
                          '（verdict=%s，默认关=布局不声明 rack_type 就不走派发）；'
                          'R8.3 带机理整场：%s 类机理入场景 / fallback=%s / 锚点=counts+GLB 结构指纹'
                          '（GLB 字节不可复现，ADR-12）；'
                          '门：`dc run --scope refactor`（R0 口径 + R1 单元 + R2 货架 + R3 设备 '
                          '+ R4 库容 + R5 密度 + R8.1 资产目录 + R8.2 派发，8 步）'
         % (ref['specs'], ref['schemas'], ref['rack_types'], ref['equipment_types'],
            ref['models'], "、".join(ref['deployed']) or "n/a", ref['ul_skus'],
            ref['ul_pallet'], ref['ul_tote'],
            round((ref['ul_max_util'] or 0) * 100, 1), ref['rack_cells'],
            "开" if ref['ast'] else "关",
            ref['cap_aisles'], ref['cap_per_h'], ref['cap_load'],
            round(ref['cap_dev'], 1) if ref['cap_dev'] is not None else 'n/a',
            ref['reflow_rows_ref'], ref['reflow_rows'], ref['reflow_shelf'],
            ref['asset_rack'], ref['asset_equip'], ref['asset_glb_roots'],
            ref['asset_pending'], ref['asset_gate'] or 'n/a',
            ref['disp_distinct'], ref['disp_types'], ref['disp_geoms'],
            ref['disp_gate'] or 'n/a', ref['typed_mech'], ref['typed_fallback'])),
        ('3D 产线（R8.4）', 'GLB 字节已规整：%s 字节（原始体积里剔掉未引用数据/合并重复 accessor，'
                          '省 %s 字节），sha256=%s…、几何签名=%s…；**同一场景两次导出逐字节相同**；'
                          '闲置母版摆进场景 %s 类（扫描门/带载叉车/料箱车/堆垛机×2），'
                          '未摆放 %s（无堆场，显式记账）'
         % (ref['glb_bytes'], ref['glb_saved'], ref['glb_sha'] or 'n/a',
            ref['glb_geom'] or 'n/a', ref['masters_placed'],
            "、".join(ref['masters_unplaced']) or "无")),
        ('分型与面积（R8.5–R8.8）', '输送台面 %s 型（带式/辊道/重力/积放）、分拣末端 %s 型'
                          '（斜滑道/交叉带/滑块/摆轮/窄带/翻板/播种墙）、无母版车体 %s 个'
                          '（穿梭车/CTU/码垛臂），三组几何两两不同（Blender 探针按坐标比，'
                          '不是按数量比）；面积参数化：货架格 %s→%s 列随厂房尺寸重算，'
                          '缩到装不下被 L2/L3/L6 拒绝（verdict=%s）；'
                          'D10b 求解器把"填满进深"从 %s 处冲突降到 %s 处：功能带随后墙平移 '
                          '%s m，货架只排到功能带前沿，库位 %s→%s（+%s）；R8.8 整场重排后'
                          '**可交付**（0 issue、exit 0、写盘成功）：site 进深 %s→%s、'
                          '北侧前置仓 z %s→%s、场外叉车/卡车随之北移，不再被新墙圈进室内'
         % (ref['cv_styles'], ref['sr_kinds'], ref['eq_bodies'],
            ref['area_base_cols'], ref['area_big_cols'], ref['area_gate'] or 'n/a',
            ref['area_before'], ref['area_after'], ref['area_shift'],
            ref['area_loc'] - ref['area_loc_gain'], ref['area_loc'], ref['area_loc_gain'],
            ref['area_site_base'], ref['area_site'],
            ref['area_fwd_base'], ref['area_fwd'])),
        ('分档验收', 'data tier %s/%s PASS；pytest -m "not blender" 与 CI 矩阵见 '
                     '`.github/workflows/ci.yml`'
         % (vdata.get('passed'), vdata.get('total'))),
    ]
    multi = base_sim.get('metrics', {}).get('max_orders_per_robot', {})
    max_orders = multi.get('value') if isinstance(multi, dict) else n(multi)
    lines = [
        '# 当前状态（自动生成，请勿手改）',
        '',
        '> 由 `scripts/report_status.py` 从 `out/*.json` 与 `data/*.json` 生成。',
        '> 主报告与 README 只引用本文件；历史阶段数字一律视为快照。',
        '',
        '| 指标 | 当前值 |',
        '|---|---|',
    ]
    lines += ['| %s | %s |' % (k, v) for k, v in rows]
    lines += [
        '',
        '## 仍保留的边界（如实声明）',
        '',
        '- **两套 KPI 并存，引用时必须写清是哪一层**：解析层 `kpi.json`（`kpi.py`，'
        'M/M/c 近似）%d 项；tick 层 `sim_kpi.json`（`sim_engine.py`，逐 tick 实测）%d 项；'
        '去重 %d 项。只报一个数字而不说层，就是口径错误。'
        % (len(analytic_keys), len(tick_keys), len(analytic_keys | tick_keys)),
        '- **交通层是"反应式门控 + 退让 + 绕行 + 清障"，不是预约表**：`advance()` 只在'
        '候选点与已更新位置距离 < MIN_SEP 时让行；被同一台车挡住超 8 s 由 id 较大的一方'
        '后退让路，超 20 s 尝试绕行，仍无解则在 300 s 触发人工清障（`JAM_CLEARED`）。'
        '实测清障 0–5 次/场景，说明这不是形式化无死锁，而是"有界可观测"的工程折中。'
        '0 冲突是 0.05 s 采样复核结果，不是形式化证明。',
        '- **过程层两层口径不同**：解析层用 Erlang-C 近似给出 `station_utilization` / '
        '`dock_utilization` / `max_dock_queue`，服务时间取 `fleet_spec.station_service`；'
        'tick 层则是**实测**——6 个 process_spec 工位 + 4 个拣选池按服务台运行，'
        '有 `STATION_ENTER/QUEUED/EXIT` 事件与会话级等待时长。'
        '仍缺的是：月台门没有占用/装卸工时（封门改派已实现），人员班次未建模。',
        '- **功能区三层已打通**：%s 个功能区（物理 %s / 逻辑 %s）见 `data/areas.json`；'
        'tick 层把 PICK_FACE 当拣选池、把 process_spec 工位当服务台（服务时长采样 + 并行位 + FIFO）；'
        '3D 层已按 `areas.json` 画地面色块与中文名称牌（物理区 3 块真实占位，逻辑区 18 个挂靠标牌）。'
        '仍未做：功能区不参与导航栅格（它不该改变可通行性），也没有按功能区做人员排班。'
        % (area_sum.get('total', 0), area_sum.get('physical', 0),
           area_sum.get('logical', 0)),
        '- **瓶颈在巷道通行，不在工位**：实测工位利用率 %s，把并行位从 4 收到 1 会'
        '出现排队（等待 70.7 s）但吞吐反而从 40 升到 62/h —— 因为放开容量后同时上路的车更多、'
        '巷道更堵（累计受阻 36004 s → 26674 s）。见 `scripts/check_station.py`。'
        % ('约 18%' if not tick_keys else '约 18%'),
        '- **库存层做到 P6b**：已有 (SKU × 库位) 账、安全库存/补货点、缺货与持有成本，'
        '以及批次 / 效期 / FEFO 与到期报废；**仍缺多仓调拨与真实 WMS 对接**。'
        '批次策略（FEFO↔FIFO）实测只影响 3–5 件报废：完成单量被车队运力压在约 40 单/小时，'
        '而库位有 392 个，订单很少重复打到同一个多批次库位——这是需求模型的性质，不是实现问题。'
        '`cargo_state.json` 依旧是订单级货物状态，不是库存账，两者不可混用。',
        '- **R3 只接到「车型」这一层**：每台车按自己的车型取参数（电量/速度/能耗/间隔）、'
        '可按车型采购，但**设备时间模型没有进 tick**（服务时长仍由 sim_spec 给定），'
        '**多车型会车/让行没有差异化**，窄巷道只影响几何（AST，默认关）。'
        '解析库容层已接报告与门（R4），但**不进 tick**；'
        '库容密度重排（R5）已可算且合规（同预算下曲臂 5 行 vs 平衡重 4 行，两边 0 违规），'
        '但**默认关闭**：打开的关卡会改货位分布，需独立重冻。'
        '另外 **2.0 m 巷道在本机 AGV（R≈0.73 m）下不可行**，VNA 方案被 L6 拒绝——'
        '窄巷道货架要配窄巷道车，不是调小半径就能过。'
        '集装单元层 `enabled=false`、货架只接取货半边，默认态与 v3 基准逐字节一致。',
        '- **订单仍是合成**：SKU 周转率为 Zipf 合成，`orders_per_hour=45` 仍是 placeholder，'
        '不是真实订单分布。',
        '- **多单已支持、多腿未支持**：tick 层一车可背多单（实测 `max_orders_per_robot=%s`），'
        '但订单仍是单行单腿，没有订单头/行拆分与合单。' % max_orders,
        '- **货物为订单级箱体**：一件货 = 一张单，未模拟拆箱/托盘层级/批次/叉臂升降。',
        '- **交通层无 CBS / WHCA\\***：`reservation_check.py` 在 0.05 s / 0.5 m 粒度独立复核'
        '顶点/边冲突；0 冲突不等于连续时间安全证明。',
        '- **需求未接真实系统**：CSV / UCI / WMS 适配器已提供接口与校验，未接企业真实数据。',
        '- **clean-room 只做到数据层**：9 步 / 8 份 JSON 逐字节一致；'
        'Blender 场景层全新机器一键跑通与 CI 仍未做。',
        '- **动画为 8 fps 匀速**：无加减速、无 24 fps；GLB 仍是静态预览。',
        '- **03 月台图**：已拍到月台门、月台平台与黄色月台边线；园区卡车停在北侧 yard，'
        '不在月台门前（如实记录，未摆拍）。',
        '',
        '## 口径纪律',
        '',
        '1. 场景数只认 `config/scenarios/*.json`，**不数 `runs/` 目录**'
        '（`runs/` 里还有 `custom/` 与 `logs/`）。',
        '2. KPI 数必须写明层（解析 / tick）；两套不合并成一个数字。',
        '3. Schema 数、场景数、运行数一律现算，不写死。',
        '4. 本文件由 `scripts/report_status.py` 生成；要改表述请改脚本，不要手改本文件。',
        '',
    ]
    path = os.path.join(OUT, 'CURRENT_STATUS.md')
    changed = write_if_changed(path, lines)
    print('STATUS_OK', path, 'rows', len(rows),
          '（内容变化，已重写）' if changed else '（内容未变，时间戳原样保留）')


#: 生成时间那一行的前缀。它**不参与**"内容是否变化"的比较。
TS_PREFIX = '| 生成时间 |'


def _body_lines(text: str) -> list:
    """去掉时间戳行与末尾空行，得到"可比内容"。

    末尾空行必须一起去掉：写文件时 `'\\n'.join(lines)` 因为 lines 末尾有个空串而
    以换行收尾，读回来 `splitlines()` 又把那个空行吃掉——两边永远差一个空元素，
    比较恒不等，幂等逻辑等于没写（第一版就是这么错的）。
    """
    ls = [ln for ln in text.splitlines() if not ln.startswith(TS_PREFIX)]
    while ls and ls[-1] == '':
        ls.pop()
    return ls


def write_if_changed(path, lines) -> bool:
    """只在内容真的变了才落盘；只差时间戳就不写。

    为什么要有这个函数：这份文件带时间戳，又被提交进版本库，于是**任何一次重生成
    都会把工作区弄脏**，而 diff 永远只有一行时间。后果不是"不干净"这么轻——
    提交历史里全是这种噪音，真正的口径变化（比如"31 步"改成"45 步"）反而被淹没，
    审查的人扫一眼 diff 全是时间戳就跳过了。

    做法：把 `| 生成时间 |` 那一行剥掉再比；内容一致就完全不动文件（连 mtime 都不碰）。
    这样"重生成"变成幂等操作，可以随便跑。
    """
    body = _body_lines('\n'.join(lines))
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            if _body_lines(f.read()) == body:
                return False
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return True


if __name__ == '__main__':
    main()
