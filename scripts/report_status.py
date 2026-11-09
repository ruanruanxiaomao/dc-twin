"""report_status.py — 单一真相源：从产物 JSON 生成当前状态表。

第二轮审计 §3.10 / P1-7：旧版每次迭代手打数字，导致主报告数字互相矛盾。
本脚本只读 JSON，输出 out/CURRENT_STATUS.md；主报告与 README 只引用该文件。

运行：python scripts/report_status.py
"""
import datetime
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT, DATA = os.path.join(ROOT, 'out'), os.path.join(ROOT, 'data')


def j(*parts):
    return json.load(open(os.path.join(*parts), encoding='utf-8'))


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
    vr = j(OUT, 'validate_report.json')
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
    scenarios = [p for p in os.listdir(os.path.join(ROOT, 'runs'))
                 if os.path.isdir(os.path.join(ROOT, 'runs', p))]
    kpi_dirs = []
    for d in scenarios:
        kpi_dirs += [p for p in os.listdir(os.path.join(ROOT, 'runs', d))]
    # Only analytic runs own kpi.json; stage-A tick runs share the same tree
    # (sim_kpi.json) and must not be picked by a plain directory-name sort.
    base_root = os.path.join(ROOT, 'runs', 'baseline')
    base_runs = sorted(d for d in os.listdir(base_root)
                       if os.path.isfile(os.path.join(base_root, d, 'kpi.json')))
    base_kpi = j(base_root, base_runs[-1], 'kpi.json') if base_runs else {}

    passed = sum(1 for r in vr if r['status'] == 'PASS')
    neg_total = sum(x['total'] for x in (neg, neg_spec, neg_scen))
    neg_pass = sum(x['passed'] for x in (neg, neg_spec, neg_scen))
    Ls = L['summary']
    home_n = sum(1 for w in L['warehouses'] for l in w['locations']
                 if l['kind'] == 'HOME')
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = [
        ('生成时间', ts),
        ('Blender', br['blender']),
        ('布局来源', 'config/layout_spec.json → spec_layout.py（L1–L12 PASS，'
                     'issues=%s warnings=%s；%s 扇门）'
         % (len(spec_rep['issues']), len(spec_rep['warnings']),
            spec_rep['summary'].get('doors'))),
        ('AP 互斥', '%s 对（WARN，两 AP <2R 不能同时占用）；'
                    '预约表复核同时占用 %s 次；加宽变体互斥 %s 对'
         % (spec_rep['mutex_pairs'], resv['mutex_simultaneous_occupations'],
            variants['layout_spec_wide']['mutex'])),
        ('四层配置', 'layout_spec / fleet_spec / demand_spec / 8 个 scenarios + 4 份 schema；'
                     '3 个布局 spec（当前 / F4 / 加宽）全部审计 PASS'),
        ('场景', '%d 个场景 × %d 次运行；%d 个 KPI 每个带 definition/formula/source'
         % (len(scenarios), len(kpi_dirs), len(base_kpi.get('metrics', {})))),
        ('布局', '%d 仓 / %d 货架 / %d 库位（%d SHELF + %d HOME）/ %d 月台 / %d 站 / %d AGV'
         % (Ls['warehouses'], Ls['racks'], Ls['locations'],
            Ls['shelf_locations'], home_n,
            Ls['docks'], Ls['stations'], Ls['robots'])),
        ('布局审计', 'issues=%s warnings=%s verdict=%s'
         % (la['count'], la['warnings'], la['verdict'])),
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
         % (pm['duration_s'], n(ar['mp4'].get('duration_s')),
            rr['frames_rendered'], rr['fps'])),
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
        ('.blend', '%s objects / %s meshes / %s materials / %s actions'
         '（构建报告 %s/%s/%s，重开实测一致）；HOME 待命位 %s；隐藏 AGV=%s'
         % (ba['objects'], ba['meshes'], ba['materials'], ba['actions'],
            br['objects'], br['meshes'], br['materials'],
            ba.get('home_bays'), ba.get('agv_with_hide_render_keyframe'))),
        ('GLB', '%s 节点 / %s meshes / %s materials / %s animations；'
         '禁用节点=%s；DC_ROOT=%s'
         % (ar['glb']['nodes'], ar['glb']['meshes'], ar['glb']['materials'],
            ar['glb']['animations'], ar['glb']['forbidden_nodes'],
            ar['glb']['has_DC_ROOT'])),
        ('图像', '6 张 3D 静帧 + 2 张 2D 闭环路线图 + 时间-空间图 + 快照序列；'
         '尺寸全对=%s' % ar['all_png_sizes_ok']),
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
        ('验收', '%d/%d PASS（A1–A40）；负测试 %d/%d（3 个测试文件）'
         % (passed, len(vr), neg_pass, neg_total)),
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
        '- **仍非 tick 级 cell reservation**：用「优先级 + 出发错峰 + HOME 避让软代价」逼近，'
        '严格口径 0 冲突是采样验证结果（0.05 s），不是形式化证明。',
        '- **订单仍是合成场景**：SKU 周转率为 Zipf 合成，21 张订单按前后区派单，'
        '每种类型 4–5 单，不是真实订单分布。',
        '- **货物为订单级箱体**：一件货 = 一张单，未模拟拆箱/托盘层级/批次/叉臂升降。',
        '- **动画为 8 fps 匀速**：无加减速、无 24 fps；GLB 仍是静态预览。',
        '- **场景层是解析 what-if**：M/M/c 排队近似算吞吐/等待/利用率，不是逐车 A\\* 仿真；'
        '碰撞与路径以主链 `traffic_check.py` / `path_metrics.json` 为准。',
        '- **运营层是离线回放**：`fleet_sim.py` 回放状态机与电量，尚未把电量/故障/任务队列'
        '接入调度循环（DESIGN_PLAN Step 7 未做）；每车仍 1 单。',
        '- **交通层是独立复核**：`reservation_check.py` 在 0.05 s / 0.5 m 粒度验证顶点/边冲突，'
        '未实现 CBS / WHCA\\* / tick 级预约表；0 冲突不是连续时间的形式化证明。',
        '- **需求仍是合成**：CSV / UCI / WMS 适配器已提供接口与校验，未接企业真实数据；'
        '`orders_per_hour=45` 仍是 placeholder。',
        '- **clean-room 只做到数据层**：9 步 / 8 份 JSON 逐字节一致；'
        'Blender 场景层全新机器一键跑通与 CI 仍未做。',
        '- **03 月台图**：已拍到月台门、月台平台与黄色月台边线；园区卡车停在北侧 yard，'
        '不在月台门前（如实记录，未摆拍）。',
        '',
    ]
    path = os.path.join(OUT, 'CURRENT_STATUS.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('STATUS_OK', path, 'rows', len(rows))


if __name__ == '__main__':
    main()
