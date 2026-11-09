# 配送中心数字孪生 · GitHub 开源补齐方案

> 评估对象：`dc_project/`（134 个被 git 跟踪的文件、3 次提交、无 remote、无 tag、无 CI）  
> 本方案不重复已经做好的部分：MIT/NOTICE、A1–A40 验收、17 个负测试、四层配置、25 个 KPI、2D/3D 控制台。  
> 核心判断：**算法与验收体系已超过多数同类开源项目；真正缺的是“把能力交付给陌生人”的产品层**——看得见、跑得通、信得过、能参与。

---

## 0. 最该补的 10 件事

| # | 缺口 | 为什么影响开源 |
|---|---|---|
| 1 | 没有静态在线 Demo，README 里也没有一张图 | 访客点进仓库看不到任何成果；10 张渲染图白白躺在 `out/` |
| 2 | 唯一入口是 Windows PowerShell + 本机 Blender 路径 | Linux/macOS 用户第一步就失败 |
| 3 | 构建产物、临时测试文件、`config.bak.json` 入库，`runs/` 反而被忽略 | 仓库臃肿、diff 噪音；全新 clone 时控制台“场景”面板是空的 |
| 4 | `run_all.ps1` 硬编码 `C:\Users\OYC\.workbuddy\...` | 换一台机器直接不可运行 |
| 5 | README 引用 `DESIGN_PLAN.md` / `AUDIT_REPORT.md`，仓库内不存在 | 第一屏就产生“文档不可信”的印象 |
| 6 | 无 CI、无 pytest、控制台 0 测试 | 贡献者无法确认自己的改动没破坏 40 条验收 |
| 7 | 只有输入 schema，没有输出 schema / 版本号 / 统一 manifest | 下游解析脆弱，报告形状还不一致 |
| 8 | 无 tag、无 Release、无 checksum | `.blend/.glb/.mp4` 只能靠 clone，无法作为交付物引用 |
| 9 | 控制台全局单 JOB、配置写入非原子、备份覆盖、轮询日志 | 并发或中断可能损坏数据，也不像“产品” |
| 10 | 8 个场景是 M/M/c 解析近似，且复用主链路径指标 | “场景 KPI”与“逐车 A*”是两套口径，可信度有裂缝 |

---

## 1. 现状证据：只列会阻断开源体验的细节

| 细节 | 证据 | 影响 |
|---|---|---|
| 仓库未进入可发布状态 | `git remote -v` 为空；只有 `master` 的 3 次提交；README、console 文件、`validate_report.json` 仍有未提交改动 | 无法形成稳定基线，别人 clone 到的不是“发布态” |
| 个人环境硬编码 | `run_all.ps1:33` 指向 `C:\Users\OYC\.workbuddy\binaries\python\versions\3.13.12\python.exe` | 可移植性直接归零 |
| 文档死链 | `README.md:5` 引用 `AUDIT_REPORT.md` / `DESIGN_PLAN.md`；仓库内没有这两个文件（`DESIGN_PLAN.md` 只在工作区上一级） | 首屏可信度受损 |
| 生成物混入源码 | `git ls-files` 含 `out/distribution_center.blend`、`out/dc_preview.glb`、`out/anim_dc.mp4`、10 张 PNG、`tests/_tmp_spec/*`、`data/config.bak.json` | 仓库约 15.5 MiB；每次重跑都脏 diff；临时文件误导贡献者 |
| 演示数据缺失 | `.gitignore` 忽略 `runs/`，但控制台“场景”面板读取 `runs/` | 新 clone 打开控制台，场景对比、断言、KPI 面板为空 |
| 没有“门面” | README 中没有 `![]` 或 `<img>`，也没有 badge/GIF；全文中文 | GitHub 访客 30 秒内看不到项目长什么样 |
| 单平台入口 | `run_all.ps1`、`start_console.bat` 仅 Windows；Blender 探测只覆盖 Windows 安装目录 | Linux/macOS 无法按文档运行 |
| 无工程化配置 | 无 `.github/`、`pyproject.toml`、`.gitattributes`、`.editorconfig`、`pre-commit` | 无法自动验证、无法 `pip install`、CRLF/编码问题只能靠注释提醒 |
| 输出契约不齐 | 仅 `config/schemas/` 4 份输入 schema；`out/validate_report.json` 顶层是 **list**，其余 `*_report.json` 是 dict；无统一 `schema_version` | 下游脚本和第三方集成容易碎 |
| 测试不可发现 | `tests/` 是 4 个自写脚本 + 自写 runner；无 pytest、无 coverage；console 无测试；`validate.py:249-258` 的 A13 读取自身源码字符串 | 贡献者无法“只跑一个测试”；A13 存在自证风险 |
| 控制台健壮性 | `console/server.py:166` 全局 `JOB`；`handle_config` 先 `copyfile` 再直接覆盖写；`config.bak.json` 每次被覆盖；日志靠 1 秒轮询 | 并发/中断/多标签页可能坏数据；没有作业历史 |
| 场景口径混用 | `scripts/kpi.py:53-54` 在场景 KPI 中默认回退读取主链 `data/path_metrics.json` 与 `out/traffic_report.json`；`scenario_run.py` 未传 `main` | 8 个场景的 KPI 不是独立仿真结果，只是解析近似 + 主链路径统计 |
| 时间与溯源不完整 | `scenario_run.py:118-123` 用无时区的 `datetime.now()`；主链 `out/` 没有统一 manifest；`baseline_sha256.json` 靠手工维护 | 跨时区复现、审计、CI 比对都不可靠 |
| 依赖不可复现 | `requirements.txt` 只锁 3 个直接依赖，无 hash、无传递依赖锁 | 半年后环境漂移，别人跑不出同样结果 |
| 远程暴露无保护 | 默认 `127.0.0.1` 是好的，但 `DC_CONSOLE_HOST` 可改成 `0.0.0.0`，而 `/api/run`、`/api/config` 无鉴权 | 误配置时可被远程触发重跑、改配置 |

