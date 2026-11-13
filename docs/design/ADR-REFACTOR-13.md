# ADR-REFACTOR-13 · 把 GLB 规整到字节可复现，并让闲置母版站到地上

- 状态：已实施（R8.4）
- 方案依据：§10 金标准重冻纪律、§12.6 `3D_GOLDEN_OK` / `IDLE_MASTER_WIRED_OK`
- 相关：ADR-REFACTOR-11（派发）、ADR-REFACTOR-12（查明不可复现）

## 背景：ADR-12 说"字节不可复现"，这一轮把它修掉

ADR-12 实测到两类随机性：索引缓冲顺序（默认场景 1864 字节）、accessor 是否复用
（带机理场景 889 vs 888，差 852 字节）。两者都不改几何，但都让 sha256 变。

结论不是"那就别用哈希"，而是"在导出后把表示收紧成唯一形式"。于是新增
`scripts/glb_canonical.py`（纯 Python，不依赖 Blender），`export_glb.py` 导出后原地
规整一次。

## 决策一 · 三步规整，只改表示

1. **丢掉未被引用的 accessor**（node/mesh/skin/animation 都不指向的）；
2. **一个 accessor 一个紧密 bufferView**，不依赖导出器怎么合并视图；
3. **索引规范化 + 相同数据合并**：每个三角形旋转到以最小顶点开头（绕序不变，法线
   朝向不变），三角形按字典序排序；内容完全相同的 accessor 合并成一份。

第 3 步是关键：`889 vs 888` 的本质是"导出器有时复用同一份 NORMAL、有时不复用"。
合并相同数据后，这条随机性被按死。

**安全性**：`geometry_signature()`（所有 POSITION 坐标排序摘要）在规整前后必须相同，
`export_glb.py` 里是硬断言——不同就 `SystemExit`，绝不写出。规整也做成幂等，
`test_glb_canonical.py` 有专门用例。

## 决策二 · 实测结果

| 场景 | 规整前 | 规整后 | 两次运行 sha256 |
|---|---|---|---|
| 默认布局 | 1211380 字节 | **929260 字节** | 相同 `a5e90872…` |
| 带机理 + 母版全开 | 1185888 / 1185036（两态） | 同一份 | 相同 `ccd0cc10…`（带母版） |

体积缩了 23% 不是压缩，是**去重**：原来的文件里同一份法线/索引被存了多遍。

于是 `3D_GOLDEN_OK` 终于可以用方案书 D11 说的"GLB 哈希"——`check_asset_dispatch.py`
的锚点现在是 counts + layout_sha + **glb_sha256** + 结构指纹（保留结构指纹作第二道，
防止"哈希对但结构被人换了"这种荒唐情形）。

## 决策三 · 母版必须站到地上，摆不了的要记账

R8.2 的 `equipment_dispatch` 只做到"母版被 extract、出现在 `asset_meshes` 里"。
那仍然是一种"报告里有、现场没有"。这一轮给了具体落脚点（`build_master_props`）：

| 母版 | 位置 | 理由 |
|---|---|---|
| `asset_scanner_arch` | 第一道入库门内侧 | 标识识别发生在门口 |
| `asset_forklift_loaded` | 现有叉车旁 3 m | 作业态叉车 |
| `asset_amr_cart` | 第一个站台取货点旁 | 料箱车在站台待命 |
| `asset_stacker_crane` | W 前两组货架巷道 | 有货架才有堆垛机 |
| `asset_gantry_crane` | **不摆** | 这套布局没有集装箱堆场，摆哪儿都是布景 |

门 `IDLE_MASTER_WIRED_OK` 断言前四类**各有 ≥1 个实例**，同时断言龙门吊**出现在
`master_props_unplaced` 里**——"没摆"必须是显式记录，不能靠人记得。自检注入三种反例：
漏摆一类、龙门吊消失、龙门吊被偷偷摆进场景。

## 未做

- **输送/分拣分型**（§16.7）：辊道/重力/积放、交叉带/滑块/摆轮/窄带/翻板/播种墙。
  `zone_detail.conveyor()` 是沿折线画的情形，加分型要动路径分段逻辑，会波及默认场景的
  输送线，留到 R8.5 单独做（届时默认样式仍是 belt，逐字节不变）。
- `procedural:shuttle_car` / `ctu_robot` / `robot_arm` 仍未实现（pending 账本里有）。
- 面积旋钮 + 交互重摆（D10a / D12）。

## 后果

- 3D 金标准第一次真的可锚：同一场景两次导出**逐字节相同**，`--typed-build` 连跑两次 PASS。
- 默认产物仍然零行为改动：`build_report.json` 仍是 `f86ae7a2…`（`master_props` 只在
  `equipment_dispatch` 打开时写 COUNTS），`dc_preview.glb` 因为规整换了字节
  （体积下降、几何签名不变），`docs/app` 与 webapp manifest 已同步重建并通过
  `check_webapp` 的 W1–W4。
- 母版从"接进构建"变成"站进场景"，并留下一条诚实的未摆放记录。
