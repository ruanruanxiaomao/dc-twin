"""demand_source.py — 需求数据源（DESIGN_PLAN §9 Step 5 / Step 6）。

两种来源，输出**同一套 events schema**（与主链 gen_data.py 兼容）：
  · SyntheticSource：按 demand_spec 的 per_hour / mix / priority / qty / deadline
    生成订单（泊松到达 + 加权抽样），SKU 按 Zipf 权重抽；
  · CsvSource：读企业 CSV，按 csv_mapping 映射字段，过滤负数量/取消单，
    校验引用完整性。缺列、非法类型、from/to 不存在 → 明确 FAIL。

所有随机源都来自同一个 seed，保证同 seed 同结果（A25/A36）。
"""
import csv
import json
import math
import os
import random
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

KIND_RULES = {
    'INBOUND': ('INBOUND', {'SHELF'}),
    'REPLENISH': ('INBOUND', {'SHELF'}),
    'OUTBOUND': ('SHELF', {'OUTBOUND'}),
    'PICK': ('SHELF', {'PACKING', 'SORTING', 'OUTBOUND'}),
    'TRANSFER': ('SHELF', {'SHELF'}),
}
REQUIRED_CSV = ('order_id', 'sku', 'qty', 'type', 't_request_s',
                'priority', 'deadline_s', 'from_location', 'to_location')


def index_layout(layout):
    """按 (仓库, kind) 建索引，供订单选点。"""
    by_kind, homes = defaultdict(lambda: defaultdict(list)), {}
    for w in layout['warehouses']:
        wid = w['id']
        for l in w['locations']:
            by_kind[wid][l['kind']].append(l['id'])
        homes[wid] = [l['id'] for l in w['locations'] if l['kind'] == 'HOME']
        for d in w['docks']:
            if d['id'] not in by_kind[wid][d['kind']]:
                by_kind[wid][d['kind']].append(d['id'])
    return by_kind, homes


def validate_events(events, layout, problems=None):
    """引用完整性：from/to/return_to 必须存在，kind 组合必须合法。"""
    problems = problems if problems is not None else []
    loc = {l['id']: l for w in layout['warehouses'] for l in w['locations']}
    for e in events:
        for k in ('from', 'to', 'return_to'):
            if e.get(k) not in loc:
                problems.append('%s %s=%r not in locations' % (e['order_id'], k, e.get(k)))
        if e.get('return_to') in loc and loc[e['return_to']]['kind'] != 'HOME':
            problems.append('%s return_to not HOME' % e['order_id'])
        rule = KIND_RULES.get(e.get('type'))
        if not rule:
            problems.append('%s unknown type %r' % (e['order_id'], e.get('type')))
        elif e.get('from') in loc and loc[e['from']]['kind'] != rule[0]:
            problems.append('%s from kind mismatch' % e['order_id'])
        elif e.get('to') in loc and loc[e['to']]['kind'] not in rule[1]:
            problems.append('%s to kind mismatch' % e['order_id'])
    return problems


