# ADR-21：产物清单是「快照」，契约不能要求生成物永远在盘

- 状态：已采纳（v1.0 冻结后修正）
- 触发：一次工作区清理让 `test_manifest_artifact_hashes_match_disk` 变红，
  且 `report_status.py` / `check_doc_drift.py` 直接抛 `WinError 3`（找不到 `runs/`）。

## 1. 现象与误判

清理 `out/frames`、`runs/`、`build/`、`artifacts/`、`out/exp/` 之后：

| 症状 | 表面结论 | 实际结论 |
|---|---|---|
| 契约测试 1 红 | "产物被删坏了" | manifest 钉了 25 个已删文件，其中含 `junit2.xml`、`pytest2.log`、`out/r810/*.bak` |
| 状态脚本崩溃 | "清理把工具链弄坏了" | `os.listdir(ROOT/'runs')` 假设目录永在 |
| 口径门崩溃 | 同上 | 同一假设，复制了两份 |

**这三条都不是"某次提交失败"，而是"生成物被当成了不可变的真相"。** 用户侧的观感
是"每次操作都要补提交/修红"，根因在这里。

## 2. 决定

1. **收集用白名单，不用 `rglob` + 黑名单。**
   `manifest.artifact_files()` 只收两处：`out/` 顶层文件、`out/renders/` 下的图。
   顶层是交付真相，renders 是截图。调试目录（`r810`、`exp`、`frames`）与临时文件
   （`*.log`、`*.bak`、`junit*.xml`）**在结构上无法进入清单**，不靠逐条排除。
2. **每条产物带 `class`。**
   - `required`：顶层报告 / 三锚 → 缺失或哈希不符 = 漂移，判红；
   - `rebuildable`：`out/renders/*` → 缺失只计入 `cleared`，不判红。
   清理工作区不该等于交付损坏；但金标准缺了必须红。
3. **工具链不得假设生成目录存在。**
   `report_status.art_count()` 直接调 `manifest.artifact_files()`（同一份规则只写一次），
   `runs/` 缺失时按 0 计。
4. **契约测试的双向要求**（缺一不可）：
   - 删一张截图 → 仍绿（否则清理永远假红）；
   - 删 `out/areas_report.json` → 立即红（否则门成了橡皮章）。

## 3. 验证

```
删 renders 一张图  → 2 passed, MANIFEST_PASS cleared=1
删 areas_report    → 1 failed（必需产物缺失）
恢复后             → 2 passed
artifacts 126 → 96（排除的正是调试垃圾与已清理目录）
```

## 4. 没有解决的部分（诚实记账）

- `check_asset_dispatch --probe` 仍会重写主报告，导致"跑完 acceptance 再跑契约"
  那 2 条依旧红。那是**顺序**问题，不是本 ADR 的范围；根治仍是 backlog 第 10 条
  （probe 报告写到 `out/exp/`）。
- 若将来新增 `out/` 下的子目录并希望它进清单，必须显式加进 `REBUILDABLE_DIRS`
  或改白名单——这是有意为之：不进去比"误进后被钉住"安全。
