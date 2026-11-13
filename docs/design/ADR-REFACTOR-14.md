# ADR-REFACTOR-14 · 输送/分拣/车体分型到位，面积参数化只兑现了一半

- 状态：已实施（R8.5 分型 + R8.6 面积参数化），其中"面积→库容"明确未完成
- 方案依据：§16.7 输送分拣分型、§16.8 面积自定义、§12.6 `CONVEYOR_SORTER_VARIANT_OK` / `AREA_PARAM_OK` / `REFLOW_BOUNDARY_NEG`
- 相关：ADR-REFACTOR-09（密度重排）、ADR-REFACTOR-13（GLB 规整）

## 一、分型：4 种输送台面 + 7 种分拣末端 + 3 个无母版车体

`zone_detail.conveyor(..., style=)` 与新增的 `zone_detail.sorter(..., kind=)` 都**默认
`belt` / `chute`**，即默认场景的代码路径与 R8.4 完全一致（这一条被现实教育过，见第三节）。

| 组 | 分型 | 结构差异 |
|---|---|---|
| 输送台面 | belt / roller / gravity / accumulation | 皮带→密排辊子；重力式带 2.6° 坡度；积放式加挡停块 |
| 分拣末端 | chute / crossbelt / slider / swing_wheel / narrow_belt / tilt_tray / put_wall | 斜滑道→环线小车/侧推板/三轮/窄带/斜托盘/格口阵列 |
| 车体（无 GLB 母版） | shuttle_car / ctu_robot / robot_arm | 矮车+升降销 / 立柱+出箱叉 / 底座+转台+两节臂 |

`scripts/variant_probe.py` 在 Blender 里各建一份，量**世界坐标摘要**。

### 这里踩了两个坑，都值得记下来

1. **只比数量会被骗**：第一版探针里 `gravity` 与 `roller` 都是 61 对象 / 488 顶点
   （坡度只改 Z），`slider`/`swing_wheel`/`tilt_tray`/`put_wall`/`chute` 全是
   6 对象 / 48 顶点，`shuttle_car` 与 `ctu_robot` 都是 9 个盒子。**数量相同不等于
   长得一样**——和货架探针用 `geom_sha` 是同一个教训，这里同样改成坐标摘要。
2. **后台模式不更新依赖图**：`bpy` 新建对象的 `matrix_world`/`dimensions` 还是旧的，
   第一版量出来全是"单位立方体"，四类分型被判成同一份几何。加
   `bpy.context.view_layer.update()` 后才拿到真实坐标——**探针自己也会撒谎**。

修完后 `distinct_conveyor=4 / distinct_sorter=7 / distinct_body=3`，且探针带
`source_sha`（三个源文件），源码改了没重跑即判过期。

## 二、面积：参数真的传导了，但只到"壳体 + 导航格"

`check_area_param.py` 三条子门：

| 门 | 结论 |
|---|---|
| `AREA_PARAM_OK` | 厂房 100×70 → 120×84，货架格 200×140 → **240×168**（cell_size 不变），货架/货位**不丢** |
| `AREA_FILL_OK` | 把进深预算设为"填满厂房"（`depth_budget: "from_size"`，R8.6 新增）→ **被 L3/L4/L6 拒 1022 处** |
| `AREA_SHRINK_NEG` | 缩到 1/3 → L2/L3/L6 报 160 处、**退出码 1**，拒绝生成 |

**为什么"放大不会多库位"**：货架行数是 spec 里写死的整数（`rows: 4`），放大厂房只
放大壳子与网格。想让面积进库容，必须同时给进深预算——这就是新加的
`depth_budget: "from_size"`：预算 = 厂房进深 − 组原点 − 边距 − 1 m 施工余量。

**而"填满"当场撞上月台与输送线**（1022 处 L3/L4/L6）。这不是 bug，是真实结论：
功能区/月台坐标是绝对坐标、不随面积重排（§16.8 缺口④），所以"自动填满"不是可行解。
要真的用上多出来的面积，必须先把 docks/areas/chargers 一起重排——D10b 求解器，未做。

所以门不断言"面积必须让货位变多"，而是分别验：**能变的必须变**（壳体/网格）、
**不能悄悄无效**（填满要么被拒、要么真变多，二者皆可，"通过但没变"判红）、
**越界必须拒绝**。

## 三、又抓到一个自己造的回归

`sorting_line` 原来直接调 `chute(..., drop=0.5)`，重构后走 `sorter(kind='chute')`，
而 `sorter` 的默认 `drop=0.55` —— 默认场景的滑道坡度被悄悄改了 0.05 m，
`dc_preview.glb` 从 `a5e90872…` 变成 `8072f476…`（多 1 个 accessor、436 字节）。
发现方式是重跑默认构建对比哈希，不是靠看代码。修法：`drop` 由调用方显式传入，
`sorting_line` 传 0.5。回归修复后哈希回到 `a5e90872…`（929260 字节）。

**教训**：把"默认值"从调用点搬进被调函数，就是在偷偷改默认行为。凡搬迁参数，
必须让调用点显式传值，并用字节哈希兜住。

## 四、`implemented_in` 改成函数名，终结行号腐烂

这一轮又漂了两次行号（`build_dc.py:560` 指到了别的函数），把两条真实声明判成撒谎。
现在支持 `scripts/build_dc.py#build_vehicles` 这种写法：门按 AST 找到函数并检查函数体
里是否出现符号。行号形式保留兼容，但新条目一律写函数名——**函数名不会因为加一行注释
就失效**。

## 未做

- **面积 → 库容的真实传导**（D10b：docks/areas/chargers 随面积重排）。
- **交互重摆 UI**（D10a：editor.js 拖拽 + 受控提交，`ASSEMBLY_COMMIT_OK`）。
- 输送/分拣分型**尚未在默认场景启用**：数据没有 `style`/`sorter` 字段，画面与 R8.4 相同；
  何时启用属于关卡决策（会在 `3D_GOLDEN_OK` 里体现为一次受控重冻）。
- 穿梭车/CTU 车体已建好但未摆进场景（无对应设备部署，属 `level: gap`）。

## 后果

- `dc run --scope refactor` 从 8 步变 **9 步**（新增面积门），26.8 s 全绿。
- 输送/分拣/车体第一次有了"能不能分辨"的自动判据，不再靠看图。
- 面积自定义有了可证伪的门，并且把"只改壳子"这件事写进了结论而不是藏起来。
