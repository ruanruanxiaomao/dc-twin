"""validate.py — 验收质量门（审计 §8 的 A1–A14）。

与旧版 14 条的本质区别：
  · 每条判据尽量**独立复算**，不直接相信生成器自报的 JSON；
  · 任一 FAIL → `sys.exit(1)`（旧版永远退出 0）；
  · 阈值来自物理/几何常量，不按当前结果调；
  · 必须能通过 tests/test_validator_can_fail.py 的 7 个破坏性负测试。

运行：python scripts/validate.py   退出码 0=PASS / 1=FAIL
产出：out/validate_report.json
"""
import hashlib
import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA, OUT = os.path.join(ROOT, 'data'), os.path.join(ROOT, 'out')
sys.path.insert(0, os.path.dirname(__file__))
from geometry import AGV_RADIUS, MIN_SEP, obstacle_rects, seg_hits_rect, inflate

R = []


def check(name, ok, detail):
    R.append({'item': name, 'status': 'PASS' if ok else 'FAIL', 'detail': detail})


def jload(p):
    return json.load(open(p, encoding='utf-8'))


def jlines(p):
    return [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]


def exists(p):
    return os.path.exists(p)


def rect_overlap(a, b, eps=1e-6):
    # 与 layout_audit.py 同口径：仅共享边界不算重叠
    return (a[0] < b[2] - eps and b[0] < a[2] - eps and
            a[1] < b[3] - eps and b[1] < a[3] - eps)


