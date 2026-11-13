# ADR-REFACTOR-10 · R8.1 资产目录：先钉死「一个键管两边」，再谈画模型

- 状态：已实施（R8.1 地基，默认关闭）
- 方案依据：§16.3 P1–P3、§16.4 数据模型、§16.11 防两张皮、§12.6 ASSET_CONSISTENCY_OK / GAP_MARKED_OK
- 相关：ADR-REFACTOR-06（rack_type 键）、ADR-REFACTOR-07（equipment_type 与逐车 model_id）、ADR-REFACTOR-09（密度重排）

## 背景：仿真的类型键已经齐了，3D 层还在用「一种货架」

R2 把存取机理写进 `rack_spec.types`（9 类），R3 把设备类型写进 `equipment_spec.types`（10 类），
`spec_layout` 已经把 `rack_type` 展开到每个货架组、`equipment_ref` 已经能按车型取参。但 3D 侧：

```python
def build_racks_and_cargo(...):
    for rk in racks:                      # 只用 levels/heat/size
        mesh = rack_mesh_for_heat(rk['heat'], rk['levels'])
```

`rk['model']` / `rk['rack_type']` **从未被读**——双深、驶入、贯通、穿梭、移动、流利、重力、压入
长得跟横梁式一模一样；`import_assets()` 的 `wanted` 元组写死 5 个母版，GLB 里实测 38 个，
**33 个闲置**（含堆垛机、龙门、带载叉车、料箱车、扫描门）。

这就是方案书说的 R15/R19 风险：外观与行为分叉（「看起来是驶入式、跑起来是横梁式」）。

## 决策一 · 目录用**双向相等**，不用子集

「asset_spec.mechanism_key ⊆ rack_spec 键」挡得住凭空造类型，挡不住**漏资产**——
少一个 `push_back` 也能过。所以门要求：

```
set(asset_spec.rack_assets)        == set(rack_spec.types)          # 9 == 9
set(asset_spec.equipment_assets)   == set(equipment_spec.types)     # 10 == 10
```

另加两条同源约束：

- `deferred_rack_types`（回转/VCM/VLM/阁楼/悬臂/搁板）与 `rack_spec.types` **必须不相交**——
  哪天有人把 VLM 塞进 rack_spec 却忘了资产条目，这里会红。
- 两个不同货架机理**不许共用一个程序化函数**。共用即「分型是假的」，比不做更糟：
  它会让人以为 8 类货架都能看了。

## 决策二 · footprint 是算出来的，不是填出来的

```
footprint_m = [bays × (clearW + structW),  depth_positions × (clearD + structD)]
```

`bays=3`（palette 模块 3 个货格宽），几何全部取自 `rack_spec.geometry`。**门会复算并比对，
容差 1e-6**；`depth_positions="from_rack"`（驶入/贯通/穿梭/重力/压入的整巷道深度）必须留
`null`——不知道就写不知道，不许拿托盘的 1.35 m 冒充。

这条门第一次跑就抓到了我自己写的错：`carton_flow` 的 footprint 我照抄了托盘的
`[2.91, 1.35]`，实际料箱货架是 `clearW 0.60 / clearD 0.50`，应为 **`[1.95, 0.54]`**。
方案书 §16.4 示例里的 `1.2 m` 深同理是示意值，真实值由 EUR 1200×800 推得的
`clearD 1.3 + structD 0.05 = 1.35 m` 决定——**以 geometry 为准，不以示例为准**。

## 决策三 · GLB 节点名现算，不抄

门每次运行时只读解析 `assets/cargotwin_assets.glb` 的 JSON chunk
（`struct.unpack('<II', d[12:20])` → `json.loads(d[20:20+jlen])`），把 `nodes[].name`
按 `__` 后缀去重取 `asset_` 根。本轮实测：

```
magic=glTF version=2 bytes=700396 nodes=164 meshes=126 asset_roots=38
```

11 个 `glb:` 源全部能对上（`asset_amr` / `asset_forklift` / `asset_stacker_crane` /
`asset_gantry_crane` / `asset_forklift_loaded` / `asset_amr_cart` / `asset_scanner_arch` /
`asset_rack` / `asset_dock_leveler` / `asset_pallet_boxes` / `asset_truck_box`）。
自检里故意把 `asset_stacker_crane` 改成 `asset_stacker_krane`，门必须抓住——**不臆造节点名是硬线**，
因为 GLB 里不存在的名字在 Blender 里只会静默返回 `None`，然后场景少一台设备而无人知晓。

