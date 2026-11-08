"""gen_tasks.py — 事件 → 闭环作业任务 → AGV 分配（审计 §3.5 / P0-5 的核心链路）。

第六阶段：一张订单 = 一台车的一个完整作业循环
    HOME → 取货(from) → 交付(to) → HOME(return_to)
事件数 = 车辆数，全部 ASSIGNED，PENDING=0；任务显式携带 home_id / return_to，
plan_paths.py 据此生成「去程 / 载货 / 回程」三段 A* 轨迹。

产出：data/tasks.json
"""
import collections
import json
import math
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')

LAYOUT = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
EVENTS = [json.loads(l) for l in open(os.path.join(DATA, 'events.jsonl'), encoding='utf-8') if l.strip()]
SKUS = {s['sku'] for s in json.load(open(os.path.join(DATA, 'skus.json'), encoding='utf-8'))['skus']}

LOC = {}
ROBOTS = {}
for w in LAYOUT['warehouses']:
    LOC[w['id']] = {l['id']: l for l in w['locations']}
    ROBOTS[w['id']] = [{'id': rb['id'],
                        'pos': (rb['position'][0], rb['position'][2])}
                       for rb in w['spawn']['robots']]


def main():
    # 引用完整性：events 的 from/to / sku 必须存在
    problems = []
    for e in EVENTS:
        pool = LOC.get(e['warehouse_id'], {})
        for k in ('from', 'to'):
            if e[k] not in pool:
                problems.append('%s %s=%s not in locations' % (e['event_id'], k, e[k]))
        if e['sku'] not in SKUS:
            problems.append('%s sku=%s not in skus.json' % (e['event_id'], e['sku']))
    if problems:
        raise SystemExit('GEN_TASKS_FAIL: ' + '; '.join(problems[:5]))

    by_wh = collections.defaultdict(list)
    for e in EVENTS:
        by_wh[e['warehouse_id']].append(e)

    tasks = []
    for wid, evs in by_wh.items():
        robots = ROBOTS[wid]

        def trip_cost(r, e):
            a = LOC[wid][e['from']]['access_point']
            b = LOC[wid][e['to']]['access_point']
            home = LOC[wid][e['return_to']]['access_point']
            # 闭环全程：home -> 取货 -> 交付 -> home
            return (math.hypot(r['pos'][0] - a[0], r['pos'][1] - a[1])
                    + math.hypot(b[0] - a[0], b[1] - a[1])
                    + math.hypot(home[0] - b[0], home[1] - b[1]))

        used_r, used_e = set(), set()
        robot_ids = {r['id'] for r in robots}
        # 优先把「沿排间通道深入取货」任务交给生成它的那台车，
        # 保证每台车走自己那条通道、路径空间分离（用户指出的入口打转问题）
        for e in evs:
            aid = e.get('preferred_agent')
            if (aid in robot_ids and aid not in used_r
                    and e['event_id'] not in used_e):
                used_r.add(aid)
                used_e.add(e['event_id'])
                tasks.append(_task(e, aid, 'ASSIGNED', None))
        # 其余任务用全局最小成本匹配补漏，避免"最后一台车捡最远任务"
        pairs = sorted((trip_cost(r, e), r['id'], e)
                       for r in robots for e in evs)
        for _, rid, e in pairs:
            if rid in used_r or e['event_id'] in used_e:
                continue
            used_r.add(rid)
            used_e.add(e['event_id'])
            tasks.append(_task(e, rid, 'ASSIGNED', None))
        for e in evs:
            if e['event_id'] not in used_e:
                tasks.append(_task(e, None, 'PENDING', 'no free AGV in warehouse'))
    tasks.sort(key=lambda t: t['task_id'])
    assigned = sum(1 for t in tasks if t['status'] == 'ASSIGNED')
    out = {
        'method': 'one closed-loop order -> one AGV; preferred_agent from event; '
                  'all events ASSIGNED; no PENDING',
        'summary': {
            'events': len(EVENTS), 'tasks': len(tasks),
            'assigned': assigned, 'pending': len(tasks) - assigned,
            'agents_total': sum(len(v) for v in ROBOTS.values()),
            'agents_with_task': assigned,
            'event_types_executed': sorted({e['type'] for e in EVENTS}),
            'closed_loop_orders': assigned,
            'referential_integrity_errors': len(problems),
        },
        'tasks': tasks,
    }
    with open(os.path.join(DATA, 'tasks.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('GEN_TASKS_OK', json.dumps(out['summary'], ensure_ascii=False))


def _task(e, agent_id, status, reason):
    t = {
        'task_id': 'T' + e['event_id'][1:],
        'event_id': e['event_id'], 'agent_id': agent_id, 'status': status,
        'warehouse_id': e['warehouse_id'],
        'legs': [{'from': e['from'], 'to': e['to'],
                  'sku': e['sku'], 'qty': e['qty']}],
        'home_id': e['return_to'], 'return_to': e['return_to'],
        'route_kind': e.get('route_kind', 'CLOSED_LOOP'),
        'phases': e.get('phases', []),
        'pick_location': e['from'], 'drop_location': e['to'],
        't_request': e['t_request'], 'deadline': e['deadline'],
        'priority': e['priority'],
    }
    if reason:
        t['pending_reason'] = reason
    return t


if __name__ == '__main__':
    main()
