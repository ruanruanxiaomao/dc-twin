"""uci_retail.py — UCI Online Retail II 订单映射（DESIGN_PLAN §8.6.1 / Step 6）。

**只提供映射逻辑，不打包数据集**。真实数据请自行下载（CC BY 4.0）：
  https://archive.ics.uci.edu/dataset/502/online+retail+ii
并把 xlsx 转成 CSV 后传 --input。

映射规则：
  InvoiceNo -> order_id（以 C 开头的取消单丢弃）
  StockCode -> sku
  Quantity  -> qty（<=0 丢弃）
  InvoiceDate -> t_request_s（相对首单的秒数）
  其余 type/priority/deadline/from/to 由布局与 demand_spec 补齐（明确标 synthetic fill）
编码：UCI 原始文件常见 latin-1，读不到时自动回退。
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def map_rows(rows, layout, demand, limit=None):
    loc = [l for w in layout['warehouses'] for l in w['locations']]
    by = {}
    for l in loc:
        by.setdefault(l['kind'], []).append(l['id'])
    out, dropped = [], 0
    stamps = []
    for r in rows:
        inv = (r.get('InvoiceNo') or '').strip()
        if inv.upper().startswith('C'):
            dropped += 1
            continue
        try:
            qty = int(float(r.get('Quantity', 0)))
        except (ValueError, TypeError):
            dropped += 1
            continue
        if qty <= 0:
            dropped += 1
            continue
        try:
            dt = datetime.fromisoformat((r.get('InvoiceDate') or '').replace('/', '-'))
            stamps.append(dt.timestamp())
        except ValueError:
            stamps.append(0.0)
        out.append((inv, r.get('StockCode') or 'UNKNOWN', qty))
        if limit and len(out) >= limit:
            break
    t0 = min(stamps) if stamps else 0.0
    events = []
    for i, (inv, sku, qty) in enumerate(out):
        src = by['SHELF'][i % len(by['SHELF'])]
        dst = by['OUTBOUND'][i % len(by['OUTBOUND'])]
        t = max(0.0, stamps[i] - t0)
        events.append({
            'order_id': inv, 'sku': sku, 'qty': qty, 'type': 'OUTBOUND',
            'warehouse_id': 'W', 'from': src, 'to': dst,
            'return_to': by['HOME'][i % len(by['HOME'])],
            't_request': round(t, 2), 'deadline': round(t + 3600, 2),
            'priority': 'P2', 'source': 'uci_retail',
            'filled_fields': ['type', 'priority', 'deadline', 'from', 'to', 'return_to'],
        })
    return events, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', default=None, help='UCI Online Retail II CSV')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'uci_events.jsonl'))
    ap.add_argument('--limit', type=int, default=1000)
    args = ap.parse_args()
    if not args.input or not os.path.exists(args.input):
        print('UCI_ADAPTER: no dataset provided. Download Online Retail II (CC BY 4.0),')
        print('  convert to CSV, then: python scripts/adapters/uci_retail.py --input <file>')
        print('  Mapping logic is available as map_rows(rows, layout, demand, limit).')
        sys.exit(0)
    layout = json.load(open(os.path.join(ROOT, 'data', 'layout.json'), encoding='utf-8'))
    demand = json.load(open(os.path.join(ROOT, 'config', 'demand_spec.json'), encoding='utf-8'))
    for enc in ('utf-8-sig', 'latin-1'):
        try:
            with open(args.input, encoding=enc, newline='') as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    events, dropped = map_rows(rows, layout, demand, args.limit)
    with open(args.out, 'w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    print('UCI_ADAPTER_OK rows', len(events), 'dropped', dropped, 'encoding', enc)


if __name__ == '__main__':
    main()
