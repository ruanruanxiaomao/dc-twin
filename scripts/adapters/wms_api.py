"""wms_api.py — WMS 接口预留（DESIGN_PLAN §9 Step 6 / §1.2 非目标）。

只定义接口与 mock，**不连接任何真实系统**，不声称兼容 VDA 5050。
真实接入时实现 fetch_orders() / push_task_status() 并写清认证与 schema 版本。
"""
import abc
import json
import os
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


class WmsClient(abc.ABC):
    @abc.abstractmethod
    def fetch_orders(self, start, end):
        """返回与 demand_source 事件 schema 兼容的订单列表。"""

    @abc.abstractmethod
    def push_task_status(self, task_id, status, at):
        """回写任务状态；真实系统需处理幂等与重试。"""


class MockWmsClient(WmsClient):
    """本地 mock：读取 config/orders_sample.csv 转成事件，不产生网络请求。"""

    def __init__(self, sample_path=None):
        self.sample_path = sample_path or os.path.join(ROOT, 'config', 'orders_sample.csv')
        self.status_log = []

    def fetch_orders(self, start, end):
        import csv
        with open(self.sample_path, encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        out = []
        for r in rows:
            t = float(r['t_request_s'])
            if not (start <= t <= end):
                continue
            out.append({
                'order_id': r['order_id'], 'sku': r['sku'], 'qty': int(r['qty']),
                'type': r['type'], 'warehouse_id': r['warehouse_id'],
                'from': r['from_location'], 'to': r['to_location'],
                'return_to': r['return_to'], 't_request': t,
                'deadline': float(r['deadline_s']), 'priority': r['priority'],
                'source': 'wms_mock',
            })
        return out

    def push_task_status(self, task_id, status, at):
        self.status_log.append({'task_id': task_id, 'status': status,
                                'at': at or datetime.now().isoformat(timespec='seconds')})
        return True


if __name__ == '__main__':
    c = MockWmsClient()
    orders = c.fetch_orders(0, 1e18)
    print('WMS_MOCK_OK orders', len(orders), 'note: interface stub only, no real WMS')