---

## 2. 差距地图：按“陌生人旅程”分层

| 阶段 | 用户问题 | 当前状态 | 需要补的能力 |
|---|---|---|---|
| 看见 | 这是什么？值得看吗？ | 有 10 张渲染图，但 README 一张没展示；无在线 Demo | 静态演示站、GIF/WebM、Hero README、架构图 |
| 跑通 | 我能 5 分钟跑起来吗？ | 需要 Windows + Blender 5.2 + 本机 Python 路径 | 跨平台 CLI、`dc demo`、`dc doctor`、无需 Blender 的数据层 |
| 相信 | 这些数字可信吗？ | A1–A40 很扎实，但场景层是解析近似，输出无契约 | 输出 schema、manifest、全仿真场景、口径标签 |
| 贡献 | 我能改哪里？怎么验证？ | 无 CONTRIBUTING、无 CI、测试是自写脚本 | pytest、CI 矩阵、模板、扩展指南、good first issue |
| 交付 | 我能拿走结果吗？ | 产物在 git 里，无 Release | Release assets、checksum、SBOM、静态导出 |

---

## 3. P0：发布阻断项（建议 3–5 人日）

> 目标：把仓库从“本机工程”变成“别人能 clone、能跑、能看”的 v0.1.0。

### 3.1 仓库卫生与可克隆性

**要做的事**

1. 清理被跟踪的生成物与临时文件：

   ```powershell
   git rm -r --cached tests/_tmp_spec data/config.bak.json
   git rm --cached out/distribution_center.blend out/dc_preview.glb out/anim_dc.mp4
   git rm --cached out/renders/*.png
   ```

   如果仍希望仓库自带“开箱即用”的演示，把必要文件移到 `examples/release/` 或用 Git LFS，不要和源码混在 `out/`。

2. 调整 `.gitignore`：

   ```gitignore
   out/*
   !out/CURRENT_STATUS.md
   !out/renders/.gitkeep
   runs/
   tests/_tmp_spec/
   data/*.bak
   ```

3. 新增 `.gitattributes`：

   ```gitattributes
   * text=auto eol=lf
   *.ps1 text eol=crlf
   *.bat text eol=crlf
   *.png binary
   *.jpg binary
   *.glb binary
   *.blend binary
   *.mp4 binary
   ```

4. 新增 `.editorconfig`，统一 UTF-8、LF、缩进。

5. 把默认分支改为 `main`，提交当前未提交的 console/README 变更，打第一个 tag。

