"""csv_orders.py — 企业订单 CSV 适配器（DESIGN_PLAN §9 Step 6）。

把 WMS/ERP 导出的 CSV 映射成项目 events schema，并做引用完整性校验。
缺列、非法数值、负数量/取消单、from/to 不存在 → 明确 FAIL（A34）。

运行：
  python scripts/adapters/csv_orders.py --csv config/orders_sample.csv \
      --out out/csv_events.jsonl
"""
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from demand_source import CsvSource, validate_events, REQUIRED_CSV  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', default=os.path.join(ROOT, 'config', 'orders_sample.csv'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'csv_events.jsonl'))
    ap.add_argument('--duration', type=float, default=1e18)
    args = ap.parse_args()

    layout = json.load(open(os.path.join(ROOT, 'data', 'layout.json'), encoding='utf-8'))
    demand = json.load(open(os.path.join(ROOT, 'config', 'demand_spec.json'), encoding='utf-8'))
    src = CsvSource(demand, layout, args.csv)
    events = src.generate(args.duration)
    problems = validate_events(events, layout)
    with open(args.out, 'w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    report = {
        'csv': os.path.relpath(args.csv, ROOT).replace('\\', '/'),
        'rows_out': len(events), 'required_columns': list(REQUIRED_CSV),
        'referential_integrity_errors': problems,
        'schema_valid': not problems,
        'verdict': 'PASS' if not problems else 'FAIL',
        'note': 'mapping comes from demand_spec.json -> csv_mapping',
    }
    os.makedirs(os.path.join(ROOT, 'out'), exist_ok=True)
    with open(os.path.join(ROOT, 'out', 'csv_adapter_report.json'), 'w',
              encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('CSV_ADAPTER', report['verdict'], 'rows', len(events),
          'errors', problems[:3])
    sys.exit(0 if not problems else 1)


if __name__ == '__main__':
    main()