def main():
    L = jload(os.path.join(DATA, 'layout.json'))
    CFG = jload(os.path.join(DATA, 'config.json'))
    paths = jlines(os.path.join(DATA, 'paths.jsonl'))
    events = jlines(os.path.join(DATA, 'events.jsonl'))
    skus = jload(os.path.join(DATA, 'skus.json'))['skus']
    tasks = jload(os.path.join(DATA, 'tasks.json'))
    slot = jload(os.path.join(DATA, 'slotting.json'))
    pm = jload(os.path.join(DATA, 'path_metrics.json'))
    nav = jload(os.path.join(DATA, 'nav_grid.json'))

    la = jload(os.path.join(OUT, 'layout_audit.json'))
    tr = jload(os.path.join(OUT, 'traffic_report.json'))
    geo = jload(os.path.join(OUT, 'geometry_report.json'))
    br = jload(os.path.join(OUT, 'build_report.json'))
    ba = jload(os.path.join(OUT, 'blend_audit.json'))
    ar = jload(os.path.join(OUT, 'artifact_report.json'))
    rr = jload(os.path.join(OUT, 'render_report.json'))

    rects = obstacle_rects(L, CFG)
    locmap = {l['id']: l for w in L['warehouses'] for l in w['locations']}
    spawns = {rb['id']: (rb['position'][0], rb['position'][2])
              for w in L['warehouses'] for rb in w['spawn']['robots']}
    grid_rows = nav['walkable_rows']
    cell = nav['cell_size_m']

    # A1 布局实体不重叠（独立复算 + 独立审计脚本）
    ov = 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            if rect_overlap(rects[i][2], rects[j][2]):
                ov += 1
    check('A1 布局实体零重叠',
          ov == 0 and la['count'] == 0 and la['verdict'] == 'PASS',
          '独立复算重叠=%s；layout_audit issues=%s verdict=%s'
          % (ov, la['count'], la['verdict']))

    # A2 访问点可达（独立检查可走格 + 场地内 + 净距）
    bad_ap = []
    for lid, l in locmap.items():
        x, z = l['access_point']
        c, r = int(x / cell), int(z / cell)
        walk = (0 <= r < len(grid_rows) and 0 <= c < len(grid_rows[r])
                and grid_rows[r][c] == '1')
        inside_site = 0 <= x <= L['size']['width'] and 0 <= z <= L['size']['depth']
        near = min((math.hypot(max(rc[0] - x, 0, x - rc[2]),
                               max(rc[1] - z, 0, z - rc[3]))
                    for _, _, rc in rects), default=99)
        if not (walk and inside_site and near >= 0.5):
            bad_ap.append(lid)
    check('A2 全部访问点在通道内且净距 >=0.5 m',
          not bad_ap and la['warnings'] == 0,
          'locations=%s 异常=%s layout_audit warnings=%s'
          % (len(locmap), len(bad_ap), la['warnings']))

    # A3 出生点合法
    sp_bad, ids = [], list(spawns)
    for i in range(len(ids)):
        p = spawns[ids[i]]
        if any(rc[0] <= p[0] <= rc[2] and rc[1] <= p[1] <= rc[3]
               for _, _, rc in rects):
            sp_bad.append(ids[i])
        for j in range(i + 1, len(ids)):
            q = spawns[ids[j]]
            if math.hypot(p[0] - q[0], p[1] - q[1]) < 1.5:
                sp_bad.append(ids[i] + '/' + ids[j])
    check('A3 出生点不在实体内且两两 >=1.5 m', not sp_bad,
          'agents=%s 异常=%s' % (len(ids), sp_bad[:5]))

    # A4 路径不穿实体（独立抽样全部线段-矩形相交）
    by = {}
    for p in paths:
        by.setdefault(p['agent_id'], []).append(p)
    segs, hits = 0, 0
    for ps in by.values():
        ps = sorted(ps, key=lambda p: p['t'])
        for a, b in zip(ps, ps[1:]):
            segs += 1
            p, q = (a['x'], a['y']), (b['x'], b['y'])
            if any(seg_hits_rect(p, q, inflate(rc, AGV_RADIUS))
                   for _, _, rc in rects):
                hits += 1
    check('A4 AGV_RADIUS 下路径零穿越实体',
          hits == 0 and geo['verdict'] == 'PASS'
          and geo['segment_crossings_raw'] == 0 and geo['agv_radius_m'] >= 0.7,
          '独立复算 %s 段命中=%s；geometry radius=%s min_clearance=%s'
          % (segs, hits, geo['agv_radius_m'], geo['min_path_to_obstacle_m']))

    # A5 多车不重叠：第六阶段改为严格口径，不再有条件通过
    strict = tr.get('strict_violations',
                    tr.get('strict_violations_if_completed_stay_in_scene', -1))
    check('A5 全程在场：任意时刻两车中心距 >= 2×AGV_RADIUS',
          tr['verdict'] == 'PASS' and tr['count'] == 0
          and strict == 0 and tr['min_pair_distance_m'] >= MIN_SEP
          and not tr.get('completed_agents_excluded_after_t_end', True),
          'threshold=%.4f m；min_pair=%s m at %s；traffic issues=%s；'
          '严格口径冲突=%s；完成车离场=%s'
          % (MIN_SEP, tr['min_pair_distance_m'], tr['min_pair_at'],
             tr['count'], strict, tr.get('completed_agents_excluded_after_t_end')))

    # A6 起点 = 出生点
    first = {}
    for p in paths:
        first.setdefault(p['agent_id'], p)
    mism = [k for k, p in first.items()
            if k not in spawns or
            math.hypot(p['x'] - spawns[k][0], p['y'] - spawns[k][1]) > 0.01]
    check('A6 每台车首帧等于出生点（无瞬移）', not mism,
          'agents=%s 不匹配=%s' % (len(first), mism[:5]))

    # A7 行驶速度一致
    vbad = []
    for aid, ps in by.items():
        ps = sorted(ps, key=lambda p: p['t'])
        for a, b in zip(ps, ps[1:]):
            d = math.hypot(b['x'] - a['x'], b['y'] - a['y'])
            if d < 1e-9:
                continue
            dt = b['t'] - a['t']
            v = d / dt if dt > 0 else 99
            if not (0.8 - 1e-3 <= v <= 1.5 + 1e-3):
                vbad.append((aid, round(v, 3)))
    check('A7 所有移动段速度 ∈[0.8,1.5] m/s', not vbad,
          '独立复算异常段=%s；config speed=%s' % (len(vbad), CFG['agv']['speed_mps']))

    # A8 朝向连续
    hbad = 0
    for ps in by.values():
        ps = sorted(ps, key=lambda p: p['t'])
        for a, b in zip(ps, ps[1:]):
            if abs(b['theta'] - a['theta']) > math.radians(10) + 1e-3:
                hbad += 1
    check('A8 相邻关键帧 |Δθ| <=10°', hbad == 0,
          '独立复算跳变=%s；traffic HEADING_JUMP=%s'
          % (hbad, tr['issue_kinds'].get('HEADING_JUMP', 0)))

    # A9 帧完整
    ok9 = (rr['frames_rendered'] == rr['expected_frames']
           and ba['frame_end'] == rr['frame_end']
           and ar['mp4']['frame_count_ok']
           and abs(ar['mp4']['duration_s'] - rr['duration_s']) < 0.2)
    check('A9 frame_end 与视频帧数完整一致', ok9,
          'blend frame_end=%s render %s->%s frames=%s/%s mp4=%ss'
          % (ba['frame_end'], rr['frame_start'], rr['frame_end'],
             rr['frames_rendered'], rr['expected_frames'],
             ar['mp4']['duration_s']))

    # A10 数据引用完整性
    sku_ids = {s['sku'] for s in skus}
    ev_bad = [e['event_id'] for e in events
              if e['from'] not in locmap or e['to'] not in locmap
              or e['sku'] not in sku_ids]
    t_bad = [t['task_id'] for t in tasks['tasks']
             if t['status'] == 'ASSIGNED' and
             (t['agent_id'] not in spawns or
              not all(lg['from'] in locmap and lg['to'] in locmap and
                      lg['sku'] in sku_ids for lg in t['legs']))]
    s_bad = [a['sku'] for a in slot['assignments']
             if a['status'] == 'ASSIGNED' and a['sku'] not in sku_ids]
    pal_ok = br['counts'].get('pallets_built') == slot['summary']['assigned']
    check('A10 事件/任务/货位/托盘引用完整可追溯',
          not ev_bad and not t_bad and not s_bad and pal_ok
          and slot['summary']['all_locations_have_status'],
          'events=%s 坏=%s tasks=%s 坏=%s slot坏=%s 托盘=%s/%s'
          % (len(events), len(ev_bad), len(tasks['tasks']), len(t_bad),
             len(s_bad), br['counts'].get('pallets_built'),
             slot['summary']['assigned']))

    # A11 产物可解析
    ok11 = (ar['verdict'] == 'PASS' and ar['glb']['valid']
            and not ar['glb']['forbidden_nodes'] and ar['glb']['has_DC_ROOT']
            and ar['all_png_sizes_ok'] and ar['mp4']['valid']
            and br['objects'] == ba['objects']
            and br['meshes'] == ba['meshes']
            and br['materials'] == ba['materials'])
    check('A11 GLB/PNG/MP4 与 .blend 重开实测一致', ok11,
          'glb nodes=%s forbidden=%s DC_ROOT=%s；png尺寸全对=%s；'
          'objects %s/%s meshes %s/%s'
          % (ar['glb']['nodes'], ar['glb']['forbidden_nodes'],
             ar['glb']['has_DC_ROOT'], ar['all_png_sizes_ok'],
             br['objects'], ba['objects'], br['meshes'], ba['meshes']))

    # A12 许可合规（不只看 asset_source 字符串）
    lic = os.path.join(ROOT, 'LICENSE')
    notice = os.path.join(ROOT, 'THIRD_PARTY_NOTICES.md')
    lic_txt = open(lic, encoding='utf-8').read() if exists(lic) else ''
    ntc_txt = open(notice, encoding='utf-8').read() if exists(notice) else ''
    asset = os.path.join(ROOT, 'assets', 'cargotwin_assets.glb')
    sha = hashlib.sha256(open(asset, 'rb').read()).hexdigest() if exists(asset) else ''
    ok12 = (exists(lic) and exists(notice)
            and lic_txt.count('MIT License') >= 1
            and ntc_txt.count('MIT License') >= 2
            and 'Zihan Wang' in ntc_txt and 'Wayne Chou' in ntc_txt
            and sha[:16] in ntc_txt and len(sha) == 64)
    check('A12 LICENSE / NOTICE 含 MIT 全文与资产哈希', ok12,
          'LICENSE=%s NOTICE=%s MIT段落=%s 资产sha=%s'
          % (exists(lic), exists(notice), ntc_txt.count('MIT License'), sha[:16]))

    # A13 验证器能失败
    neg = os.path.join(OUT, 'negative_tests.json')
    neg_report = jload(neg) if exists(neg) else None
    src = open(os.path.join(ROOT, 'scripts', 'validate.py'), encoding='utf-8').read()
    # 第二轮审计 §3.8：不能只信报告里的 PASS 数字，要核对每个用例真的返回了非 0
    cases = neg_report.get('cases', []) if neg_report else []
    neg_ok = (neg_report is None or
              (neg_report.get('passed') == neg_report.get('total') == 7
               and neg_report.get('verdict') == 'PASS'
               and len(cases) == 7
               and all(c.get('ok') is True and c.get('exit_code') not in (0, None)
                       for c in cases)))
    ok13 = ('sys.exit(1' in src
            and exists(os.path.join(ROOT, 'tests', 'test_validator_can_fail.py'))
            and neg_ok)
    check('A13 验证器能失败且负测试逐例可核验', ok13,
          'validate 含 sys.exit(1)=%s；负测试=%s；逐例非0退出=%s'
          % ('sys.exit(1' in src,
             ('%s/%s' % (neg_report['passed'], neg_report['total']))
             if neg_report else '报告未生成（脚本已存在）',
             all(c.get('exit_code') not in (0, None) for c in cases)))

    # A14 从零可复现
    need = ['run_all.ps1', 'requirements.txt', 'README.md', '.gitignore',
            'LICENSE', 'THIRD_PARTY_NOTICES.md']
    missing = [f for f in need if not exists(os.path.join(ROOT, f))]
    scripts = ['gen_data.py', 'layout_audit.py', 'nav_grid.py',
               'optimize_slotting.py', 'gen_tasks.py', 'plan_paths.py',
               'gen_cargo.py', 'traffic_check.py', 'check_geometry.py',
               'plot_nav_plan.py',
               'build_dc.py', 'inspect_blend.py', 'animate.py',
               'export_glb.py', 'render.py', 'parse_artifacts.py',
               'encode_video.py', 'validate.py', 'report_status.py',
               'annotate_heatmap.py']
    miss_s = [s for s in scripts if not exists(os.path.join(ROOT, 'scripts', s))]
    ok14 = (not missing and not miss_s
            and 'path_samples' not in L['summary']
            and pm['samples'] == len(paths))
    check('A14 从零复现所需文件齐备且计数自洽', ok14,
          '缺文件=%s 缺脚本=%s；layout.summary 无 path_samples=%s；'
          'path_metrics.samples=%s / paths=%s'
          % (missing, miss_s, 'path_samples' not in L['summary'],
             pm['samples'], len(paths)))

    # --- 第二轮审计新增：运营语义 / 路线图语义 ---
    # A15 事件覆盖率：第六阶段 21 单全部执行、5 种类型全部进视频
    total_ev, executed = len(events), tasks['summary']['assigned']
    cov = executed / max(total_ev, 1)
    types = set(pm.get('event_types_executed', []))
    distinct = {e['type'] for e in events if
                e['event_id'] in {t['event_id'] for t in tasks['tasks']
                                  if t['status'] == 'ASSIGNED'}}
    ok15 = (abs(cov - 1.0) < 1e-9 and tasks['summary']['pending'] == 0
            and abs(float(pm.get('event_coverage', 0)) - 1.0) < 1e-9
            and len(types) >= 5 and len(distinct) >= 5)
    check('A15 订单 100% 执行且 5 种业务类型全部进视频', ok15,
          'executed %d/%d = %.1f%%；pending %d；types=%s'
          % (executed, total_ev, cov * 100,
             tasks['summary']['pending'], sorted(distinct)))

    # A16 任务时间：t_request/priority 必须真正进入调度
    sch = jload(os.path.join(DATA, 'agent_schedules.json'))['schedules']
    release_bad = [a for a, s in sch.items()
                   if s.get('departure_t_s', 0) + 1e-6 < s.get('t_request_s', 0)]
    ok16 = (not release_bad and pm.get('on_time_rate') is not None
            and pm.get('max_tardiness_s') is not None)
    check('A16 任务时间约束生效（start >= t_request，tardiness 可报告）', ok16,
          '违反释放时间=%s；on_time_rate=%s；max_tardiness=%ss；'
          'max_conflict_wait=%ss'
          % (release_bad[:3], pm.get('on_time_rate'), pm.get('max_tardiness_s'),
             pm.get('max_conflict_wait_s')))

    # A17 严格口径冲突必须归零（不再只是「报告出来」）
    strict = tr.get('strict_violations',
                    tr.get('strict_violations_if_completed_stay_in_scene'))
    ok17 = isinstance(strict, int) and strict == 0
    check('A17 严格口径交通冲突归零（完成车全程在场）', ok17,
          'strict_violations=%s；阈值=%.4f m；semantics=%s'
          % (strict, MIN_SEP, tr.get('semantics')))

    # A18 路线图语义：路网/时间-空间/快照三图 + 数字与 JSON 一致
    rrp = os.path.join(OUT, 'route_report.json')
    rrep = jload(rrp) if exists(rrp) else {}
    p09 = ar['pngs'].get('09_time_space.png', {})
    p10 = ar['pngs'].get('10_snapshot_sequence.png', {})
    ok18 = (rrep.get('routes') == executed
            and rrep.get('route_head_on_pairs') == 0
            and rrep.get('edge_clearance_m', 0) > 0
            and abs(rrep.get('edge_clearance_m', -1) -
                    (geo['min_path_to_obstacle_m'] - AGV_RADIUS)) < 0.01
            and rrep.get('path_obstacle_crossings') == geo['segment_crossings_raw']
            and p09.get('size_ok') and p10.get('size_ok'))
    check('A18 路线图语义：路数/迎头对/边缘间隙/时间-空间图/快照', ok18,
          'routes=%s segments=%s head_on=%s edge_clearance=%sm '
          'central_cross_aisle_samples=%s；09/10 尺寸=%s/%s'
          % (rrep.get('routes'), rrep.get('segments_07'),
             rrep.get('route_head_on_pairs'), rrep.get('edge_clearance_m'),
             rrep.get('central_cross_aisle_samples'),
             p09.get('size_ok'), p10.get('size_ok')))

    # --- 第六阶段：闭环作业 / 货物状态机 / 全程在场 ---
    # A19 闭环数据链：路径首尾 = HOME，全体 t=0..t_end 在场，PICK/DROP 齐全
    home_ids = {l['id']: l['access_point'] for l in locmap.values()
                if l['kind'] == 'HOME'}
    home_of = {rb['id']: home_ids[rb['home_id']]
               for w in L['warehouses'] for rb in w['spawn']['robots']}
    task_by_agent = {t['agent_id']: t for t in tasks['tasks']
                     if t['status'] == 'ASSIGNED'}
    t_end = max(p['t'] for p in paths)
    loop_bad = []
    for aid, ps in by.items():
        ps = sorted(ps, key=lambda p: p['t'])
        h = home_of.get(aid)
        if h is None:
            loop_bad.append(aid + ':no_home')
            continue
        for tag, p in (('start', ps[0]), ('end', ps[-1])):
            if math.hypot(p['x'] - h[0], p['y'] - h[1]) > 0.01:
                loop_bad.append('%s:%s' % (aid, tag))
        if abs(ps[0]['t']) > 1e-6 or abs(ps[-1]['t'] - t_end) > 1e-6:
            loop_bad.append(aid + ':presence')
        ph = {p.get('phase') for p in ps}
        if not {'PICK', 'DROP', 'PARK'} <= ph:
            loop_bad.append(aid + ':phases')
        t = task_by_agent.get(aid)
        if not t or t.get('return_to') not in home_ids:
            loop_bad.append(aid + ':return_to')
    check('A19 闭环作业数据链：HOME→PICK→DROP→HOME 且全程在场',
          not loop_bad and pm.get('route_kind') == 'CLOSED_LOOP'
          and pm.get('return_to_home') is True
          and pm.get('agents_present_whole_time') is True,
          'agents=%s 异常=%s；pick_drop_stops=%s；duration=%ss'
          % (len(by), loop_bad[:5], pm.get('pick_drop_stops'), pm.get('duration_s')))

    # A20 货物状态机：21 件货物随车、库存增减可追溯、.blend 里真有动画对象
    cargo_path = os.path.join(DATA, 'cargo_state.json')
    cargo = jload(cargo_path) if exists(cargo_path) else {}
    cs = cargo.get('summary', {})
    ok20 = (cs.get('orders') == executed
            and cs.get('loaded') == executed
            and cs.get('delivered') == executed
            and cs.get('state_transitions') == 2 * executed
            and ba.get('cargo_objects') == executed
            and ba.get('cargo_with_action') == executed
            and ba.get('home_bays') == len(spawns)
            and ba.get('agv_with_hide_render_keyframe') == 0
            and len(cargo.get('inventory_delta_by_location', {})) > 0)
    check('A20 货物状态机与全程在场：托盘随车、库存可追溯、无隐藏 AGV', ok20,
          'cargos=%s loaded=%s delivered=%s transitions=%s；'
          '.blend cargo=%s/%s home_bays=%s agv_hidden=%s'
          % (cs.get('orders'), cs.get('loaded'), cs.get('delivered'),
             cs.get('state_transitions'), ba.get('cargo_objects'),
             ba.get('cargo_with_action'), ba.get('home_bays'),
             ba.get('agv_with_hide_render_keyframe')))

    # A21 路线图闭环语义：独立复算 routes.json 首尾 / 三段里程
    routes_j = jload(os.path.join(DATA, 'routes.json'))
    rt, bd = routes_j['routes'], routes_j['bounds']
    closed, leg_sum = 0, [0.0, 0.0, 0.0]
    for aid, pts in rt.items():
        if pts[0] == pts[-1]:
            closed += 1
        b = bd[aid]
        for i, (s, e) in enumerate(((0, b['pick']), (b['pick'], b['drop']),
                                    (b['drop'], b['end']))):
            leg_sum[i] += sum(math.hypot(q[0] - p[0], q[1] - p[1])
                              for p, q in zip(pts[s:e], pts[s + 1:e + 1]))
    rtot = rrep.get('loop_leg_totals_m', [])
    ok21 = (closed == len(rt) == executed
            and rrep.get('closed_loops') == closed
            and rrep.get('route_kind') == 'CLOSED_LOOP'
            and rrep.get('home_bays') == len(spawns)
            and all(v > 0 for v in leg_sum)
            and len(rtot) == 3
            and all(abs(rtot[i] - round(leg_sum[i], 2)) < 0.05 for i in range(3)))
    check('A21 路线图闭环语义：首尾闭合 + 三段里程可复算', ok21,
          'closed=%s/%s；独立复算三段=%s；route_report=%sm；'
          'central_samples=%s'
          % (closed, len(rt), [round(v, 2) for v in leg_sum], rtot,
             rrep.get('central_cross_aisle_samples')))

    with open(os.path.join(OUT, 'validate_report.json'), 'w', encoding='utf-8') as f:
        json.dump(R, f, ensure_ascii=False, indent=2)
    fails = [r for r in R if r['status'] != 'PASS']
    for r in R:
        print('[%s] %s — %s' % (r['status'], r['item'], r['detail']))
    print('SUMMARY', len(R) - len(fails), '/', len(R), 'PASS')
    if fails:
        print('FAILED:', [r['item'] for r in fails])
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