6. 把 `DESIGN_PLAN.md` 移入 `docs/design/` 并纳入 git；补一份 `AUDIT_REPORT.md`，或删除 README 中的引用。

**验收**

```powershell
git clone <repo> fresh
cd fresh
git status --porcelain     # 必须为空
du -sh .git                # 建议 < 5 MiB
```

---

### 3.2 一条命令跑起来：`dc` CLI + `doctor`

**不要重写现有脚本**。先加一个薄封装层：

```text
dc_twin/
├─ __main__.py
├─ cli.py          # argparse 子命令
├─ doctor.py       # 环境诊断
├─ runner.py       # 调用现有 scripts/*.py
└─ paths.py        # 项目根、输出目录、Blender 探测
```

**最小命令集**

| 命令 | 作用 |
|---|---|
| `dc doctor` | 检查 Python/Blender/依赖/端口/写权限/路径长度，输出可粘贴到 issue 的诊断块 |
| `dc demo` | 纯数据层生成 + 启动控制台，**不要求 Blender** |
| `dc run --scope data\|scenarios\|full --seed 20261106` | 替代 `run_all.ps1` |
| `dc validate --tier data\|render\|full --json` | 分档验收，CI 才能跑 |
| `dc serve --port 8765` | 启动控制台 |
| `dc export --target demo\|release` | 静态演示 / 发布包 |

**关键修复**

- 删除 `run_all.ps1` 里的个人 Python 路径；按 `.venv` → `sys.executable` → `PATH` 解析。
- Blender 探测改为：`BLENDER_EXE` → `shutil.which` → Windows/macOS/Linux 常见安装目录。
- 新增 `run.sh` / `run.py`，`run_all.ps1` 只作为 Windows 包装器。
- `dc doctor` 明确提示“Blender 未安装时哪些步骤会跳过”。

**验收**

- Windows 与 Ubuntu 都能：

  ```bash
  python -m pip install -e .
  dc demo
  ```

  3 分钟内打开控制台并看到 2D/3D 视图。
- `dc run --scope data` 在 15 秒内完成。
- `grep -R "C:\\Users"` 在仓库中为空。

---

### 3.3 GitHub 门面：把“运行手册”改成“项目主页”

当前 README 18 KB，信息密度很高，但更像内部 runbook。建议拆成：

```text
README.md              # 英文主页面：一句话、GIF、badges、3 条命令、能力表、限制、文档链接
README.zh-CN.md        # 中文版
docs/
├─ getting-started.md
├─ architecture.md
├─ data-contracts.md
├─ scenarios.md
├─ console.md
├─ cli.md
├─ extending.md
├─ troubleshooting.md
└─ design/DESIGN_PLAN.md
```

**README 首屏必须包含**

1. 一句话定位：`Blender-based digital twin for a 4-warehouse distribution center with 21 AGVs, reproducible data chain and 40 acceptance checks.`
2. 一张 10–20 秒 GIF 或 WebM：控制台 2D/3D 切换 + 时间轴播放。
3. 3–5 张优化后的截图（WebP/JPEG，单张 < 500 KB），不要直接放 2.5 MB PNG。
4. badges：CI、License、Python、Blender、Release。
5. 3 条命令：

   ```bash
   pip install -e .
   dc demo
   dc run --scope data
   ```

6. “What it is / What it is not” 两段，明确合成数据、解析场景、非生产 WMS。
7. Mermaid 架构图：`config → derived data → validators → Blender → artifacts → console`。
8. 指向 `docs/`、`CITATION.cff`、Release 的链接。

**同时补**

- `CITATION.cff`
- `CHANGELOG.md`（Keep a Changelog）
- GitHub About：description、topics（`digital-twin`, `warehouse`, `agv`, `blender`, `simulation`, `logistics`）、social preview、Pages 链接
- favicon / logo（当前 `/favicon.ico` 返回 204）

**验收**

- 用 `lychee` 检查无死链。
- 一个没看过项目的人，30 秒内能说出“它是什么、怎么跑、有什么限制”。

---

### 3.4 CI 最小闭环

新增 `.github/workflows/ci.yml`，先做“无 Blender 也能绿”的矩阵：

