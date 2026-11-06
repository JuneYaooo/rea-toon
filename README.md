# rea-toon

**任何短剧，拆成剧本。**

## 能拆什么

- **剧本源码** `story.json`：分镜、字幕、角色表、逐句说话人、逐镜动作表情、乔装换装合并
- **深度拆解** `breakdown.html`：分场造型、戏剧节拍、逐镜看点、画面文字、机位视线、特效漫符，审完 `lock`
- **爆款报告** `report.html`：节奏曲线、爽点分布、人物戏份、套路、可复用公式
- **整部剧** `series.html`：跨集角色、人物弧光、伏笔回收、集间钩子、付费卡点
- **各种类型**：真人、AI 真人感、2D / 3D 漫剧、动态漫画、解说类，自动识别
- **换角复刻**（可选）：在锁定的剧本上换角色、换画风，见 [remake/](remake/README.md)

## 安装

```bash
pip install -e .                        # Python ≥ 3.10、ffmpeg
pip install -e ~/code/github/rea-video  # 切镜、分轨
pip install -e ./remake                 # 可选：复刻
```

密钥放 `.env`：`REA_TOON_VLM_BASE_URL` / `_API_KEY` / `_MODEL`（或 `GEMINI3_*`），抖音链接解析 `XIAOLVFANG_API_TOKEN`。

## 用法

```bash
rea-toon analyze work/a --source "<抖音分享 | 视频链接 | 本地 mp4>"   # 拆一集
rea-toon series  work/b --source ~/剧集文件夹/                        # 拆整部剧，可续跑
rea-toon lock    work/a                                             # 审完锁定
```

| 命令 | 作用 |
|---|---|
| `analyze WORK [--source] [--cuts]` | 拆一集：剧本源码 → 深度拆解 → 报告 |
| `series WORK [--source] [--title] [--only-series]` | 拆整部剧 |
| `breakdown WORK` / `report WORK` | 单独重做深度拆解 / 报告 |
| `lock WORK` | 审定拆解 |
| `refine` / `proofread` / `ingest` | 补标说话人 / 校对字幕 / 只导入视频 |

详细说明：[skills/rea-toon/SKILL.md](skills/rea-toon/SKILL.md)。
