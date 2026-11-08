"""gen_cargo.py — 订单货物状态机（审计 P1-1）。

每张闭环订单生成一件货物，状态按真实时间轴推进：

    IN_STOCK / INBOUND_DOCK  →  LOADED(t_pick, 跟随 AGV)  →  DELIVERED(t_drop)

同时输出库位库存增减（只统计 SHELF 库位；月台/打包台视为外部接口），
使「托盘不是静态布景」成为可核验的数据，而不是视频里的视觉暗示。

只依赖标准库，读取 tasks.json / paths.jsonl / layout.json / events.jsonl。
运行：python scripts/gen_cargo.py   产出：data/cargo_state.json
"""
import collections
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')

TASKS = json.load(open(os.path.join(DATA, 'tasks.json'), encoding='utf-8'))
LAYOUT = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
LOCMAP = {l['id']: l for w in LAYOUT['warehouses'] for l in w['locations']}
EVENTS = {e['event_id']: e for e in
          (json.loads(x) for x in open(os.path.join(DATA, 'events.jsonl'), encoding='utf-8')
           if x.strip())}

BY_AGENT = collections.defaultdict(list)
for line in open(os.path.join(DATA, 'paths.jsonl'), encoding='utf-8'):
    if line.strip():
        p = json.loads(line)
        BY_AGENT[p['agent_id']].append(p)
for k in BY_AGENT:
    BY_AGENT[k].sort(key=lambda p: p['t'])


def main():
    cargos, transitions, inventory = [], [], collections.defaultdict(int)
    problems = []
    for t in TASKS['tasks']:
        if t['status'] != 'ASSIGNED':
            continue
        aid, ev = t['agent_id'], EVENTS[t['event_id']]
        ps = BY_AGENT[aid]
        picks = [p for p in ps if p.get('phase') == 'PICK']
        drops = [p for p in ps if p.get('phase') == 'DROP']
        if not picks or not drops:
            problems.append('%s missing PICK/DROP keyframes' % t['task_id'])
            continue
        tp, td = picks[0]['t'], drops[0]['t']
        if td <= tp:
            problems.append('%s drop before pick' % t['task_id'])
            continue
        src, dst = t['legs'][0]['from'], t['legs'][0]['to']
        src_kind, dst_kind = LOCMAP[src]['kind'], LOCMAP[dst]['kind']
        qty = t['legs'][0]['qty']
        if src_kind == 'SHELF':
            inventory[src] -= qty
        if dst_kind == 'SHELF':
            inventory[dst] += qty
        cid = 'CARGO_DYN_%s' % t['task_id']
        states = [
            {'state': 'IN_STOCK' if src_kind == 'SHELF' else 'INBOUND_DOCK',
             't': 0.0, 'location': src, 'agent_id': None},
            {'state': 'LOADED', 't': tp, 'location': src, 'agent_id': aid},
            {'state': 'DELIVERED', 't': td, 'location': dst, 'agent_id': None},
        ]
        cargos.append({
            'cargo_id': cid, 'task_id': t['task_id'], 'event_id': t['event_id'],
            'order_type': ev['type'], 'agent_id': aid,
            'sku': t['legs'][0]['sku'], 'qty': qty,
            'pickup_location': src, 'drop_location': dst,
            'pickup_ap': LOCMAP[src]['access_point'],
            'drop_ap': LOCMAP[dst]['access_point'],
            't_pick_s': tp, 't_drop_s': td,
            'carried_duration_s': round(td - tp, 2),
            'states': states,
            'inventory_delta': {k: v for k, v in
                                ((src, -qty) if src_kind == 'SHELF' else (None, 0),
                                 (dst, +qty) if dst_kind == 'SHELF' else (None, 0))
                                if k},
        })
        for a, b in zip(states, states[1:]):
            transitions.append({'t': b['t'], 'cargo_id': cid,
                                'from_state': a['state'], 'to_state': b['state'],
                                'location': b['location'], 'agent_id': aid})
    if problems:
        raise SystemExit('CARGO_STATE_FAIL: ' + '; '.join(problems[:5]))

    out = {
        'method': 'order-level cargo state machine; pallet follows AGV from '
                  'PICK to DROP, then stays at delivery point',
        'summary': {
            'orders': len(cargos),
            'loaded': sum(1 for c in cargos if c['states'][1]['state'] == 'LOADED'),
            'delivered': sum(1 for c in cargos if c['states'][2]['state'] == 'DELIVERED'),
            'shelf_locations_changed': len(inventory),
            'inventory_net_change': sum(inventory.values()),
            'state_transitions': len(transitions),
            'referential_integrity_errors': len(problems),
        },
        'inventory_delta_by_location': dict(inventory),
        'state_transitions': sorted(transitions, key=lambda x: (x['t'], x['cargo_id'])),
        'cargos': cargos,
        'limitations': '箱数=订单数(21)，未模拟拆箱/托盘层级/批次；'
                       '库位库存只记录增减，不接 WMS 实时账',
    }
    with open(os.path.join(DATA, 'cargo_state.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('CARGO_OK', json.dumps(out['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
