# artifacts/ —— 一次运行一个包

这个目录只放**跑出来的东西**，不放代码。每个子目录对应一次完整运行，自带指纹，可以单独拷给别人看，也方便回答"这批数字是哪一次跑出来的"。

## 怎么产生

```bash
cd C:/Users/OYC/Desktop/blender/dc_project

# 跑场景链 + 一致性检查 + 收产物（最常用）
python scripts/run_bundle.py --scope scenarios --label m1_process

# 只跑数据链，快
python scripts/run_bundle.py --scope data --label quick --no-post

# 连 blend/glb/mp4/渲染图一起收（体积大）
python scripts/run_bundle.py --scope full --label demo --with-media
```

也可以用驾驶舱：`启动驾驶舱.bat` → 工具箱 → **生成产物包**；如果要先按自己的功能区生成变体，走左侧 **「功能区」页** → 勾选 → *生成并校验* → *用变体跑一遍*（产物同样落进 `artifacts/`）。

## 包内结构

| 路径 | 内容 |
|---|---|
| `manifest.json` | 指纹：时间、scope、git HEAD、Python 版本、种子、计数、每个文件的 sha256 |
| `out/` | 报告类产物（`.json` / `.jsonl` / `.md` / `.csv`），默认不含 `.blend`/`.glb`/`.mp4` |
| `runs/<场景>/<run_id>/` | **本次新增**的运行目录（历史 run 不重复收） |
| `logs/` | 每个步骤的完整 stdout/stderr，失败时先看这里 |
| `README.txt` | 一句话摘要 + 复现命令 + 各检查项是否通过 |

## 三条纪律

1. **不要再往包里手写文件**。包里的一切都由脚本生成，手改会破坏 manifest 的 sha256。
2. **`artifacts/` 不进 git**（见 `.gitignore`）。要交付就把整个包目录压成 zip 发出去。
3. **判读顺序**：先看 `README.txt` 的检查摘要，再看 `manifest.json` 的 `facts`，最后才看 `out/*.json` 的具体数字。

## 和 `out/`、`runs/` 的关系

`out/` 与 `runs/` 是**工作区**：每次跑都会被覆盖/追加，只反映"最近一次"。
`artifacts/<包>/` 是**快照**：跑完就冻结，不随后续运行变化。

所以：日常开发看 `out/`，答辩/交付看 `artifacts/`。
