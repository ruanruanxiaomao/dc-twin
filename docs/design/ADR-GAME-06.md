# ADR-GAME-06 · GPL 参考隔离

- 状态：已接受
- 日期：2026-11-12
- 关联：计划报告 §6 ADR-06 / §10、方案书 §11、`docs/GITHUB_REFERENCE_AUDIT.md`

## 背景

参考的几个最贴合的开源游戏：shapez.io、Mindustry、OpenTTD、Simutrans、OpenRCT2 —— **全部 GPLv3**。
本项目是 Apache-2.0。GPLv3 代码一旦并入，整个项目被迫 GPL，与既有 `LICENSE`、`NOTICE`、
`THIRD_PARTY_NOTICES.md` 以及"A1–A40 中 A39 许可登记"直接冲突。

## 决策

| 来源 | 许可 | 允许的做法 |
|---|---|---|
| shapez.io / Mindustry / OpenTTD / Simutrans / OpenRCT2 | GPLv3 | **仅阅读架构与设计思路，不复制任何代码**（不抄函数体、不抄常量表、不抄注释） |
| Kittens Game | MIT | 可借鉴代码，须保留版权声明并登记到 `THIRD_PARTY_NOTICES.md` |
| SimPy / Salabim | 宽松（MIT/BSD 类） | 逐个核对 LICENSE，保留署名 |
| 方法论（Fix Your Timestep、MDA、Machinations） | 文章/方法论 | 可直接使用，注明出处 |

## 理由

许可传染是法律事实，不是风格偏好。清白的做法只有一条：**只拿思想，不拿表达**。

## 后果

- 本仓库实现全部自写；如需借鉴 MIT 代码，先在 `docs/GITHUB_REFERENCE_AUDIT.md` 增加一行。
- `tests/` 里不引入任何 GPL 依赖；`requirements.txt` 不新增 GPL 包。
- 复核动作：新增外部参考时同步更新审计文档，`check_doc_drift.py` 与 A39 会兜底。
