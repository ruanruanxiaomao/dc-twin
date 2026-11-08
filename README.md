# 配送中心数字孪生（区域仓 W + 前置仓 F1/F2/F3）

Blender 5.2.3 LTS 的配送中心场景与任务驱动 AGV 仿真。**主交付是 `.blend` 工程 + 可复现的数据/脚本链**，图/视频/GLB 为辅。

> 本版本已完成 `AUDIT_REPORT.md` 两轮审计的 P0 整改与主要 P1 修复，并把「一次性单程搬运」升级为**闭环作业**：
> HOME 待命 → 取货 → 交付 → 回 HOME，全程在场、严格口径零冲突、货物随车移动。
> 验收 A1–A21 全部独立实测，且验证器能失败（见 `tests/`）。
> 当前数字一律以 `out/CURRENT_STATUS.md` 为准（由脚本从 JSON 生成）。

## 1. 环境

| 项 | 要求 |
|---|---|
| Blender | 5.2.3 LTS（本机 `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`） |
| Python | 3.13（实测），3.11+ 应可用 |
| 依赖 | `pip install -r requirements.txt`（Pillow、imageio-ffmpeg） |

`blender` 不在 PATH 时设置 `$env:BLENDER_EXE = '...\blender.exe'`，脚本会读取。

## 2. 一键运行

```powershell
cd C:\Users\OYC\Desktop\blender\dc_project
.\run_all.ps1
```

任一步退出码非 0 即 `throw`。脚本顺序**不可颠倒**：

```
gen_data → layout_audit → nav_grid → optimize_slotting → gen_tasks
→ plan_paths → gen_cargo → traffic_check → check_geometry → plot_nav_plan
→ build_dc → animate → inspect_blend → export_glb → render → annotate_heatmap
→ encode_video → parse_artifacts → test_validator_can_fail → validate → report_status
```

## 3. 分步运行（调试）

```powershell
python scripts/gen_data.py
python scripts/layout_audit.py        # 布局实体冲突，必须退出 0
python scripts/nav_grid.py            # 0.5 m 导航栅格
python scripts/optimize_slotting.py
python scripts/gen_tasks.py           # 事件 → 任务 → AGV
python scripts/plan_paths.py          # 闭环 3 段 A* + 时间线 + 严格口径错峰
python scripts/gen_cargo.py           # 订单货物状态机（PICK/DROP、库存增减）
python scripts/traffic_check.py       # 全程在场：两车间距/速度/朝向，必须退出 0
python scripts/check_geometry.py      # 线段 vs 实体，必须退出 0
python scripts/plot_nav_plan.py

& $Blender -b --factory-startup -P scripts/build_dc.py
& $Blender -b --factory-startup -P scripts/animate.py
& $Blender -b --factory-startup -P scripts/inspect_blend.py
& $Blender -b --factory-startup -P scripts/export_glb.py
& $Blender -b --factory-startup -P scripts/render.py

python scripts/annotate_heatmap.py    # 给 05 图叠 5 档图例
python scripts/encode_video.py
python scripts/parse_artifacts.py     # 必须在 encode_video 之后
python scripts/validate.py            # A1–A21 硬门槛，任一 FAIL 退出 1
python scripts/report_status.py       # 生成 out/CURRENT_STATUS.md
python tests/test_validator_can_fail.py   # 7 个负测试
```

## 4. 目录

```
data/     config.json · layout.json · skus.json · events.jsonl · tasks.json
          slotting.json · nav_grid.json · paths.jsonl · routes.json
          agent_schedules.json · path_metrics.json · cargo_state.json
scripts/  20 个脚本（数据 / 校验 / 场景 / 渲染 / 解析）
assets/   cargotwin_assets.glb（MIT，见 THIRD_PARTY_NOTICES.md）
out/      distribution_center.blend · dc_preview.glb · anim_dc.mp4
          renders/01–10 · *_report.json · CURRENT_STATUS.md
tests/    test_validator_can_fail.py
```

## 5. 预期产物

| 产物 | 预期 |
|---|---|
| `out/layout_audit.json` | `issues: []`、`verdict: PASS` |
| `out/traffic_report.json` | `verdict: PASS`、`min_pair_distance_m >= 1.4592`、0 issues、`strict_violations: 0` |
| `out/geometry_report.json` | `verdict: PASS`、`agv_radius_m≈0.73`、0 穿越 |
| `out/build_report.json` | 与 `blend_audit.json` 的 objects/meshes/materials 一致 |
| `out/glb_report.json` | 无 `NAV_CORRIDORS` / `AGV_ROUTE_LINES`，有 `DC_ROOT` |
| `out/renders/01–06` | 1920×1080 PNG |
| `out/renders/07–08` | 1032×1104 / 1464×858；三色闭环段 + HOME 待命位 + 取货/交付圈 + 方向箭头 |
| `out/anim_dc.mp4` | 帧数 = `frame_end - frame_start + 1`；时长 = 帧数/fps；AGV 全程在场 |
| `out/route_report.json` | `closed_loops` / 三段里程 / head-on / edge clearance / 中央主通道流量 |
| `data/cargo_state.json` | 21 件货物 `IN_STOCK→LOADED→DELIVERED`，42 次状态迁移、库位库存增减 |
| `out/renders/09_time_space.png` | 21 台车 × 时间轴，MOVE/WAIT/PARK/ROTATE/FACE/PICK/DROP 分色 |
| `out/renders/10_snapshot_sequence.png` | 5 个时刻快照，21 台车全程在场 |
| `out/CURRENT_STATUS.md` | 由 JSON 自动生成的当前状态表（单一真相源） |
| `out/validate_report.json` | A1–A21 全 PASS，进程退出码 0 |

