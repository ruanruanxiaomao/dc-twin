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
    route = j(OUT, 'route_report.json')

    passed = sum(1 for r in vr if r['status'] == 'PASS')
    Ls = L['summary']
    home_n = sum(1 for w in L['warehouses'] for l in w['locations']
                 if l['kind'] == 'HOME')
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = [
        ('生成时间', ts),
        ('Blender', br['blender']),
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
        ('验收', '%d/%d PASS；负测试 %d/%d'
         % (passed, len(vr), neg['passed'], neg['total'])),
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
        '- **运营层未做**：WMS/车队管理/电量故障/站台排队/运营 KPI/真实数据校准。',
        '- **clean-room 只做到数据层**：全新空目录复现的 5 份关键 JSON 与主工程逐字节一致；'
        'Blender 场景层全新机器一键跑通 / venv 自动创建 / CI 仍未做。',
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
