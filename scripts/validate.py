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

# Windows 管道控制台默认非 UTF-8（cp1252/GBK），中文报告会 UnicodeEncodeError。
# 强制 UTF-8 + replace，保证 CI 与本地 GBK 控制台都能跑完。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

R = []
FLAGS = {}          # set by the __main__ CLI (--tier / --json / --junit / --check)


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
    # 项目自身 v3.8 起改用 Apache-2.0（用户指定），第三方资产仍为 MIT：
    # 所以 A12 要同时核验"自己的许可证"和"别人的许可证还留着"。
    own_lic = (lic_txt.count('Apache License') >= 1
               and 'Version 2.0' in lic_txt)
    tpl_lic = ntc_txt.count('MIT License') >= 2
    ok12 = (exists(lic) and exists(notice)
            and own_lic and tpl_lic
            and 'Zihan Wang' in ntc_txt and 'Wayne Chou' in ntc_txt
            and sha[:16] in ntc_txt and len(sha) == 64)
    check('A12 自身 Apache-2.0 + 第三方 MIT 全文与资产哈希', ok12,
          'LICENSE=%s NOTICE=%s 自身Apache=%s 第三方MIT段落=%s 资产sha=%s'
          % (exists(lic), exists(notice), own_lic, ntc_txt.count('MIT License'),
             sha[:16]))

    # A13 行为测试（OPEN_SOURCE_PLAN §4.2）：不再读取本文件源码里的字符串自证，
    # 而是核验 7 个破坏性用例真的让校验器/生成器返回非 0 退出码。
    # 其中 5 个用例的 check 目标是 validate.py 本身（删除 LICENSE / 截断 GLB /
    # 换空白 PNG / 篡改 samples / 删 NOTICE），即"扰动产物 -> validate 退出 1"。
    neg = os.path.join(OUT, 'negative_tests.json')
    neg_report = jload(neg) if exists(neg) else None
    cases = neg_report.get('cases', []) if neg_report else []
    behavior_cases = [c for c in cases if 'validate' in str(c.get('check', ''))]
    cases_ok = all(c.get('ok') is True and c.get('exit_code') not in (0, None)
                   for c in cases)
    # 生产物（PNG/GLB）缺失时用例会如实 skip，不伪装成 PASS：
    # 所以判据是 total + skipped == 7，且被执行的全部按预期失败。
    n_ran = neg_report.get('total') if neg_report else 0
    n_skip = neg_report.get('skipped', 0) if neg_report else 0
    neg_ok = (neg_report is not None
              and neg_report.get('passed') == n_ran
              and n_ran + n_skip == 7 and n_ran >= 5
              and neg_report.get('verdict') == 'PASS'
              and len(cases) == 7 and cases_ok
              and len(behavior_cases) >= 5)
    ok13 = (exists(os.path.join(ROOT, 'tests', 'run_all_negative.py')) and neg_ok)
    check('A13 行为证明：扰动产物后校验器真的失败', ok13,
          '负测试=%s；逐例非0退出=%s；其中 validate 行为用例=%d/7；'
          'runner=%s'
          % (('%s/%s' % (neg_report['passed'], neg_report['total']))
             if neg_report else '报告未生成',
             cases_ok, len(behavior_cases),
             exists(os.path.join(ROOT, 'tests', 'run_all_negative.py'))))

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

    # ================= 第十阶段：DESIGN_PLAN.md A22–A40 =================
    import glob
    sys.path.insert(0, os.path.join(ROOT, 'scripts'))
    import spec_layout
    import kpi as kpi_mod

    def latest(pattern, require=None):
        ds = sorted(glob.glob(os.path.join(ROOT, pattern)))
        # Stage-A tick runs share runs/<scenario>/ but have no kpi.json /
        # scenario.json. Only analytic scenario runs qualify here.
        if require:
            ds = [d for d in ds if os.path.isfile(os.path.join(d, require))]
        return ds[-1] if ds else None

    def digest(obj):
        return hashlib.sha256(json.dumps(obj, ensure_ascii=False,
                                         sort_keys=True).encode('utf-8')).hexdigest()

    CFG = os.path.join(ROOT, 'config')
    spec_rep = jload(os.path.join(OUT, 'spec_layout_report.json'))
    # 布局来源以「本次运行实际用的 spec」为准：报告里记了路径，变体（用户自选功能区的
    # 扩仓版）也能被同一套 A1–A40 验。写死 layout_spec.json 会让 A23 在变体上误报。
    spec_rel = (spec_rep.get('spec') or 'config/layout_spec.json').replace('\\', '/')
    spec_path = spec_rel if os.path.isabs(spec_rel) else os.path.join(ROOT, spec_rel)
    if not os.path.exists(spec_path):
        spec_path = os.path.join(CFG, 'layout_spec.json')
    spec = jload(spec_path)

    # A22 spec / 四层配置 schema 合法
    cfg_files = ['layout_spec.json', 'fleet_spec.json', 'demand_spec.json',
                 'layout_spec_f4.json', 'layout_spec_wide.json']
    cfg_files += ['scenarios/' + os.path.basename(p) for p in
                  glob.glob(os.path.join(CFG, 'scenarios', '*.json'))]
    cfg_files += ['schemas/' + os.path.basename(p) for p in
                  glob.glob(os.path.join(CFG, 'schemas', '*.json'))]
    cfg_bad = []
    for f in cfg_files:
        try:
            jload(os.path.join(CFG, f))
        except Exception as e:
            cfg_bad.append('%s:%s' % (f, e))
    l1 = spec_layout.check_schema(spec)
    ok22 = not cfg_bad and not l1 and spec_rep['verdict'] == 'PASS' \
        and 'L1' not in spec_rep.get('issue_kinds', {})
    check('A22 四层配置与 schema 合法', ok22,
          'config_files=%s 解析失败=%s；L1 issues=%s；spec verdict=%s'
          % (len(cfg_files), cfg_bad[:2], len(l1), spec_rep['verdict']))

    # A23/A36 展开确定性与 seed 敏感性（独立重算，不读自报 hash）
    e1 = spec_layout.expand(spec)
    e2 = spec_layout.expand(spec)
    e3 = jload(os.path.join(OUT, 'spec_layout.json'))
    h1, h2, h3 = digest(e1), digest(e2), digest(e3)
    check('A23 spec 展开确定性（两次 + 落盘一致）', h1 == h2 == h3,
          'sha=%s / %s / %s' % (h1[:16], h2[:16], h3[:16]))
    spec_seed = json.loads(json.dumps(spec))
    spec_seed['site']['seed'] = spec['site']['seed'] + 1
    h_seed = digest(spec_layout.expand(spec_seed))
    check('A36 改 seed 后展开结果改变', h_seed != h1,
          'seed=%s sha=%s；seed+1 sha=%s'
          % (spec['site']['seed'], h1[:16], h_seed[:16]))

    # A24 多布局审计（当前 / F4 / 加宽）
    variants = {}
    vnames = ['layout_spec', 'layout_spec_f4', 'layout_spec_wide']
    if os.path.exists(os.path.join(CFG, 'layout_spec_areas.json')):
        vnames.append('layout_spec_areas')     # 用户自选功能区的扩仓变体
    for name in vnames:
        s = jload(os.path.join(CFG, name + '.json'))
        lay = spec_layout.expand(s)
        iss, wrn, mtx, radius = spec_layout.validate(lay, s)
        variants[name] = {'issues': len(iss), 'mutex': len(mtx),
                          'racks': lay['summary']['racks'],
                          'warehouses': lay['summary']['warehouses']}
    ok24 = (all(v['issues'] == 0 for v in variants.values())
            and variants['layout_spec_wide']['mutex'] <
            variants['layout_spec']['mutex']
            and variants['layout_spec_f4']['warehouses'] == 5)
    # 供 report_status.py 从 JSON 引用，避免文档手写数字（DESIGN_PLAN §0.2 规则 9）
    with open(os.path.join(OUT, 'spec_layout_variants.json'), 'w',
              encoding='utf-8') as f:
        json.dump(variants, f, ensure_ascii=False, indent=2)
    check('A24 多布局审计：%d 个 spec 全 PASS 且加宽方案降低互斥' % len(vnames), ok24,
          'baseline=%s；F4=%s；wide=%s' %
          (variants['layout_spec'], variants['layout_spec_f4'],
           variants['layout_spec_wide']))

    # A25/A26/A27/A32/A35 场景层
    base_dir = latest('runs/baseline/*', require='scenario.json')
    peak_dir = latest('runs/peak/*', require='scenario.json')
    fail_dir = latest('runs/robot_failure/*', require='scenario.json')
    s50_dir = latest('runs/scale_50/*', require='scenario.json')
    scene_ok = all(d and os.path.isdir(d) for d in
                   (base_dir, peak_dir, fail_dir, s50_dir))
    if scene_ok:
        bsc = jload(os.path.join(base_dir, 'scenario.json'))
        blay = jload(os.path.join(base_dir, 'layout.json'))
        bev = jlines(os.path.join(base_dir, 'events.jsonl'))
        bkpi = jload(os.path.join(base_dir, 'kpi.json'))
        bmanifest = jload(os.path.join(base_dir, 'manifest.json'))
        recomputed = kpi_mod.compute_kpi(
            bsc, blay, bev, jload(os.path.join(CFG, 'fleet_spec.json')),
            jload(os.path.join(CFG, 'demand_spec.json')),
            spec_issues=bmanifest.get('layout_issues', 0), wall_clock_s=0.0)
        stable = {k: v['value'] for k, v in recomputed['metrics'].items()
                  if k != 'plan_wall_clock_s'}
        saved = {k: v['value'] for k, v in bkpi['metrics'].items()
                 if k != 'plan_wall_clock_s'}
        ok25 = (stable == saved and bmanifest['seed'] == bsc['seed']
                and bkpi['audit']['verdict'] == 'PASS')
        check('A25 baseline 场景可复现（独立重算 KPI）', ok25,
              'metrics=%s 全部一致=%s；seed=%s'
              % (len(stable), stable == saved, bmanifest['seed']))

        pa = jload(os.path.join(peak_dir, 'assertions.json'))
        pk = jload(os.path.join(peak_dir, 'kpi.json'))['metrics']
        bt = bkpi['metrics']['throughput_orders_per_h']['value']
        pt = pk['throughput_orders_per_h']['value']
        ok26 = pa['verdict'] == 'PASS' and pt >= 1.5 * bt - 1e-6 and \
            len(pa['assertions']) >= 1
        check('A26 peak 吞吐 >=1.5×baseline 且断言可失败', ok26,
              'baseline=%s peak=%s ratio=%.3f；断言=%s'
              % (bt, pt, pt / max(bt, 1e-9), len(pa['assertions'])))

        fa = jload(os.path.join(fail_dir, 'assertions.json'))
        fsc = jload(os.path.join(fail_dir, 'scenario.json'))
        fk = jload(os.path.join(fail_dir, 'kpi.json'))['metrics']
        faults = sum(1 for d in fsc['disruptions'] if d['type'] == 'robot_down')
        ok27 = (fa['verdict'] == 'PASS' and faults >= 3
                and fk['fault_count']['value'] >= 3
                and fk['replans']['value'] >= 1)
        check('A27 故障场景 fault_count>=3 且触发改派', ok27,
              'faults=%s replans=%s verdict=%s'
              % (faults, fk['replans']['value'], fa['verdict']))

        allm = bkpi['metrics']
        bad_formula = [k for k, m in allm.items()
                       if not m.get('definition') or not m.get('formula')
                       or not m.get('source')]
        null_no_reason = [k for k, m in allm.items()
                          if m['value'] is None and not m.get('reason')]
        ok32 = not bad_formula and not null_no_reason and len(allm) >= 10
        check('A32 每个 KPI 有 definition/formula/source', ok32,
              'kpis=%s 缺字段=%s null无理由=%s'
              % (len(allm), bad_formula[:3], null_no_reason[:3]))

        s50 = jload(os.path.join(s50_dir, 'kpi.json'))['metrics']
        ok35 = (s50['fleet_size']['value'] == 50
                and s50['plan_wall_clock_s']['value'] < 300)
        check('A35 50 车场景 plan_wall_clock <300 s', ok35,
              'fleet=%s wall_clock=%ss'
              % (s50['fleet_size']['value'], s50['plan_wall_clock_s']['value']))
    else:
        for name in ('A25 baseline 场景可复现', 'A26 peak 吞吐',
                     'A27 故障场景', 'A32 KPI 公式', 'A35 50 车规模'):
            check(name, False, 'runs/ 场景产物缺失，请先跑 scenario_run.py --all')

    # A28/A29 车队状态机与电量（独立复算每条迁移）
    import fleet_sim as fleet_mod
    fr = jload(os.path.join(OUT, 'fleet_report.json'))
    fe = jlines(os.path.join(OUT, 'fleet_events.jsonl'))
    illegal_indep = [e for e in fe
                     if e['to_state'] not in fleet_mod.ALLOWED.get(e['from_state'], set())
                     or (e['from_state'], e['to_state']) in fleet_mod.FORBIDDEN]
    ok28 = fr['illegal_count'] == 0 and not illegal_indep
    check('A28 车队状态机无非法迁移', ok28,
          'transitions=%s 独立复算非法=%s' % (len(fe), len(illegal_indep)))
    reserve = jload(os.path.join(CFG, 'fleet_spec.json'))['battery']['reserve_pct']
    batt_bad = [b for b in fr['battery'] if b['min_pct'] < reserve]
    ok29 = not batt_bad and fr['min_battery_pct'] >= reserve
    check('A29 电量全程 >=reserve 且充电成对', ok29,
          'min_battery=%s%% reserve=%s%% 越界=%s'
          % (fr['min_battery_pct'], reserve, len(batt_bad)))

    # A30/A31 任务时间与截止期
    sch_all = jload(os.path.join(DATA, 'agent_schedules.json'))['schedules']
    t_bad = [a for a, s in sch_all.items()
             if s['departure_t_s'] + 1e-6 < s['t_request_s']]
    ok30 = not t_bad
    check('A30 所有车 start >= t_request', ok30,
          'agents=%s 违反=%s' % (len(sch_all), t_bad[:3]))
    d_bad = [a for a, s in sch_all.items()
             if abs(s['tardiness_s'] - max(0.0, s['t_end_s'] - s['deadline_s'])) > 0.02]
    ok31 = not d_bad and pm.get('max_tardiness_s') is not None
    check('A31 tardiness = max(0, t_end - deadline) 可报告', ok31,
          '不符合=%s；max_tardiness=%s' % (d_bad[:3], pm.get('max_tardiness_s')))

    # A33 单一真相源：状态表数字必须来自 JSON
    status = open(os.path.join(OUT, 'CURRENT_STATUS.md'), encoding='utf-8').read()
    ok33 = ('自动生成，请勿手改' in status
            and str(tr['min_pair_distance_m']) in status
            and str(pm['duration_s']) in status
            and str(geo['min_path_to_obstacle_m']) in status)
    check('A33 状态表数字由 JSON 生成（非手写）', ok33,
          'min_pair=%s duration=%s clearance=%s 均在状态表中'
          % (tr['min_pair_distance_m'], pm['duration_s'],
             geo['min_path_to_obstacle_m']))

    # A34 真实数据适配器
    cr_path = os.path.join(OUT, 'csv_adapter_report.json')
    cr = jload(cr_path) if exists(cr_path) else {}
    cev = jlines(os.path.join(OUT, 'csv_events.jsonl')) if exists(
        os.path.join(OUT, 'csv_events.jsonl')) else []
    csv_bad = [e['order_id'] for e in cev
               if e['from'] not in locmap or e['to'] not in locmap
               or e['return_to'] not in locmap]
    ok34 = cr.get('verdict') == 'PASS' and cr.get('rows_out', 0) > 0 and not csv_bad
    check('A34 CSV 适配器输出 schema 合法且引用完整', ok34,
          'rows=%s 引用错误=%s' % (cr.get('rows_out'), csv_bad[:3]))

    # A37 负测试总数 >=12
    neg_reports = [jload(os.path.join(OUT, f)) for f in
                   ('negative_tests.json', 'spec_negative_tests.json',
                    'scenario_negative_tests.json')
                   if exists(os.path.join(OUT, f))]
    neg_total = sum(x.get('total', 0) for x in neg_reports)
    neg_pass = sum(x.get('passed', 0) for x in neg_reports)
    ok37 = len(neg_reports) == 3 and neg_total >= 12 and neg_pass == neg_total
    check('A37 负测试 >=12 且全部能失败', ok37,
          'reports=%s total=%s passed=%s'
          % (len(neg_reports), neg_total, neg_pass))

    # A38 文档完整（v0.1.0 起英文 Hero 首页 + 中文版 + 独立 getting-started）
    docs = ('ENTERPRISE_SETUP.md', 'PARAMETER_GUIDE.md', 'REAL_DATA_SOURCES.md',
            'getting-started.md', 'cli.md', 'data-contracts.md')
    doc_bad = [d for d in docs if not exists(os.path.join(ROOT, 'docs', d))]
    zh = open(os.path.join(ROOT, 'README.zh-CN.md'), encoding='utf-8').read() \
        if exists(os.path.join(ROOT, 'README.zh-CN.md')) else ''
    en = open(os.path.join(ROOT, 'README.md'), encoding='utf-8').read()
    zh_ok = all(x in zh for x in ('参数', '场景', '排查'))
    en_ok = 'getting-started' in en and 'What it is not' in en
    ok38 = not doc_bad and zh_ok and en_ok
    check('A38 参数表/场景表/故障排查文档齐全', ok38,
          '缺文档=%s 中文README三节=%s 英文首页链接=%s'
          % (doc_bad, zh_ok, en_ok))

    # A39 新增数据源许可登记
    notice = open(os.path.join(ROOT, 'THIRD_PARTY_NOTICES.md'), encoding='utf-8').read()
    ok39 = 'UCI' in notice and 'CC BY 4.0' in notice and 'DESIGN_PLAN' in notice
    check('A39 新增数据源与依赖已登记许可', ok39,
          'NOTICE 含 UCI=%s CC BY 4.0=%s DESIGN_PLAN=%s'
          % ('UCI' in notice, 'CC BY 4.0' in notice, 'DESIGN_PLAN' in notice))

    # A40 全新空目录复现
    cl_path = os.path.join(OUT, 'cleanroom_report.json')
    cl = jload(cl_path) if exists(cl_path) else {}
    ok40 = cl.get('verdict') == 'PASS' and cl.get('steps_run') == cl.get('steps_total') \
        and all(v.get('identical') for v in cl.get('files', {}).values())
    check('A40 全新空目录复现数据层（无 .venv）', ok40,
          'steps=%s/%s identical=%s/%s'
          % (cl.get('steps_run'), cl.get('steps_total'),
             sum(1 for v in cl.get('files', {}).values() if v.get('identical')),
             len(cl.get('files', {}))))

    import re

    def _anum(item):
        m = re.match(r'^A(\d+)', item['item'])
        return int(m.group(1)) if m else 999

    R.sort(key=_anum)   # 报告按 A1 → A40 顺序输出，与 DESIGN_PLAN §15 一致
    # --json is a read-only machine-readable query (used by pytest acceptance):
    # it must NOT rewrite the report, otherwise out/validate_report.json drifts
    # from its sidecar / manifest hashes. The normal run still persists it.
    if not FLAGS.get('json'):
        with open(os.path.join(OUT, 'validate_report.json'), 'w', encoding='utf-8') as f:
            json.dump(R, f, ensure_ascii=False, indent=2)
    fails = [r for r in R if r['status'] != 'PASS']
    if FLAGS.get('junit'):
        import xml.etree.ElementTree as ET
        suite = ET.Element('testsuite', name='dc-twin-acceptance',
                           tests=str(len(R)), failures=str(len(fails)))
        for r in R:
            case = ET.SubElement(suite, 'testcase', name=r['item'], classname='validate')
            if r['status'] != 'PASS':
                ET.SubElement(case, 'failure', message=r['detail'])
        ET.ElementTree(suite).write(FLAGS['junit'], encoding='utf-8', xml_declaration=True)
    if FLAGS.get('json'):
        print(json.dumps({'schema_version': '1.0', 'kind': 'validate_run',
                          'tier': FLAGS.get('tier', 'full'), 'checks': R,
                          'passed': len(R) - len(fails), 'total': len(R)},
                         ensure_ascii=False))
    else:
        for r in R:
            print('[%s] %s — %s' % (r['status'], r['item'], r['detail']))
        print('SUMMARY', len(R) - len(fails), '/', len(R), 'PASS')
    if fails and not FLAGS.get('json'):
        print('FAILED:', [r['item'] for r in fails])
        sys.exit(1)
    if fails:
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    import argparse
    import subprocess

    ap = argparse.ArgumentParser(description='A1-A40 acceptance gate')
    ap.add_argument('--tier', choices=('data', 'render', 'full'), default='full',
                    help='data = no Blender (delegates to validate_data.py)')
    ap.add_argument('--json', action='store_true', help='machine-readable stdout')
    ap.add_argument('--junit', default=None, help='write JUnit XML for CI')
    ap.add_argument('--check', action='store_true',
                    help='verify artifacts against out/manifest.json first')
    a = ap.parse_args()
    if a.tier == 'data':
        cmd = [sys.executable, os.path.join(ROOT, 'scripts', 'validate_data.py')]
        if a.json:
            cmd.append('--json')
        if a.junit:
            cmd += ['--junit', a.junit]
        if a.check:
            cmd.append('--check')
        sys.exit(subprocess.call(cmd, cwd=ROOT))
    if a.check:
        from manifest import check_manifest
        rc = check_manifest()
        if rc:
            sys.exit(rc)
    FLAGS.update(vars(a))
    main()