## 决策四 · 给「没做完」建账本：`pending_assets` + `--strict`

R8 一次做不完。如果只是把 28 项资产写成 `status: "new"` 就收工，下一个人看到「门全绿」
会以为 R8 完了——这正是 R4 那个「0 巷道却全绿」的翻版。所以：

- 报告里显式给 `pending_assets`（当前 28 项：8 类货架 mesh、穿梭车/CTU/机械臂、
  辊道/重力/积放、6 类分拣、待接的 3 个闲置母版…）；
- `--strict` 下 pending 非空即退出码 1；
- **一条 pytest 专门断言「现在 `--strict` 必须还是红的」**（`test_strict_mode_still_red_while_pending`）。
  等 R8.2 落地再翻绿——翻绿这个动作本身要改测试，改不动就说明没做完。

`implemented_in` 也不是自由文本：门要求「文件存在 + 行号在范围内 + 该行 ±10 行内出现符号」。
声称 `status: "existing"` 却指向一个不存在的函数，会被 `IMPLEMENTATION_TRUTHFUL_OK` 拦下。

## 决策五 · 编号归位（这是账，不是代码）

当前 git 历史的 "R5"（`0821893`，密度重排）**不是**方案书 §11 的 R5。对照两边：

| 项 | 方案书位置 | 工程实况 |
|---|---|---|
| 密度重排（深度预算 → 行数、越界即拒绝） | §12.6 `REFLOW_BOUNDARY_NEG` + §16.12 `layout_reflow.py`，属 **R8** | 已实施（`0821893`），是 R8 面积重排的**求解器内核** |
| 方案书 R5 库存策略（EOQ / ROP_SS / newsvendor / GMROI） | §11 R5、§12.4 五门 | **未开工** |

所以密度重排的归位不是「R3 延伸」，更准确是 **R8 前置件**：它解决的正是 §16.8
「改 size → 货架 grid 重排 → 库容变」里的 grid 重排那一半；还差 docks / stations /
chargers / spawns / areas 的重排与越界拒绝（§16.8 缺口④）。

**方案书是作者的文档，执行方不改。** 本 ADR 只把工程侧的账记清楚：此后提交与门名按内容
标注（`check_asset_consistency.py` 归 R8.1），不再用「R5」指代密度重排，避免下一轮把
「R5 已完成」误读成「库存策略已完成」。

## 未做（R8.2 / R8.3 的清单，别当成遗漏）

- `build_dc` 的 rack/equipment dispatcher 与 8 类 `rack_<mechanism>` 程序化 mesh（R8.2）。
- 闲置母版接线（堆垛机/龙门/带载叉车/料箱车/扫描门）→ `IDLE_MASTER_WIRED_OK`（R8.2）。
- 输送/分拣分型（辊道/重力/积放；交叉带/滑块/摆轮/窄带/翻板/播种墙）（R8.2）。
- 3D 金标准双锚（counts + GLB 哈希）与受控重冻结 + `RACK_MESH_DISTINCT_NEG`（R8.3）。
- 厂房面积旋钮 + 交互重摆 + 受控提交闭环（D10a/D12、`AREA_PARAM_OK` / `ASSEMBLY_COMMIT_OK`）。
- R2 上架半（ADR-06 只兑现取货半）——与 R8 解耦：组装器只表达 footprint 与类型键，
  不表达动线，所以 R8 不被它阻塞。

## 后果

- 「两张皮」第一次有了**秒级可跑、能失败**的守门人：`python scripts/check_asset_consistency.py`
  不依赖 Blender，7 个反例注入全被抓住。
- 资产清单成为唯一真相：`build_dc`（R8.2）与 `editor.js`（R8.5）以后只能读它，
  不能再各自写死 5 个母版。
- 默认字节不变：`asset_spec.default_off = true`，`build_dc` 目前**根本不读**它，
  所以 STEP/3D 金标准本轮零风险——真正的风险（dispatcher 改产出）留到 R8.3 受控重冻结时面对。
