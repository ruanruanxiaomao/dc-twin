"""kpi.py — 运营 KPI 计算（DESIGN_PLAN §10.6 / §9 Step 8）。

每个 KPI 必须带 `value / unit / definition / formula / source`；
没有数据源的标 `value: null + reason`，绝不编数（§8 规则）。

模型分两类，**每个 KPI 都标清来源**：
  · [measured/main-chain] 直接读主链 out/path_metrics.json、traffic_report.json
    （21 单 / 117.62 s 的 demo 窗口，真实 A* 结果）；
  · [analytic/M/M/c] 场景层解析排队近似：订单泊松到达、c 台车、平均服务时间 S，
    用 Erlang-C 求等待与队列。**不是逐车 A* 仿真**，不能替代主链校验。

作为脚本：python scripts/kpi.py --run runs/<scenario>/<timestamp>
  → 从该 run 的 scenario/layout/events 独立重算并覆盖 kpi.json + 写 kpi_audit.json。
"""
import argparse
import json
import math
import os
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(ROOT, 'out')


def jload(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    return json.load(open(path, encoding='utf-8'))


def erlang_c(lam, service_s, servers):
    """M/M/c 等待概率与平均排队等待（Erlang-C）；返回 (P_wait, Wq, rho, Lq)。"""
    a = lam * service_s
    c = max(1, int(servers))
    rho = a / c
    if a >= c - 1e-9:
        return 1.0, max(0.0, (a - c) * service_s / max(c, 1)), min(rho, 2.0), max(0.0, a - c)
    terms, t = [], 1.0
    for k in range(c):
        if k:
            t *= a / k
        terms.append(t)
    last = t * a / c
    denom = sum(terms) + last * (c / (c - a))
    p_wait = (last * (c / (c - a))) / denom if denom > 0 else 0.0
    wq = p_wait * service_s / (c - a) if c > a else 0.0
    return p_wait, wq, rho, lam * wq


def compute_kpi(scenario, layout, events, fleet_spec, demand_spec,
                main=None, spec_issues=0, wall_clock_s=0.0):
    main = main or {}
    pm = main.get('path_metrics', jload(os.path.join(ROOT, 'data', 'path_metrics.json')))
    tr = main.get('traffic_report', jload(os.path.join(OUT, 'traffic_report.json')))
    sim = scenario['simulation']
    duration = sim['duration_s']
    ov = scenario.get('overrides', {})
    per_hour = ov.get('demand.orders.per_hour', demand_spec['orders']['per_hour'])
    fleet_size = ov.get('fleet.size', sum(d['count'] for d in fleet_spec['deployment']))
    disruptions = scenario.get('disruptions', [])
    faults = [d for d in disruptions if d['type'] == 'robot_down']
    blocked = [d for d in disruptions if d['type'] == 'dock_blocked']

    speed = fleet_spec['models'][0]['speed_mps']
    dwell = jload(os.path.join(ROOT, 'data', 'config.json')).get('agv', {}).get('dwell_pick_s', 2.0)
    avg_path = pm.get('avg_path_m', 84.05)
    service_s = avg_path / speed + 2 * dwell
    effective = max(1, int(fleet_size) - len(faults) * (600.0 / max(duration, 1)))
    effective = max(1, int(round(effective)))
    lam = per_hour / 3600.0
    p_wait, wq, rho, lq = erlang_c(lam, service_s, effective)

    n_orders = len(events)
    lines = sum(e.get('qty', 1) for e in events)
    doors = sum(len(d.get('doors', [])) or 1 for w in layout['warehouses'] for d in w['docks'])
    stations = sum(len(w['stations']) for w in layout['warehouses'])
    dock_orders = sum(1 for e in events if e['type'] in ('INBOUND', 'OUTBOUND', 'REPLENISH'))
    blocked_docks = {d['dock'] for d in blocked}
    reassigned = sum(1 for e in events if e.get('to') in blocked_docks or e.get('from') in blocked_docks)
    dock_svc = fleet_spec.get('dock_service', {}).get('service_time_s', 180)
    station_svc = fleet_spec.get('station_service', {}).get('service_time_s', 45)
    energy = avg_path * fleet_spec['models'][0]['consumption_kwh_per_m'] + \
        fleet_spec['models'][0].get('idle_kw', 0.05) * service_s / 3600.0

    def M(value, unit, definition, formula, source, reason=None):
        d = {'value': value, 'unit': unit, 'definition': definition,
             'formula': formula, 'source': source}
        if reason:
            d['reason'] = reason
        return d

    main_src = 'out/path_metrics.json + out/traffic_report.json (main chain, 21 orders)'
    ana = 'analytic M/M/c (scenario layer, NOT per-vehicle A*)'
    k = {}
    k['orders_generated'] = M(n_orders, 'orders', '本次场景生成的订单数',
                              'N = Poisson(per_hour/3600 * duration_s) samples <= duration_s',
                              'runs/<id>/events.jsonl')
    k['throughput_orders_per_h'] = M(round(min(lam, effective / service_s) * 3600, 3), 'orders/h',
                                     '解析模型下的稳定吞吐（到达率与车队容量取小）',
                                     'min(lambda, c/S) * 3600; lambda=per_hour/3600, S=avg_path/speed+2*dwell', ana)
    k['throughput_lines_per_h'] = M(round(min(lam, effective / service_s) * 3600 * (lines / max(n_orders, 1)), 3),
                                    'lines/h', '按平均订单行数折算的行吞吐',
                                    'throughput_orders_per_h * (sum(qty)/N)', ana)
    k['on_time_rate'] = M(pm.get('on_time_rate'), 'ratio',
                          '主链 demo 窗口中 t_end <= deadline 的订单比例',
                          'count(t_end <= deadline) / orders', main_src,
                          None if pm.get('on_time_rate') is not None else '主链报告缺失')
    k['max_tardiness_s'] = M(pm.get('max_tardiness_s'), 's', '主链 demo 窗口最大超期',
                             'max(0, t_end - deadline)', main_src)
    k['avg_wait_s'] = M(round(wq, 3), 's', '解析模型平均排队等待（Erlang-C）',
                        'Wq = P_wait * S / (c - a), a = lambda*S', ana)
    k['utilization'] = M(round(min(rho, 1.0), 4), 'ratio', '车队利用率',
                         'rho = lambda*S/c', ana)
    k['idle_rate'] = M(round(max(0.0, 1 - min(rho, 1.0)), 4), 'ratio', '车队空闲率',
                       '1 - utilization', ana)
    k['energy_per_order_kwh'] = M(round(energy, 6), 'kWh/order', '每单估算能耗',
                                  'avg_path_m * consumption_kwh_per_m + idle_kw * S/3600', ana)
    k['dock_utilization'] = M(round(min(1.0, dock_orders * dock_svc / max(doors * duration, 1)), 4),
                              'ratio', '月台门占用率',
                              'dock_orders * service_time_s / (doors * duration_s)', ana)
    # 低负载下 Lq 可能是 1e-40 量级的浮点残差，ceil 会把它变成 1（假排队），
    # 所以解析队列用 round 归零；但「月台被封锁 → 有订单必须改派」是离散事实，
    # 只要 reassigned>0 就至少产生 1 个等待位（不能用连续比例把它抹掉）。
    queue_reassign = 1 if reassigned > 0 else 0
    k['max_dock_queue'] = M(int(round(lq)) + queue_reassign,
                            'vehicles', '最大月台排队长度（解析队列 + 封锁改派）',
                            'round(Lq) + (1 if reassigned>0 else 0)', ana)
    k['station_utilization'] = M(round(min(1.0, sum(1 for e in events if e['type'] == 'PICK') *
                                           station_svc / max(stations * duration, 1)), 4),
                                 'ratio', '打包/分拣站占用率',
                                 'pick_orders * station_service_s / (stations * duration_s)', ana)
    k['congestion_index'] = M(round(wq / max(avg_path, 1), 6), 's/m',
                              '单位路径长度的等待时间',
                              'avg_wait_s / avg_path_m', ana)
    k['strict_collisions'] = M(tr.get('strict_violations'), 'count',
                               '主链严格口径采样点中心距 < 2*AGV_RADIUS 的次数',
                               'sum over t of pairs with dist < 2R (dt=0.05 s)', main_src,
                               None if tr.get('strict_violations') is not None else '主链报告缺失')
    k['path_to_euclid_ratio'] = M(pm.get('path_to_euclid_ratio'), 'ratio',
                                  '主链路径长 / 任务点折线直线距离',
                                  'total_path_m / sum(|p_i -> p_i+1|)', main_src)
    k['predicted_on_time_rate'] = M(
        round(min(1.0, math.exp(-max(0.0, demand_spec['orders']['deadline_s']['P2'] - service_s) /
                                max(wq + service_s, 1e-6))), 4),
        'ratio', '解析模型预计准时率（指数尾近似，仅作趋势参考）',
        'exp(-max(0, deadline_P2 - S)/(Wq+S))', ana)
    k['fault_count'] = M(len(faults), 'count', '注入的机器人故障次数',
                         'count(disruptions[type=robot_down])', 'config/scenarios/*.json')
    k['replans'] = M(len(faults) + len(blocked), 'count', '扰动触发的改派/重规划次数',
                     'fault_count + count(disruptions[type=dock_blocked])', 'config/scenarios/*.json')
    k['reassigned'] = M(reassigned, 'orders', '因月台封锁需要改派的订单数',
                        'count(orders with from/to == blocked dock)', 'runs/<id>/events.jsonl')
    k['layout_issues'] = M(spec_issues, 'count', 'spec_layout 校验 FAIL 条数',
                           'len(spec_layout.validate().issues)', 'out/spec_layout_report.json')
    k['warehouses'] = M(len(layout['warehouses']), 'count', '仓库数',
                        'len(layout.warehouses)', 'runs/<id>/layout.json')
    k['robots'] = M(layout['summary']['robots'], 'count', '布局中的 AGV / HOME 数',
                    'sum(len(w.spawn.robots))', 'runs/<id>/layout.json')
    k['fleet_size'] = M(int(fleet_size), 'count', '场景配置的车队规模',
                        'overrides.fleet.size or sum(fleet_spec.deployment.count)', 'config/scenarios/*.json')
    k['plan_wall_clock_s'] = M(round(wall_clock_s, 3), 's', '本次场景层运行墙钟时间',
                               'time.time() delta', 'runs/<id>/manifest.json')
    k['event_coverage'] = M(pm.get('event_coverage'), 'ratio', '主链订单执行覆盖率',
                            'assigned / events', main_src)

    missing = [name for name, m in k.items()
               if not m.get('definition') or not m.get('formula') or not m.get('source')]
    audit = {'total_kpis': len(k), 'with_formula': len(k) - len(missing),
             'missing_fields': missing,
             'null_kpis': [name for name, m in k.items() if m['value'] is None],
             'null_without_reason': [name for name, m in k.items()
                                     if m['value'] is None and not m.get('reason')],
             'verdict': 'PASS' if not missing else 'FAIL'}
    return {'metrics': k, 'audit': audit,
            'model': {'service_time_s': round(service_s, 3),
                      'lambda_per_s': lam, 'servers': effective,
                      'rho': round(rho, 5), 'p_wait': round(p_wait, 5),
                      'lq': round(lq, 5)},
            'note': 'analytic M/M/c for scenario-level what-if; per-vehicle A* '
                    'and collision verification remain in the main chain.'}


def recompute_run(run_dir):
    """从 run 目录独立重算 kpi.json（A32 独立复算）。"""
    scenario = jload(os.path.join(run_dir, 'scenario.json'))
    layout = jload(os.path.join(run_dir, 'layout.json'))
    events = [json.loads(l) for l in open(os.path.join(run_dir, 'events.jsonl'),
                                          encoding='utf-8') if l.strip()]
    cfg = os.path.join(ROOT, 'config')
    fleet = jload(os.path.join(cfg, 'fleet_spec.json'))
    demand = jload(os.path.join(cfg, 'demand_spec.json'))
    manifest = jload(os.path.join(run_dir, 'manifest.json'))
    out = compute_kpi(scenario, layout, events, fleet, demand,
                      spec_issues=manifest.get('layout_issues', 0),
                      wall_clock_s=manifest.get('duration_s', 0.0))
    json.dump(out, open(os.path.join(run_dir, 'kpi.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    json.dump(out['audit'], open(os.path.join(run_dir, 'kpi_audit.json'), 'w',
                                 encoding='utf-8'), ensure_ascii=False, indent=2)
    print('KPI_OK', len(out['metrics']), 'metrics;', out['audit'])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    args = ap.parse_args()
    d = args.run if os.path.isabs(args.run) else os.path.join(ROOT, args.run)
    out = recompute_run(d)
    raise SystemExit(0 if out['audit']['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