## 6. 关键设计

- **坐标**：`layout` 平面 `[x,z]` → Blender `(X,Y,Z)=(x,z,h)`，Z-up 米制；GLB `export_yup=True`。
- **唯一真相源**：`config.json` 管参数，`layout.json` 只由 `gen_data.py` 写；`nav_grid.json` 由 `nav_grid.py` 生成，规划器与校验器共用 `geometry.py` 的 `AGV_RADIUS = 0.7296 m`（AMR 实测 1.12×0.77 m 外接圆 + 0.05 m 余量）。
- **栅格**：0.5 m，所有实体（含月台、柱子、园区车辆）按 AGV 半径膨胀后保守格化。
- **闭环作业**：一张订单 = 一台车一个完整循环 `HOME → PICK → DROP → HOME`，三段 A* 折点分别记为去程空驶 / 载货 / 回程空驶。取货点在该车专属排间通道深端，交付点按「闭环总里程最短」选真实月台 / 打包台 / 对面半区货架；TRANSFER 单强制跨中央主通道。
- **HOME 待命位**：每台车有显式 `HOME` 库位（对齐 0.5 m 栅格中心），贴在东西墙 / F 仓东侧待命带，彼此 ≥4 m；A* 对其它车的 HOME 加软代价，避免「停着的车被经过的车擦到」。
- **路径**：任务驱动 8 向 A*（禁止切角、octile 启发式），起点 = 终点 = HOME；时间 `t = 弧长/1.5 m/s + 停靠 + 原地旋转`；朝向连续解缠，>9° 转角插入每帧 ≤10° 的旋转关键帧。
- **作业动作**：到达取货/交付点时 **转向作业面 → 停 2 s → 转回**（`face_deg` → `FACE`/`PICK`/`DROP` 关键帧），共 42 处；货物对象在 `t_pick` 出现并随车移动，`t_drop` 落在交付点后保持可见。
- **交通（严格口径）**：所有 21 台车从 `t=0` 到 `t_end` 全程在场，未派出 / 已回程的车停在 HOME，**不再隐藏或离场**；任意时刻任意两车中心距 ≥ `2×AGV_RADIUS = 1.4592 m`。优先级 + 出发错峰只是 WareTwin 的 tick 级 cell reservation 的轻量近似。
- **订单**：21 台车 = 21 张订单，全部 ASSIGNED，`PENDING=0`，覆盖 INBOUND / OUTBOUND / PICK / REPLENISH / TRANSFER 五种类型；按待命位所在前/后区派单，避免后排车为了去前排月台横穿全仓。
- **数据链**：`skus.json → events.jsonl → tasks.json → paths.jsonl → cargo_state.json → 货物动画` 与 `→ slotting.json → 托盘` 全部可追溯；392 个库位中 240 个已分配、152 个显式标 `EMPTY`。

## 7. 已知限制（如实声明）

1. **不是真实订单**：SKU 周转率为 Zipf 合成，21 张订单按前后区派单、每类 4–5 单，是演示设定而非真实订单分布。
2. **不是 tick 级时空预约**：全程在场的 0 冲突来自优先级错峰 + HOME 避让 + 0.05 s 采样验证，不是 CBS / WHCA* 的形式化证明，也不是 WareTwin 的 cell `reserved_by`。
3. **货物是订单级箱体**：一件货 = 一张单，未模拟拆箱、托盘层级、批次、叉臂升降；`cargo_state.json` 只记订单级状态与库位库存增减。
4. **货位优化是 distance_greedy**：目标函数 `Σ(velocity × BFS 通道距离)`，排序配对即 1D 最优（`gap_to_lower_bound=0`）；**未实现 COI 的 cube-per-order 与订单共现约束**。
5. **GLB 是静态预览**（`export_animations=False`），动画请看 MP4 / `.blend`；视频是 8 fps 匀速单轮演示，无加减速。
6. **运营层未做**：WMS / 车队管理 / 电量故障 / 站台排队 / 运营 KPI / 真实数据校准；每车只执行 1 张订单。
7. **场景装饰**（相机、灯、叉车、卡车、道路）参数来自 `config.scene`；园区卡车停在北侧 yard，不在月台门前（未摆拍）。
8. `events` 驱动任务、货物与视频角标，但未做逐帧库位颜色动画；材质为程序化纯色。