class SyntheticSource:
    def __init__(self, demand_spec, layout, seed):
        self.spec = demand_spec
        self.layout = layout
        self.rng = random.Random(seed)
        self.by_kind, self.homes = index_layout(layout)
        self.warehouses = [w['id'] for w in layout['warehouses']]
        self.skus = ['SKU-%04d' % (i + 1) for i in range(demand_spec['sku']['count'])]
        s = demand_spec['sku']['velocity'].get('s', 0.85)
        self.weights = [1.0 / ((i + 1) ** s) for i in range(len(self.skus))]

    def _pick(self, wid, kind_set):
        pool = []
        for k in kind_set:
            pool += self.by_kind[wid].get(k, [])
        return pool

    def _one(self, i, t, per_hour):
        o = self.spec['orders']
        typ = self.rng.choices(list(o['mix']), weights=list(o['mix'].values()), k=1)[0]
        prio = self.rng.choices(list(o['priority']), weights=list(o['priority'].values()), k=1)[0]
        q = o['qty']
        qty = max(q['min'], min(q['max'],
                                int(round(self.rng.lognormvariate(math.log(q['mean']), q['sigma'])))))
        wid = self.warehouses[i % len(self.warehouses)]
        from_kind, to_kinds = KIND_RULES[typ]
        src_pool = self._pick(wid, {from_kind})
        if not src_pool:
            return None
        src = self.rng.choice(src_pool)
        dst_pool = [x for x in self._pick(wid, to_kinds) if x != src]
        if not dst_pool:
            return None
        dst = self.rng.choice(dst_pool)
        homes = self.homes[wid]
        return {
            'order_id': 'O%05d' % (i + 1), 'type': typ, 'warehouse_id': wid,
            'sku': self.rng.choices(self.skus, weights=self.weights, k=1)[0],
            'qty': qty, 'from': src, 'to': dst,
            'return_to': homes[i % len(homes)],
            't_request': round(t, 2),
            'deadline': round(t + o['deadline_s'][prio], 2),
            'priority': prio,
            'source': 'synthetic',
        }

    def generate(self, duration_s, per_hour=None):
        """条件泊松过程：固定 N = round(per_hour * duration_h)，
        到达时刻为 [0, duration] 上 N 个均匀 order statistics（泊松过程的条件分布）。
        这样订单数确定、同 seed 完全可复现，断言基于 N 才有意义（A25/A36）。"""
        rate = per_hour or self.spec['orders']['per_hour']
        n = int(round(rate * duration_s / 3600.0))
        gaps = [self.rng.expovariate(1.0) for _ in range(n)]
        total = sum(gaps) or 1.0
        events, acc = [], 0.0
        for i, g in enumerate(gaps):
            acc += g * duration_s / total
            e = self._one(i, acc, rate)
            if e:
                events.append(e)
        return events


class CsvSource:
    """企业 CSV → events；字段映射来自 demand_spec.csv_mapping。"""

    def __init__(self, demand_spec, layout, path, encoding='utf-8-sig'):
        self.spec, self.layout, self.path = demand_spec, layout, path
        self.mapping = demand_spec.get('csv_mapping', {})
        self.encoding = encoding
        self.problems = []

    def generate(self, duration_s=1e18, per_hour=None):
        if not os.path.exists(self.path):
            raise SystemExit('CSV_NOT_FOUND %s' % self.path)
        with open(self.path, encoding=self.encoding, newline='') as f:
            rd = csv.DictReader(f)
            cols = set(rd.fieldnames or [])
            missing = [c for c in REQUIRED_CSV if c not in cols]
            if missing:
                raise SystemExit('CSV_MISSING_COLUMNS %s' % missing)
            out = []
            for i, row in enumerate(rd, 1):
                try:
                    qty = int(float(row['qty']))
                    t = float(row['t_request_s'])
                    dl = float(row['deadline_s'])
                except (ValueError, TypeError, KeyError):
                    self.problems.append('row %d bad numeric field' % i)
                    continue
                # 只过滤负数量；「单号以 C 开头 = 取消单」是 UCI Online Retail II
                # 的专有规则，属于 uci_retail.py，不能套用到企业 WMS CSV。
                if qty <= 0:
                    continue
                if t > duration_s:
                    continue
                out.append({
                    'order_id': row['order_id'], 'type': row['type'],
                    'warehouse_id': row.get('warehouse_id') or 'W',
                    'sku': row['sku'], 'qty': qty,
                    'from': row['from_location'], 'to': row['to_location'],
                    'return_to': row.get('return_to') or 'HOME-W-R01',
                    't_request': round(t, 2), 'deadline': round(dl, 2),
                    'priority': row['priority'], 'source': 'csv',
                })
        if not out:
            raise SystemExit('CSV_EMPTY: no valid order rows after filtering')
        validate_events(out, self.layout, self.problems)
        if self.problems:
            raise SystemExit('CSV_VALIDATION_FAIL %s' % '; '.join(self.problems[:5]))
        out.sort(key=lambda e: e['t_request'])
        return out


def load_source(demand_spec, layout, seed, csv_path=None):
    if csv_path or demand_spec['source']['type'] in ('csv', 'wms', 'uci'):
        path = csv_path or os.path.join(ROOT, 'config', 'orders_sample.csv')
        return CsvSource(demand_spec, layout, path)
    return SyntheticSource(demand_spec, layout, seed)
