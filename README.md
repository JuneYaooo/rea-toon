# rea-toon：逆向拆解一切短剧

> 真人短剧、AI 短剧、漫剧、动态漫画、解说类，单集或一整部剧：拆成**可复查的剧本源码**，
> 再告诉你**它为什么好看、套路是什么、怎么拿来用**。每个结论都附证据（镜号、秒数、台词、截图）。

思路来自 [REA / rea-video](https://github.com/JuneYaooo/rea-video)：先把视频逆向成可复查的"源码"，结论附证据，审过再用。

拆解有三层，都是自动的，只调 VLM（看图、读文本的模型），不调图片或视频生成模型：

| 层 | 产物 | 给谁看 |
|---|---|---|
| **剧本源码** | `story.json`：切镜、字幕（OCR + 校对错字）、角色表、逐镜拆解（景别、运镜、动作、表情）、逐秒表演、**逐句说话人**（画内/画外/内心独白/旁白）、乔装换装的同一人合并 | 下游程序、要改数据的人 |
| **深度拆解** | `breakdown.html`：画风档案、分场与造型（每场谁穿什么、道具在哪只手）、戏剧节拍、逐镜看点（剧情功能、必须保住的表情动作）、画面文字、机位视线、身体接触和音效点、定格、画面动法、特效漫符、表情清单、一致性检查 | 审核的人；导演、剪辑 |
| **爆款拆解** | `report.html`：类型、一句话卖点、题材/受众/爽感标签、开头钩子、**节奏曲线**、爽点分布和空白段、结构、套路、人设、对手戏、为什么留得住人、短板、**可复用公式**、改编方向 | 编剧、制片、投放 |

整部剧再加一层 `series.html`：跨集合并角色、人物弧光、伏笔和回收、每集结尾钩子怎么被下一集接住、付费卡点、分阶段打法。

## 安装

```bash
pip install -e .                        # 拆解：Python ≥ 3.10、ffmpeg
pip install -e ~/code/github/rea-video  # 切镜和人声/配乐分轨
pip install -e ./remake                 # 可选：换角色复刻（rea-toon-remake）
```

密钥从 `--env-file`、`./.env` 或 `~/code/github/capsule-cinema/.env` 读取：

| 用途 | 环境变量 |
|---|---|
| VLM | `REA_TOON_VLM_BASE_URL` / `_API_KEY` / `_MODEL`，或 capsule-cinema 的 `GEMINI3_*`；备用模型名 `REA_TOON_VLM_FALLBACK_MODELS`（逗号分隔） |
| 抖音分享链接解析 | `XIAOLVFANG_API_TOKEN` |

## 拆一集

```bash
rea-toon analyze work/qunbai --source "https://v.douyin.com/xxxx/"   # 或视频直链、本地 mp4
open work/qunbai/breakdown.html    # 审：角色合并、谁说哪句、分场造型、看点；有问题直接改 story.json
open work/qunbai/report.html       # 爆款拆解报告
rea-toon lock work/qunbai          # 审完锁定（要复刻时必做）
rea-toon report work/qunbai        # 改过拆解后刷新报告
```

报告里的数据是代码按拆解算的（节奏曲线、第一个爽点在几秒、连续多久没有爽点、每个角色出镜多少秒、
谁和谁的对手戏最多、每分钟几刀、台词每秒几个字）；卖点、套路、公式是模型读了这些数据和节拍以后写的，
提示词要求每条都落到镜号、秒数或台词上。`--no-vlm` 只出数据部分。

**类型**按数据自动判断，每种类型的报告看不同的东西：

| 类型 | 怎么认 | 报告重点看 |
|---|---|---|
| 真人短剧 `live_action` | 画风是实拍 | 表演和台词冲突、打脸节奏、场景和道具成本 |
| AI 真人感短剧 `ai_live` | 画风是真人感 AI | 人设怎么一眼立住、AI 才拍得出的奇观、怎么靠剪辑躲开口型 |
| 2D 漫剧 `anime_2d` / 3D 动画 `anime_3d` | 画风 | 分格、漫符、特效、变脸；动作奇观和运镜 |
| 动态漫画 `motion_comic` | 一半以上时长是静图推拉 | 一张图撑多久、气泡和旁白承担多少叙事 |
| 解说类 `commentary` | 六成以上台词是旁白 | 解说词结构（钩子句、悬念句、金句）、画面和解说的配合、语速 |

判断错了在 `story.json` 里写 `"format": "commentary"` 这类 id 再 `rea-toon report`。

## 拆整部剧

```bash
rea-toon series work/裙摆 --title "裙摆藏不住" --source ~/Downloads/裙摆藏不住/   # 文件夹里的各集按文件名数字排序
rea-toon series work/裙摆                       # 断了就重跑：拆完的集跳过
rea-toon series work/裙摆 --source 第11集.mp4   # 追加新集
open work/裙摆/series.html
```

每集是普通的单集目录（`ep01/`、`ep02/`…，各自有 breakdown.html 和 report.html，可以单独审、单独 lock）。
每集的角色 id 是各自起的，`series` 用每集主要角色的头像表 + 身份 + 名字线索把同一个人合并起来，
映射记在 `series.json`，不改各集的 story.json；合并错了手改后加 `"cast_fixed": true`。

## 命令

| 命令 | 作用 |
|---|---|
| `analyze WORK [--source 视频] [--cuts 6.65,9.92]` | 拆一集：剧本源码 → 深度拆解 → 爆款拆解；`--cuts` 在溶解、穿门、遮挡转场处强制切镜 |
| `breakdown WORK` | 只重做深度拆解（改过 story.json、或旧目录补做） |
| `report WORK [--no-vlm]` | 只重出爆款拆解报告 |
| `series WORK [--source …] [--title …] [--only-series]` | 整部剧：逐集拆解（可续跑）+ 跨集拆解 |
| `lock WORK [--force]` | 审定拆解；有 error 拒绝。之后改 story.json 要重新 lock |
| `refine WORK` / `proofread WORK` | 给旧目录补做逐句说话人、同一人合并 / 字幕错字校对 |
| `ingest SOURCE WORK` | 只导入视频 |

## 产物

```
work/qunbai/
  source.mp4  source.json
  decomp/               rea-video 拆解（切镜、人声/配乐分轨）
  subtitles.json/.srt   带时间的字幕轨
  frames/               每镜首/中/尾帧
  story.json  story.md  剧本源码（可手改）
  breakdown.html/.md    深度拆解审核页
  report.html/.md/.json 爆款拆解报告
work/裙摆/
  series.json  series.html/.md
  ep01/ ep02/ …         单集目录
```

## 下游：换角色复刻

`remake/` 是一个单独的包 `rea-toon-remake`：在锁定的拆解上，把角色换成孙悟空、把产品换掉、或整部片换画风，
剧情、分镜、运镜、情绪、原声都保留。它只读锁定的 `story.json`，自己不分析原片。见 [remake/README.md](remake/README.md)。

## 局限

- 字幕靠 OCR：没烧字幕的片子台词会是空的，说话人、台词密度、对手戏都不可信。
- 逐句说话人和角色合并靠 VLM 读剧情：人名卡、台词能交代身份时很准；纯靠长相的乔装可能认不出，要人工看一眼角色表。
- 情绪强度按镜头的戏剧作用加权，说明"这部剧把力气花在哪"，不是观众数据（完播率、互动）。
- 跨集合并每集只看一张脸：只有背影、远景的角色可能合并不上。
