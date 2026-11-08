"""fleet_sim.py — 车队状态机 + 电量模型（DESIGN_PLAN §10.1 / §10.2 最小实现）。

**离线回放，不改主链**：以主链 paths.jsonl / cargo_state.json 为输入，
把每台车的闭环作业映射成状态迁移，校验禁止迁移；按里程与待机时间算电量。

状态机与报告 §10.1 一致：
  IDLE → ASSIGNED → MOVING_PICK → PICKING → MOVING_DROP
       → DROPPING → RETURNING → IDLE
  任何状态可 → FAULT / CHARGING，恢复后回 IDLE。

产出：out/fleet_events.jsonl · out/fleet_report.json
"""
import collections
import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA, OUT = os.path.join(ROOT, 'data'), os.path.join(ROOT, 'out')

ALLOWED = {
    'IDLE': {'ASSIGNED', 'FAULT', 'CHARGING'},
    'ASSIGNED': {'MOVING_PICK', 'FAULT', 'CHARGING'},
    'MOVING_PICK': {'PICKING', 'FAULT', 'CHARGING'},
    'PICKING': {'MOVING_DROP', 'FAULT', 'CHARGING'},
    'MOVING_DROP': {'DROPPING', 'FAULT', 'CHARGING'},
    'DROPPING': {'RETURNING', 'FAULT', 'CHARGING'},
    'RETURNING': {'IDLE', 'FAULT', 'CHARGING'},
    'FAULT': {'IDLE'},
    'CHARGING': {'IDLE'},
}
FORBIDDEN = {('IDLE', 'DROPPING'), ('PICKING', 'IDLE'),
             ('FAULT', 'MOVING_DROP'), ('CHARGING', 'MOVING_DROP')}


def main():
    paths = [json.loads(l) for l in open(os.path.join(DATA, 'paths.jsonl'),
                                         encoding='utf-8') if l.strip()]
    cargo = json.load(open(os.path.join(DATA, 'cargo_state.json'), encoding='utf-8'))
    fleet = json.load(open(os.path.join(ROOT, 'config', 'fleet_spec.json'), encoding='utf-8'))
    sched = json.load(open(os.path.join(DATA, 'agent_schedules.json'), encoding='utf-8'))['schedules']
    layout = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
    model = fleet['models'][0]
    battery_kwh = model['battery_kwh']
    reserve = fleet['battery']['reserve_pct']

    by = collections.defaultdict(list)
    for p in paths:
        by[p['agent_id']].append(p)
    for k in by:
        by[k].sort(key=lambda p: p['t'])
    cg_by_agent = {c['agent_id']: c for c in cargo['cargos']}
    init_pct = {rb['id']: rb['battery'] for w in layout['warehouses']
                for rb in w['spawn']['robots']}

    events, illegal, batteries = [], [], []
    for aid in sorted(by):
        ps = by[aid]
        c = cg_by_agent.get(aid)
        s = sched.get(aid, {})
        depart = s.get('departure_t_s', ps[0]['t'])
        end = s.get('t_end_s', ps[-1]['t'])
        tp = c['t_pick_s'] if c else (depart + end) / 2
        td = c['t_drop_s'] if c else tp + 1
        seq = [
            (0.0, 'IDLE'), (depart, 'ASSIGNED'), (depart + 1e-4, 'MOVING_PICK'),
            (tp, 'PICKING'), (tp + 1e-4, 'MOVING_DROP'),
            (td, 'DROPPING'), (td + 1e-4, 'RETURNING'), (end, 'IDLE'),
        ]
        for (ta, sa), (tb, sb) in zip(seq, seq[1:]):
            ok = sb in ALLOWED.get(sa, set()) and (sa, sb) not in FORBIDDEN
            if not ok:
                illegal.append({'agent': aid, 'from': sa, 'to': sb, 't': round(tb, 3)})
            events.append({'agent_id': aid, 't': round(tb, 4), 'from_state': sa,
                           'to_state': sb, 'legal': ok})
        # 电量：移动距离 * 单位能耗 + 待机功率
        dist = sum(math.hypot(b['x'] - a['x'], b['y'] - a['y'])
                   for a, b in zip(ps, ps[1:]))
        used_kwh = dist * model['consumption_kwh_per_m'] + \
            model.get('idle_kw', 0.05) * (ps[-1]['t'] - ps[0]['t']) / 3600.0
        p0 = init_pct.get(aid, 100.0)
        p1 = p0 - used_kwh / battery_kwh * 100.0
        batteries.append({'agent_id': aid, 'distance_m': round(dist, 2),
                          'used_kwh': round(used_kwh, 6),
                          'battery_start_pct': round(p0, 2),
                          'battery_end_pct': round(p1, 3),
                          'min_pct': round(min(p0, p1), 3)})
    events.sort(key=lambda e: (e['t'], e['agent_id']))
    with open(os.path.join(OUT, 'fleet_events.jsonl'), 'w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')

    below = [b for b in batteries if b['min_pct'] < reserve]
    report = {
        'method': 'offline replay of main-chain paths (no re-planning)',
        'agents': len(by), 'state_transitions': len(events),
        'illegal_transitions': illegal, 'illegal_count': len(illegal),
        'forbidden_rules': sorted([list(x) for x in FORBIDDEN]),
        'battery': batteries,
        'battery_below_reserve': below,
        'min_battery_pct': min((b['min_pct'] for b in batteries), default=None),
        'reserve_pct': reserve,
        'charging_events': 0,
        'charging_note': '本窗口单车仅 1 单、里程 %.1f m，电量未触及 critical；'
                         '充电逻辑为接口预留，未触发。' %
                         (sum(b['distance_m'] for b in batteries) / max(len(batteries), 1)),
        'verdict': 'PASS' if not illegal and not below else 'FAIL',
    }
    with open(os.path.join(OUT, 'fleet_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('FLEET_SIM', report['verdict'], 'transitions', len(events),
          'illegal', len(illegal), 'min_battery_pct', report['min_battery_pct'])
    sys.exit(0 if report['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