```yaml
name: ci
on: [push, pull_request]

jobs:
  data:
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
        python: ["3.11", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: python -m pip install -e ".[dev]"
      - run: ruff check .
      - run: pytest -m "not blender" --cov
      - run: dc validate --tier data --junit out/junit.xml
```

另加一个慢速 Blender job：

- 下载官方 portable Blender 5.2（或使用可用的 `bpy` wheel）；
- 只跑 **1 帧** 预览渲染或 Workbench 渲染；
- 上传截图作为 artifact；
- 不跑 942 帧全量渲染。

配套：

- `pyproject.toml`：项目元数据、依赖、`[project.optional-dependencies] dev/docs`、pytest/ruff 配置。
- `requirements-dev.txt` 或 `uv.lock` / `pip-compile --generate-hashes`。
- `pre-commit`：ruff、trailing-whitespace、end-of-file-fixer、check-json、check-yaml、detect-private-key。
- Dependabot + CodeQL（可选）。
- `main` 分支保护，要求 CI 通过。

**验收**

- README 顶部 CI badge 为绿。
- PR 不跑测试无法合并。
- CI 总时长 < 10 分钟。

---

### 3.5 发布 v0.1.0

- 语义化版本：`0.1.0`。
- `CHANGELOG.md` 写清“首个可复现版本”。
- GitHub Release 附件：
  - `distribution_center.blend`
  - `dc_preview.glb`
  - `anim_dc.mp4`
  - `renders/*.png`
  - `checksums.txt`（SHA256）
  - SBOM（CycloneDX，可选）
- Release 说明里给“复现此版本”的 commit SHA + `baseline_sha256.json`。
- 把 `out/` 从 git 跟踪中移除，由 `dc export --release` 生成发布包。

---

## 4. P1：可信度工程（建议 10–15 人日）

> 目标：让“数字”和“报告”可被机器验证，让贡献者能局部测试。

### 4.1 输出契约与数据血缘

**问题**：输入有 4 份 schema，输出没有；`validate_report.json` 是 list，其他报告是 dict；没有统一版本号。

**建议**

1. 新增 `schemas/reports/`：

   ```text
   traffic_report.schema.json
   geometry_report.schema.json
   route_report.schema.json
   fleet_report.schema.json
   reservation_report.schema.json
   validate_report.schema.json
   ```

2. 统一“报告信封”，但**不要一次性破坏现有格式**。两种落地方式：
   - 推荐：保留原 payload，另写 `out/<name>.meta.json`；
   - 或者逐步加 `--envelope` 模式。

   信封示例：

   ```json
   {
     "schema_version": "1.0",
     "kind": "traffic_report",
     "generated_at": "2026-11-08T10:25:28Z",
     "generator": {
       "name": "dc-twin",
       "version": "0.1.0",
       "git_commit": "d82ed14",
       "python": "3.13.14",
       "blender": "5.2.3"
     },
     "inputs": [
       {"path": "config/layout_spec.json", "sha256": "..."}
     ],
     "verdict": "PASS",
     "data": { }
   }
   ```

3. 生成 `out/manifest.json`：
   - 所有产物路径、大小、SHA256；
   - 输入文件哈希；
   - 工具版本、git commit、seed；
   - 生成时间用 UTC（`...Z`）。

4. `dc validate --check`：
   - 重新生成关键报告并与 manifest 比对；
   - CI 中检测“报告过期”或“派生数据漂移”。

5. 修掉 `kpi.py` 的两个隐患：
   - `avg_path=84.05` 这种静默 fallback 改为 **fail loud**，或标记为 `assumption` 并写入 KPI 元数据；
   - 场景 KPI 如果继续复用主链 `path_metrics`/`traffic_report`，必须在报告里标 `source: baseline_main_chain`，不能让人误以为是场景独立结果。

6. 所有 JSONL 写入时逐行校验，避免半行/坏行进入下游。

**验收**

- 所有 `out/*.json` 通过 schema 校验。
- `out/manifest.json` 可复现；CI `--check` 不产生 diff。
- 控制台能根据 `schema_version` 拒绝不兼容报告，而不是静默显示错数据。

---

### 4.2 测试体系：从“自写脚本”到“可发现、可局部运行”

保留现有负测试作为“验收证明”，但增加标准测试层：

