# 真实数据源与许可

> DESIGN_PLAN §8 / §17.2。**本项目当前所有订单都是合成的**（`demand_spec.source.type=synthetic`），
> 本文档说明如何替换成企业数据，以及引用规则。
> 引用格式：**来源 + 版本 + 访问日期**；不要写"网上说"。

## 1. 已实现的适配器

| 文件 | 状态 | 用途 |
|---|---|---|
| `scripts/demand_source.py → CsvSource` | ✅ 可用 | 企业 WMS/ERP 导出的 CSV |
| `scripts/adapters/csv_orders.py` | ✅ 可用 + 引用完整性校验 | CLI 入口 |
| `scripts/adapters/uci_retail.py` | ✅ 映射逻辑（**不含数据集**） | UCI Online Retail II |
| `scripts/adapters/wms_api.py` | ✅ 接口 + Mock，不联网 | WMS 接口预留 |

### CSV 必填列（`demand_spec.json → csv_mapping`）

```
order_id, sku, qty, type, t_request_s, priority, deadline_s,
from_location, to_location        （可选：return_to, warehouse_id）
```

- `qty > 0`；`type ∈ {INBOUND, OUTBOUND, PICK, REPLENISH, TRANSFER}`；
- `from/to/return_to` 必须存在于 `data/layout.json` 的 `locations[]`；
- 缺列 → `CSV_MISSING_COLUMNS`；引用错误 → `CSV_VALIDATION_FAIL`；
- 过滤后为空 → `CSV_EMPTY`（空结果不允许判 PASS）。

**注意**：「单号以 C 开头 = 取消单」是 UCI 数据集的专有规则，
只在 `uci_retail.py` 里做；企业 CSV 不过滤 C 前缀（曾误伤 `CSV-0001`，已修）。

```powershell
python scripts/adapters/csv_orders.py --csv config/orders_sample.csv --out out/csv_events.jsonl
```

`config/orders_sample.csv` 是 24 行的**合成样例**（同仓、引用真实库位），用于验证适配器。

## 2. 公开数据集

| 数据集 | 地址 | 许可 | 用途 | 注意 |
|---|---|---|---|---|
| UCI Online Retail II | https://archive.ics.uci.edu/dataset/502/online+retail+ii | **CC BY 4.0** | 订单/SKU/数量分布校准 | 过滤 `Quantity>0` 且 `InvoiceNo` 不以 C 开头；`CustomerID` 可空；原始文件常为 latin-1 |
| MovingAI MAPF benchmarks | https://movingai.com/benchmarks/mapf/ | 免费（学术） | 50+ 车规模测试 | 不是真实仓库尺寸，不能当布局 |
| asprilo | https://github.com/potassco/asprilo | 需核查 | 仓库实例生成/校验 | 不依赖 ASP 求解器 |

接入 UCI：下载 xlsx → 转 CSV → `python scripts/adapters/uci_retail.py --input <file>`。
映射：`InvoiceNo→order_id`、`StockCode→sku`、`Quantity→qty`、
`InvoiceDate→t_request_s`；`type/priority/deadline/from/to` 由布局补齐，
并在事件里用 `filled_fields` 明确标注是合成补齐。

## 3. 设备与标准参考值

| 来源 | 地址 | 版本/日期 | 用途 |
|---|---|---|---|
| MiR250 规格 | https://mobile-industrial-robots.com/products/robots/mir250/specifications | 厂商手册，访问日期需自填 | AGV 速度/载重/尺寸 |
| VDA 5050 | https://github.com/VDA5050/VDA5050 | 规范 2.1/3.0 | order/state/factsheet 消息结构（本项目**只留接口，不声称兼容**） |
| ISO 3691-4 | https://www.iso.org/standard/70660.html | 付费标准 | 无人工业车辆安全；**不要复制全文** |
| EUR 托盘 | EN 13698-1 / EPAL | 标准 | 1200×800×144 mm |
| OSHA 窄通道 | https://www.osha.gov/etools/powered-industrial-trucks/workplace/narrow-aisles | 政府资料 | 通道安全参考 |
| WERC DC Measures | https://werc.org/page/DCMeasuresOperationalMetrics | 报告可能需会员 | 仓库 KPI 定义对照 |

## 4. 开源项目对照（不盲目复制）

| 项目 | 许可 | 可借鉴 |
|---|---|---|
| openTCS | MIT | 车队控制 / 派车 / VDA5050 适配 |
| WareTwin（本地已 clone） | MIT | tick 引擎、电量/充电/故障、cell `reserved_by` |
| Flatland | MIT | 多智能体场景/基准格式 |
| RWARE | MIT | 仓库布局生成 |
| OR-Tools | Apache-2.0 | 任务分配 / 路径优化 |
| SimPy | MIT | 离散事件仿真 |
| Odoo / ERPNext | LGPL/AGPL、GPL | 订单/库存数据模型（**不要闭源分发 AGPL 部分**） |

规则：复制代码前看 LICENSE；MIT/Apache 保留版权；GPL/AGPL 谨慎；
复制资产前算 SHA256 并写 `THIRD_PARTY_NOTICES.md`。

## 5. 本项目自身资产

| 资产 | 来源 | 许可 | 登记 |
|---|---|---|---|
| `assets/cargotwin_assets.glb` | cargoTwin `wangzh12023/cargotwin` | MIT | `THIRD_PARTY_NOTICES.md` 含 SHA256 |
| `DESIGN_PLAN.md` | 工作区内设计文档 | 本项目内部 | 本报告 §0 引用 |
| `config/orders_sample.csv` | 本项目生成 | 本项目内部 | 合成样例，非真实数据 |
| Pillow / imageio-ffmpeg | PyPI | HPND / BSD-2-Clause | `requirements.txt` 锁版本 |

## 6. 校准检查清单

- [ ] 订单数/小时与 WMS 近 3 个月同班次统计误差 ≤20%
- [ ] SKU 频次分布替换 Zipf
- [ ] 数量分布替换 lognormal
- [ ] AGV speed/accel/decel 换成车型手册
- [ ] `meta.todo` 里的 placeholder 全部替换为 measured/vendor
- [ ] 输出 `calibration_report.md`，写清哪些仍是假设
