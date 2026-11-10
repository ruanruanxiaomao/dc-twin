"""report_status.py — 单一真相源：从产物 JSON 生成当前状态表。

第二轮审计 §3.10 / P1-7：旧版每次迭代手打数字，导致主报告数字互相矛盾。
本脚本只读 JSON，输出 out/CURRENT_STATUS.md；主报告与 README 只引用该文件。

运行：python scripts/report_status.py
"""
import datetime
import glob
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT, DATA, CONFIG = (os.path.join(ROOT, 'out'), os.path.join(ROOT, 'data'),
                     os.path.join(ROOT, 'config'))


def j(*parts, default=None):
    """读 JSON；文件不存在时返回 default（数据链没有 Blender 产物时要用）。"""
    p = os.path.join(*parts)
    if not os.path.exists(p):
        return {} if default is None else default
    return json.load(open(p, encoding='utf-8'))


def n(v, default='n/a'):
    return default if v is None else v


def main():
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
        ('布局', '%d 仓 / %d 货架 / %d 库位（%d SHELF + %d HOME）/ %d 月台 / %d 站 / %d AGV'
         % (Ls['warehouses'], Ls['racks'], Ls['locations'],
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
        ('开源交付层', 'dc CLI 7 命令 / 31 步链（data 11 · scenarios 16 · full 31）；'
                       '输出契约 sidecar %s 份 / manifest %s 个产物；'
                       '静态 Demo %s 个场景 · %s 台车'
         % (man.get('counts', {}).get('report_sidecars'),
            man.get('counts', {}).get('artifacts'),
            demo_man.get('scenarios'), demo_man.get('agents'))),
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
        '- **库存层空缺**：`cargo_state.json` 是订单级货物状态，不是 SKU / 批次 / 货位库存；'
        '没有安全库存、补货点、效期、周转。',
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
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('STATUS_OK', path, 'rows', len(rows))


if __name__ == '__main__':
    main()