```text
tests/
├─ unit/
│  ├─ test_geometry.py
│  ├─ test_kpi.py
│  ├─ test_spec_layout.py
│  └─ test_schemas.py
├─ contract/
│  ├─ test_report_contracts.py
│  └─ test_manifest.py
├─ integration/
│  ├─ test_data_chain.py
│  └─ test_cleanroom.py
├─ e2e/
│  └─ test_console_api.py
└─ blender/
   └─ test_render_smoke.py
```

**关键点**

- 用 `pytest` 标记：`@pytest.mark.blender`、`@pytest.mark.slow`；CI 默认跳过 Blender。
- 覆盖率门槛：核心模块 ≥ 70%，整体 ≥ 50%；先做“可见”，不要为覆盖率写无意义测试。
- 输出可追溯矩阵：`A1..A40 → test_id`，由 `validate.py` 的输出自动生成 `out/traceability.md`。
- 替换 A13 的“读自身源码”检查：用行为测试证明——删除/扰动一个产物后，`validate` 必须退出 1。
- 增加 `hypothesis` 属性测试：几何膨胀、线段碰撞、A* 起终点、KPI 单调性。
- 增加 golden snapshot：`tests/golden/kpi_baseline.json`，时间类指标用容差。
- 控制台 API 测试：用临时目录 + 端口 0 启动 `ThreadingHTTPServer`，覆盖：
  - `/api/bootstrap` 正常返回；
  - 路径穿越被拒绝；
  - 非法 config 字段被拒绝；
  - 并发 `/api/run` 被拒绝；
  - 作业取消能杀掉进程树。
- 测试临时文件统一用 `tmp_path`，不再提交 `tests/_tmp_spec`。

**验收**

```bash
pytest -m "not blender" --cov
pytest -m blender
dc validate --tier data --json
```

---

### 4.3 CLI、日志与错误体验

- 所有命令统一 `argparse` 子命令，支持：
  - `--json`：机器可读输出；
  - `--quiet/-v`：日志级别；
  - `--seed`：全局种子；
  - `--out`：输出目录；
  - `--dry-run`：只打印将执行的步骤；
  - `--jobs N`：并行度（先只用于独立校验步骤）。
- 结构化日志：`runs/logs/<timestamp>.jsonl`，字段：

  ```json
  {"ts":"...","level":"info","step":"plan_paths","event":"start","duration_s":null}
  ```

- 预期错误不要抛裸 traceback：
  - `DC-E1001 Blender not found`
  - `DC-E2003 layout_spec validation failed at racks[2].origin`
  - `DC-E3001 report schema mismatch: traffic_report`
- 错误码写入 `docs/errors.md`，`dc doctor` 输出可直接粘贴 issue。
- 长步骤加进度与 ETA；`render` 增加 `--preview`（低分辨率/1 帧）和 `--frames 1-10`。

---

### 4.4 控制台健壮性

- `JOB` 单例 → 作业注册表：
  - `runs/jobs/<id>.json` 持久化；
  - 历史列表、状态、日志、取消；
  - 同一时间只允许一个“写数据”的 job。
- 写文件：
  - 先写临时文件，再 `os.replace` 原子替换；
  - 加文件锁（Windows `msvcrt` / POSIX `fcntl`）；
  - `config.bak.json` 改为带时间戳的轮转备份，保留最近 N 份。
- 取消：
  - POSIX `killpg`；
  - Windows `taskkill /T /F`；
  - 取消后清理子进程和临时目录。
- 日志：
  - 1 秒轮询改为 SSE（`/api/job/stream`）或长轮询；
  - 保留增量拉取作为 fallback。
- API：
  - `/api/v1/...`；
  - `GET /api/openapi.json`；
  - 统一错误码与 request id。
- 安全：
  - 默认只允许 loopback；
  - `DC_CONSOLE_HOST=0.0.0.0` 时强制要求 `DC_CONSOLE_TOKEN`，否则拒绝启动；
  - 校验 `Origin`/`Host`；
  - 加 CSP、`X-Content-Type-Options`；
  - 文档写明“这是本地工具，不是多用户服务”。
- 前端：
  - 52 KB 的 `app.js` 拆成 `viewer/ timeline/ panels/ api/` 模块；
  - 增加错误提示和空状态（例如 `runs/` 不存在时提示“先运行 dc run --scope scenarios”）。