## 8. 常见错误

| 现象 | 处理 |
|---|---|
| `Blender not found` | 设置 `$env:BLENDER_EXE`，或修改 `run_all.ps1` 里的路径 |
| `ModuleNotFoundError: imageio_ffmpeg` | `pip install -r requirements.txt` |
| `ModuleNotFoundError: PIL` | 同上 |
| `LAYOUT_AUDIT FAIL` | 不要跳过；先修 `layout.json` 的实体重叠，`plan_paths` 依赖它 |
| `SCHEDULE_FAIL` | 检查任务点分布与冲突结构（缩短单点任务、增加通道），不要把 `MIN_SEP` 调小、也不要一味放宽 `max_schedule_wait_s` |
| JSON 中文在 PowerShell 5.1 显示乱码 | 用 Python 读；文件本身是 UTF-8 |

## 9. 第三方与许可

本项目自身使用 MIT（`LICENSE`）。第三方资产 / 衍生代码的版权声明与 MIT 全文见 `THIRD_PARTY_NOTICES.md`（cargoTwin、WareTwin）。资产哈希：

```
cargotwin_assets.glb  sha256 e6bf591fa0a69038bb9ccf782b43a1a2489d3e768637232edfc6647628fd0584
```

## 10. 图形化控制台

不想敲命令时用这个：一个本地 Web 控制台，把 `data/` 与 `out/` 的产物变成可操作、可预览的界面。

```powershell
.\start_console.bat
```

或手动：

```powershell
python console\server.py        # 默认 http://127.0.0.1:8765/
```

只用标准库，不新增依赖；不修改任何既有脚本，只读 `data/`、`out/`，写 `data/config.json`（写前备份为 `config.bak.json`）。

**2D / 3D 双视图**：画布左上角切换，共用同一条时间轴和同一份选中车状态。

| 视图 | 实现 | 内容 |
|---|---|---|
| 2D 俯视 | Canvas 矢量绘制 | 4 仓布局、货架、月台、分区、传送带；AGV 按相位分色 + 朝向箭头；选中车 7 秒尾迹与三段闭环路线（空驶去程 / 载货 / 空驶回程）；滚轮缩放、拖拽平移、点击选车 |
| 3D 透视 | three.js r160 + `out/dc_preview.glb` | 真实场景（882 节点 / 59 mesh）；21 台 AGV 由 `paths.jsonl` 实时驱动位置与朝向，头顶彩色圆点标相位；三种相机预设（斜视 / 俯视 / 近景）、跟随选中车、点击拾取。three.js 已本地内置（`console/static/vendor/`），**不依赖 CDN 或联网** |

3D 的坐标映射：GLB 是 Y-up，`layout [x, z]` → `glTF (x, h, −z)`；AGV 节点名就是 agent id（`W-R01` 这种），是 `DC_ROOT` 的直接子节点；朝向 `glTF rotation.y = π/2 − theta`。

**五个面板**

| 面板 | 内容 |
|---|---|
| 车辆 | 画布（2D/3D）+ 21 台 AGV 列表；点击车辆查看任务、相位、货物状态、三段里程 |
| 订单 | 21 张订单，点击跳到该单的取货时刻并锁定对应车辆 |
| 验收 | A1–A21 逐条 PASS/FAIL 与证据详情，附 7 个负测试用例（验证器必须能失败） |
| 参数 | 白名单字段编辑并写回 `config.json`（带范围校验与 A7 越界警告）；分级重跑并流式显示脚本日志 |
| 产物 | 10 张渲染图灯箱、`anim_dc.mp4` 播放器、`CURRENT_STATUS.md` 状态表 |

**时间轴**：拖拽 scrub、`0.25×–8×` 倍速、空格播放/暂停、`←/→` 步进 0.5 s、`Home/End` 跳到首尾。

**分级重跑**（改完参数后按需要选）

| 范围 | 步骤 | 说明 |
|---|---|---|
| 数据层重跑 | 10 步纯 Python | 布局 → 栅格 → 派单 → 路径 → 校验 → 路线图，约 10 s |
| 仅复验 | 3 步 | 负测试 + A1–A21 + 状态表，不重算数据 |
| 全链路重跑 | 21 步 | 追加 Blender 建模、动画、渲染 942 帧，最慢 |

深链接便于复现某个画面：`http://127.0.0.1:8765/?t=40&sel=W-R03&view=3d&cam=iso&tab=audit&play=1`
（`t` 秒、`sel` 车辆、`view` 2d/3d、`cam` iso/top/close、`tab` 面板、`play=1` 自动播放）。

**接口**：`GET /api/bootstrap`（汇总）、`GET /api/paths`（按车关键帧）、`POST /api/config`（写参数）、`POST /api/run`（启动重跑）、`GET /api/job?since=N`（增量日志）、`POST /api/cancel`。

