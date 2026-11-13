# ADR-REFACTOR-11 · R8.2 派发：开关是「布局有没有声明 rack_type」，不是又一个布尔

- 状态：已实施（R8.2，默认不改变产出）
- 方案依据：§16.5 派发、§16.11 防两张皮、§12.6 `ASSET_DISPATCH_OK` / `RACK_MESH_DISTINCT_NEG` / `IDLE_MASTER_WIRED_OK`
- 相关：ADR-REFACTOR-10（资产目录与双向相等）、ADR-REFACTOR-06（rack_type 键）

## 背景：目录钉住了，还得有人真的去读它

R8.1 让 `asset_spec` 与 `rack_spec` 双向相等，但那只是**账**。`build_dc` 里仍是：

```python
mesh = rack_mesh_for_heat(heat, levels)     # 只看 heat / levels
```

九类机理里，只有横梁式有几何。这一轮把派发接上，并且必须回答两个问题：
**默认构建会不会变？**（不能变）**怎么证明它没变？**（不能靠"我看着没变"）。

## 决策一 · 开关是数据形态，不是布尔

| 打开方式 | 谁决定的 | 后果 |
|---|---|---|
| 布局里某货架组声明 `rack_type` | `layout_spec` → `spec_layout` 展开 | 该组按机理建；同时深位/库容/行为一起变 |
| 全都不声明（默认） | 现状 | 走 `unit_rack_mesh`，**逐字节不变** |

不设 `options.asset_dispatch=true` 这种独立开关，是因为它允许"外观变了、行为没变"这种
最坏组合。声明机理就必须接受它连带改变库容与存取行为——这正是方案书 §16.11 要的
"同一个键驱动外观+行为"。

短路判断放在 `_dispatch_active()`：整份布局里**一个** `rack_type` 都没有时，连
`asset_spec` 都不读、`COUNTS` 一个键都不加。第一版忘了这点，`COUNTS['rack_dispatch_fallback']`
会被无条件写进 `build_report.json`，等于默认产出变了——门（`DEFAULT_OFF_OK`）要求
`build_report` 里不得出现 `rack_dispatch` 键，就是防这个。

## 决策二 · 查不到就炸，不许"画成横梁式"

```python
ra.resolve({}, spec)                         # → legacy
ra.resolve({"rack_type": "double_deep"})     # → procedural / rack_double_deep
ra.resolve({"rack_type": "vlm"})             # → DispatchError
ra.resolve({"rack_type": "double_deep"}, 被改成 selective 的目录)  # → DispatchError
```

第三种情况是真正的陷阱：如果只是"查不到就用默认外观"，双深货架会安静地长成横梁式，
而 `rack_type` 还写着 `double_deep`。第四种情况更隐蔽——`source` 与机理对不上时，
外观会撒谎而日志一片祥和。两种都抛错。

## 决策三 · 几何是「盒子数据」，所以门不需要 Blender

`scripts/rack_assets.py` 只产盒子列表（`(lo, hi, slot)`，[0,1]^3 归一化，与
`unit_rack_mesh` 同口径），`build_dc` 负责转 mesh。于是"九类是不是真的不一样"变成一个
**可算的指纹问题**，0.2 秒出结果：

| mechanism | 盒子数 | 指纹 |
|---|---|---|
| selective_pallet | 30 | `04c95d738c92` |
| double_deep | 29 | `a3fba2cbc91e` |
| drive_in | 16 | `d8d86a172443` |
| drive_through | 15 | `840a6fb107bb` |
| pallet_shuttle | 21 | `e535116866f1` |
| mobile | 17 | `15bb1253231f` |
| carton_flow | 46 | `e2f088ff75c0` |
| pallet_gravity | 50 | `b306f6b1b755` |
| push_back | 30 | `d65427bd0065` |

注意 `push_back` 与 `selective_pallet` **盒子数都是 30**——只有坐标不同。这个巧合正好
证明"按数量比"是不够的，指纹必须落到尺寸集合上。

## 决策四 · 渲染层再验一次，并且防「拿旧探针当证据」

`scripts/rack_mesh_probe.py` 在 Blender 里调**同一个** `rack_type_mesh()` 真建 9 个 mesh：

```
selective 240v/180f  double_deep 232/174  drive_in 128/96  drive_through 120/90
shuttle 168/126      mobile 136/102       carton_flow 368/276  gravity 400/300  push_back 240/180
```

顶点数只有 8 种（push_back 和 selective 同为 240）——所以探针记录的是**顶点坐标排序哈希**
`geom_sha`，9 类互不相同（`distinct_geoms=9`）。

探针里写入 `source_sha`（`rack_assets.py` 的 sha256）。`--probe` 会对比当前源码哈希：
**几何改了但没重跑探针 → 判过期**。这是"证据会腐烂"这条老问题的低成本解法。

## 决策五 · 「接进构建」和「摆进场景」分开说

`equipment_dispatch` 打开时，`import_assets()` 会把 `equipment_assets` / `scene_assets`
里所有 `glb:` 母版一并 extract（堆垛机、龙门、带载叉车、料箱车、扫描门…），它们会出现在
`build_report.asset_meshes` 里。**但那不等于它们被摆到了正确位置**——AS/RS 巷道、龙门
跨位、扫描门骑在输送线上，都属于装配（R8.3）。本轮如实只声称前者，避免又出现
"报告里有这个母版 = 场景里有这个设备"的错觉。

## 未做（R8.3 起）

- 带 `rack_type` 的场景真跑一遍并**受控重冻结** 3D 金标准（counts + GLB 哈希双锚，
  先跑负对照证明门能抓到"母版被换/数量不符"）。这需要 D11 签核，本轮刻意不动。
- 母版摆放、`IDLE_MASTER_WIRED_OK` 的场景级断言。
- 输送/分拣分型（辊带/重力/积放、交叉带/滑块/摆轮/窄带/翻板/播种墙）。
- 穿梭车 / CTU / 机械臂程序化车体（`procedural:shuttle_car` 等仍是 pending）。
- 面积旋钮 + 交互重摆 + 受控提交（D10a / D12）。

## 后果

- 派发链 5 门 + 1 个 Blender 探针，7 个反例注入全被抓住（几何塌陷、负对照空转、
  source 与机理不一致、未知机型、默认布局混入 rack_type、设备开关被打开）。
- 默认构建（`build_dc`）产出的 `build_report.json` 与 `dc_preview.glb` 哈希
  **逐字节不变**：`f86ae7a2…` / `9ecfe23c…`（R8.2 改动前后各跑一次 Blender 对比）。
- 未落地资产从 28 项降到 20 项；`--strict` 仍然红，且 pytest 里有一条断言它必须红。