---

## 5. P2：好用与差异化（建议 15–25 人日）

### 5.1 静态在线 Demo（最高 ROI）

现状：控制台必须 `python console/server.py`，且依赖 `runs/`。  
目标：GitHub Pages 上直接可玩，不需要后端。

**做法**

- 新增 `dc export --target demo`：
  - 复制 `console/static`；
  - 生成静态 `demo/data/bootstrap.json`、`demo/data/paths.json`、`demo/data/scenarios.json`；
  - 把 `dc_preview.glb` 放到 `demo/assets/`；
  - 把 `/api/*` 调用替换为静态 JSON 读取；
  - 输出 `demo/index.html`，支持 `?embed=1`。
- GitHub Actions 自动部署 `demo/` 到 Pages。
- README 首屏放 GIF + “Live Demo” 链接。

**验收**

- 无 Python、无网络后端，浏览器打开即可 2D/3D 播放。
- 移动端至少能查看，不要求完整编辑。

---

### 5.2 运行对比、分享与导出

- `dc compare <runA> <runB>`：
  - 25 个 KPI 的 delta；
  - 断言变化；
  - 配置差异；
  - 输出 Markdown/HTML/CSV。
- 控制台：
  - 固定某个 run（`?run=baseline/20261108-182528`）；
  - “复制复现命令”按钮，生成带 seed/config hash 的命令；
  - 导出当前视图 PNG、KPI CSV、完整 HTML 报告；
  - “设为 baseline” 操作。
- `runs/index.json` + 保留策略：最近 N 次、可手动 pin、可删除。

---

### 5.3 场景层“真仿真”

现状：8 个场景是 M/M/c 解析近似，且复用主链路径指标。  
目标：至少一个场景能跑完整 A* 主链，形成“快/准”双引擎。

**做法**

- `scenario_run --engine full`：
  - 在临时工作区应用 overrides（layout/fleet/demand）；
  - 跑 `gen_data → plan_paths → traffic_check → kpi`；
  - 输出 `measured` KPI；
  - 按配置哈希缓存，避免重复计算。
- 控制台给每个场景加 `engine: analytic | full` 徽章。
- CI 只跑一个 `peak` 全仿真场景做冒烟。
- 保留解析引擎作为快速 what-if，但 UI 必须明确区分。

**验收**

- 同一个场景在 analytic 与 full 下的差异被显式展示，而不是混在一个数字里。
- README 的“已知限制”同步更新。

---

### 5.4 故障与电量闭环

现状：`fleet_sim.py` 是离线回放；电量/故障没有进入调度循环。  
目标：让“robot_failure / dock_blocked / night”从解析参数变成可观察行为。

**最小闭环**

- 充电桩：SOC、reserve、排队、充电时长；
- `robot_down`：任务重分配、恢复时间；
- `dock_blocked`：改道/改月台；
- 多订单/任务队列：一台车执行多单；
- 新增 KPI：可用率、充电等待、任务 tardiness、恢复时间。

**注意**

- 不要在没有连续时间预约的情况下宣称“零碰撞证明”；
- 文案用 `sampled verification` / `reservation check`，不要用 `formal guarantee`。

---

### 5.5 布局编辑器

把“改 JSON 不改 Python”的承诺变成可视化能力：

- 2D 画布拖拽货架/月台/AP/待命位；
- 实时跑 L1–L12 校验；
- 修改 `layout_spec.json` 前先 dry-run；
- 显示与当前布局的 diff；
- 导出新 spec + 一键重跑数据层。

后端只加两个只读/试算接口：

```text
POST /api/layout/validate
POST /api/layout/dry-run
```

写入仍走显式“保存并重跑”，避免误操作。

---

### 5.6 国际化与可访问性

- 控制台 `lang="zh-CN"` 硬编码；增加 `?lang=en` 与 `locales/{zh,en}.json`。
- 英文 UI + 英文 README/docs（建议英文为主，中文为翻译）。
- 可访问性：
  - canvas 提供“表格视图/CSV 导出”作为替代；
  - 键盘可选车、可播放/暂停；
  - 焦点可见；
  - 相位颜色增加图案/文字，不只靠颜色；
  - `prefers-reduced-motion`；
  - 对比度检查。
- 响应式：至少 1280/768 两档；触屏可拖拽时间轴。

---

## 6. P3：生态与领域深度（按需，不建议先做）

| 方向 | 说明 | 何时做 |
|---|---|---|
| 连续时间预约 | SIPP / WHCA* / CBS，替换 0.05 s 采样验证 | 有学术或工业合作时 |
| VDA 5050 / WMS 适配 | 认证、幂等、重试、schema 版本 | 有真实系统对接时 |
| 多楼层 / 异构车队 / 电梯 | 扩展布局与调度模型 | 有明确场景时 |
| 插件 API | `entry_points` 注册 adapter/KPI/scenario | 有第三方贡献时 |
| 规模基准 | 21/50/100/200 AGV 的运行时、内存、冲突率 | P2 完成后 |
| 学术打包 | 论文、DOI、复现实验包 | 有发表计划时 |

---

## 7. 目标目录结构

```text
dc_project/
├─ pyproject.toml
├─ README.md
├─ README.zh-CN.md
├─ CHANGELOG.md
├─ CONTRIBUTING.md
├─ CODE_OF_CONDUCT.md
├─ SECURITY.md
├─ CITATION.cff
├─ .gitattributes
├─ .editorconfig
├─ .pre-commit-config.yaml
├─ .github/
│  ├─ workflows/ci.yml
│  ├─ workflows/release.yml
│  ├─ ISSUE_TEMPLATE/bug.yml
│  ├─ ISSUE_TEMPLATE/feature.yml
│  ├─ PULL_REQUEST_TEMPLATE.md
│  └─ CODEOWNERS
├─ dc_twin/                  # 新 CLI 薄封装，不重写旧脚本
│  ├─ cli.py
│  ├─ doctor.py
│  ├─ runner.py
│  └─ paths.py
├─ scripts/                  # 保持兼容
├─ schemas/
│  ├─ input/                 # 现有 4 份
│  └─ reports/               # 新增输出 schema
├─ docs/
│  ├─ en/
│  ├─ zh/
│  ├─ adr/
│  ├─ assets/                # 优化后的截图/GIF
│  └─ design/DESIGN_PLAN.md
├─ examples/
│  ├─ minimal/
│  └─ runs/baseline/         # 可选：小型演示快照
├─ tests/
│  ├─ unit/
│  ├─ contract/
│  ├─ integration/
│  ├─ e2e/
│  └─ blender/
└─ demo/                     # 生成物，部署 Pages
```

---

## 8. 里程碑与验收

| 里程碑 | 主题 | 主要交付 | 验收命令 |
|---|---|---|---|
| **M0 / v0.1.0** | 能看见、能跑 | 仓库清理、跨平台 CLI、`dc doctor`、`dc demo`、Hero README、CI、Release | `git clone && dc demo`；CI 绿；README 有图 |
| **M1 / v0.2.0** | 能相信 | 输出 schema、manifest、`validate --tier`、pytest/coverage、控制台测试 | `dc validate --tier data --json`；`pytest --cov` |
| **M2 / v0.3.0** | 能玩、能分享 | 静态 Demo、run compare、导出、全仿真场景、布局编辑器、i18n | Pages Demo 可玩；`dc compare` 有输出 |
| **M3 / v0.4.0** | 能扩展 | 故障/电量闭环、插件 API、规模基准、连续时间预约（可选） | 新场景模板 15 分钟跑通 |

**每个里程碑的统一 Definition of Done**

- 新增/修改的公开行为有测试；
- 文档、schema、CHANGELOG 同步；
- CI 通过，且没有新增绝对路径/大文件；
- `dc doctor` 在干净环境可用；
- 至少一条“从零复现”的命令写进 README。

---

## 9. 首批可直接开的 issue / PR

| # | 标题 | 关键文件 | 完成标准 |
|---|---|---|---|
| 1 | `chore: remove generated artifacts and temp files from git` | `.gitignore`, `git rm --cached` | clone 后 `git status` 干净，`.git` < 5 MiB |
| 2 | `fix: remove hardcoded personal Python path` | `run_all.ps1` | `grep C:\Users` 为空 |
| 3 | `docs: fix missing DESIGN_PLAN/AUDIT_REPORT links` | `README.md`, `docs/design/` | 链接检查通过 |
| 4 | `feat: add cross-platform dc CLI and doctor` | `dc_twin/`, `pyproject.toml` | `dc doctor` 在 Windows/Linux 输出正确 |
| 5 | `feat: add data-only demo scope` | `dc_twin/runner.py` | 无 Blender 可启动控制台 |
| 6 | `ci: add GitHub Actions data matrix` | `.github/workflows/ci.yml` | Ubuntu/Windows × 3.11/3.13 绿 |
| 7 | `test: adopt pytest and add core unit tests` | `tests/unit/*`, `pyproject.toml` | `pytest -m "not blender"` 通过 |
| 8 | `feat: output report schemas and manifest` | `schemas/reports/*`, `scripts/report_status.py` | 所有报告 schema-valid |
| 9 | `feat: static demo export for GitHub Pages` | `dc_twin/export.py`, `demo/` | 无后端可播放 |
| 10 | `docs: English README with screenshots and GIF` | `README.md`, `docs/assets/` | 首屏有 3 张以上可视化 |
| 11 | `chore: add .gitattributes/.editorconfig/pre-commit` | 根目录 | CRLF/编码问题不再靠注释 |
| 12 | `feat: console job registry and atomic writes` | `console/server.py` | 并发 run 被拒绝；中断不坏配置 |
| 13 | `test: console API integration tests` | `tests/e2e/test_console_api.py` | 覆盖路径穿越/非法配置/取消 |
| 14 | `feat: compare two runs` | `dc_twin/compare.py` | 输出 KPI delta 表 |
| 15 | `feat: full-simulation scenario engine` | `scripts/scenario_run.py`, `scripts/kpi.py` | `peak --engine full` 产出 measured KPI |
| 16 | `docs: add CONTRIBUTING/SECURITY/CODE_OF_CONDUCT/CITATION` | 根目录 | GitHub 社区健康文件齐全 |
| 17 | `release: v0.1.0 with assets and checksums` | `CHANGELOG.md`, Release | Release 可下载、校验通过 |

---

## 10. 取舍与风险

**不要做**

- 不要为了“开源观感”重写已经稳定的仿真链；先包一层 CLI。
- 不要把 8 个解析场景包装成逐车仿真；口径必须显式。
- 不要在没有连续时间预约的情况下宣称“零碰撞证明”。
- 不要引入 React/Vue/数据库；当前标准库控制台是优势。
- 不要继续把 18 MB 渲染图塞进 git；用 Release/LFS/优化图。
- 不要为覆盖率写无意义测试；优先核心模块和契约。

**主要风险**

| 风险 | 缓解 |
|---|---|
| CI 跑 Blender 慢/不稳定 | 只跑 1 帧预览；full render 放到手动 workflow |
| 拆包导致旧脚本导入失败 | 先保留 `scripts/`，CLI 通过 subprocess 调用；后续再加 deprecation shim |
| 输出 schema 改动破坏控制台 | 先加 sidecar `.meta.json`，再逐步启用信封模式 |
| 英文文档与中文漂移 | 英文为主、中文翻译；CI 链接检查；关键数字由脚本生成 |
| 数据许可不清 | 增加 `DATA_LICENSE`（合成数据建议 CC0/CC BY 4.0），明确不含真实客户数据 |
| 仓库仍被大文件污染 | PR 检查文件大小；`pre-commit` + CI 拒绝 > 5 MB 新文件 |
| 时间戳/时区导致复现差异 | 统一 UTC ISO8601；KPI 区分 `wall_clock_s` 与 `sim_duration_s` |

---

## 11. 一句话总结

这个项目的“技术骨架”已经足够强，下一步不是再加算法，而是补齐四件事：

1. **看得见**：静态 Demo + README 首屏可视化；
2. **跑得通**：跨平台 `dc demo` + `dc doctor` + 去掉个人路径；
3. **信得过**：输出 schema、manifest、分档验收、全仿真场景；
4. **能参与**：pytest + CI + CONTRIBUTING + 模板 + Release。

把这四件事做完，它就会从“一个很强的本机工程”变成“一个优秀的 GitHub 开源项目”。
