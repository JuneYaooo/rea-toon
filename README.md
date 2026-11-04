# rea-toon — 换角色 / 换画风复刻 AI 短剧

> 拿一条爆款 AI 短剧或短视频，**剧情、分镜、运镜、情绪、节奏、原声全部保留**，只把你指定的元素换掉：角色（比如把新郎新娘换成孙悟空和白晶晶）、
> 服装、带货的产品、场景和道具，或者把整部片换个画风、换个 IP。

思路来自两个项目：

- [REA / rea-video](https://github.com/JuneYaooo/rea-video)：先把视频**逆向成可复查的"源码"**（镜头、字幕、声音、每镜怎么做的），结论附证据。
- [hypit](https://github.com/hypit-ai/hypit)：源码里每个角色是一个**可替换的组件**，形成依赖图（角色设定图 → 镜头关键帧 → 视频片段 → 成片）；换一个组件，只重做依赖它的部分。

模型只用目前接得住短剧剧情和情绪的组合：**gpt-image-2**（设定图、逐镜换角色的关键帧）+ **MiniMax H3**（AutoDL）或 **Seedance 2.0**（视频）。

## 流程

```
 1 拆剧本    参考视频 ─► analyze：切镜、字幕 OCR、人声/配乐分轨、VLM 角色表 + 逐镜拆解 ─► story.json / story.md
 2 定范围    和用户确认换谁（主角？配角？群众？）─► remake.yaml
 3 看计划    plan：重做哪些镜头、每镜视频模式、调用次数、哪些角色保持原样、试跑镜头（不花钱）
 4 关键帧    角色设定图 ─► 原首/尾帧逐张换人（gpt-image-2）─► 自动质检 ─► 人工审 review.jpg
 5 试跑      run --pilot：对白 / 群众宽镜头 / 边说边大运镜 各一镜 ─► 出视频、拼片、自查 ─► 有问题先改配置
 6 全片      每镜三选一：ref_video（原镜头当动作参考） / lipsync（原人声驱动） / first_last（首尾帧）
             生成后自动检查：构图跑偏、原片的人漏回来 → 降级；口型和原声对不上 → 换种子，记下时差
 7 拼成片    按磁盘上的片段拼回（不依赖运行状态）、画面按时差平移、逐段核对帧数 ─► 重烧字幕 ─► 铺原音轨
 8 自查      run 结束自动 check：时间线、漏换、口型分数 + 中段抽帧、字幕抽帧 ─► 人工看完再交付
```

保真靠这几件事：

1. **只重做受影响的镜头**：没有被换角色的镜头直接用原片，零成本、完全保真。
2. **首尾帧双约束**：每个重做的镜头都从原片的首帧和尾帧改出来（只换人，构图/机位/光线/表情不动）。
3. **原镜头当动作参考**（自部署 H3 Ref2VA）：把原镜头（人脸和字幕打码）交给 H3，运镜、走位、动作节奏一一对应，
   新关键帧和设定图定长相。打码是必须的：不打码 H3 会把原演员的脸带回来；打码后衣着体型仍可能被照抄，
   所以生成后 VLM 对比中段帧和新首帧，原片的人漏回来就自动降级重做。
4. **口型事后对齐**：H3 会自己给台词配音，嘴跟它自己的声音走。生成后比较它的音轨和原人声，对不上就换种子重试，
   对得上但有时差就在拼接时平移画面，让嘴落在原声上。
5. **表演拆解**：每个重做镜头先从原片抽 6 帧做逐秒表演拆解（眉眼、嘴、头手怎么动，以及禁止项），写进视频提示词。
6. **关键帧自动质检**：每张换人的关键帧都和原帧比一遍，不通过带着问题重画；原帧里小到看不清的人直接用原帧。
7. **字幕重烧**：OCR 出带时间的字幕轨；关键帧去字幕；给了音频的模式提示词不写台词原文（否则模型会把台词画成字幕），
   成片再兜底擦掉模型画的字幕，按原位置、原时间重烧。
8. **按输入 hash 缓存**：改一个角色，只重做它的设定图和它出现的镜头；失败镜头重跑同一命令续跑，不重复计费。
9. **先试跑再全片**：`--pilot` 用 3 个最容易出问题的镜头验证模型的实际行为，十几分钟发现问题，不用等全片跑完。
10. **交付前自查**：`run` 结束自动 `check`，查时间线、漏换镜头、口型分数，出中段抽帧和字幕抽帧给人看。

## 安装

```bash
pip install -e .                       # 需要 Python ≥ 3.10、ffmpeg
pip install -e ~/code/github/rea-video # 切镜和人声/配乐分轨用 rea-video
```

密钥从 `--env-file`、`./.env` 或 `~/code/github/capsule-cinema/.env` 读取（复用 capsule-cinema 的配置）：

| 用途 | 环境变量 |
|---|---|
| gpt-image-2（默认 cyber = Chybenzun 渠道） | `CHYBENZUN_GPT_IMAGE2_BASE_URL` / `_API_KEY`（或 `KRILL_*`、`JULING_*`） |
| MiniMax H3（AutoDL.Art） | `AUTODL_ART_TOKEN` |
| Seedance 2.0（火山方舟） | `ARK_API_KEY`，可选 `ARK_SEEDANCE_MODEL` |
| VLM 拆剧本 | `REA_TOON_VLM_BASE_URL` / `_API_KEY` / `_MODEL`，或 capsule-cinema 的 `GEMINI3_*` |
| 抖音分享链接解析 | `XIAOLVFANG_API_TOKEN` |
| VLM 备用模型（主模型都不可用时，在第一个 VLM 端点上试这些模型名） | `REA_TOON_VLM_FALLBACK_MODELS`，逗号分隔 |

## 快速开始：把《14亿人都来参加婚礼》换成孙悟空和白晶晶

```bash
# 1. 导入 + 拆剧本（只花 VLM 的钱）
rea-toon analyze work/wedding --source "https://v.douyin.com/QcaNeGuOM-A/"
cat work/wedding/story.md            # 角色表 + 逐镜表

# 2. 写换角色配置（或 rea-toon init work/wedding my_remake 生成模板）
cat examples/wukong_wedding/remake.yaml

# 3. 看计划：重做哪些镜头、多少次调用
rea-toon plan work/wedding examples/wukong_wedding/remake.yaml

# 4. 先出设定图和关键帧审一遍
rea-toon run work/wedding examples/wukong_wedding/remake.yaml --until frames
open work/wedding/remakes/wukong_wedding/review.jpg

# 5. 试跑 3 个代表镜头，看自查结果
rea-toon run work/wedding examples/wukong_wedding/remake.yaml --pilot

# 6. 出全片 + 拼成片 + 自动自查（看 check.md、check_frames.jpg、check_subs.jpg）
rea-toon run work/wedding examples/wukong_wedding/remake.yaml
open work/wedding/remakes/wukong_wedding/compare.mp4
```

全员换成西游记（各路神仙妖怪来参加孙悟空和白骨精的婚礼）：见 [examples/xiyou_wedding/remake.yaml](examples/xiyou_wedding/remake.yaml)。
12 个角色逐个对应（收礼大爷→太白金星、停车指挥员→猪八戒、服务员→哪吒、司仪→唐僧、吃席大妈→王母娘娘……），群众写成 `crowd`，会画群像设定图，宽镜头里成片的人一起换。

`remake.yaml`：

```yaml
name: wukong_wedding
style:                 # 留空 = 保持原画风；写 "3D 皮克斯动画电影风格" 就整片换画风（所有镜头重做）
video_engine: h3local  # h3local（自部署 H3）| h3（托管 API）| seedance
frames: first_last
cast:
  groom:               # story.json 里的角色 id
    name: 孙悟空
    look: 写实电影质感的齐天大圣：猴脸、金棕色猴毛、火眼金睛、金箍、凤翅紫金冠翎羽……
    costume: keep      # 穿原角色的衣服（黑西装 + "新郎"胸花）；也可以写新服装
    image: ./wukong.png  # 可选：你自己的角色参考图
  bride:
    name: 白晶晶
    look: 银白长发盘成新娘发髻、肤白如瓷、眉心花钿……原创形象
```

换产品、换场景、一镜里多个状态（换装）时还会用到：

```yaml
style: >-              # 换场景：写"原来的 A 换成 B"的对应关系
  厨房台面换成露营木桌，背景换成帐篷和树林；香蕉换成草莓
anchors:               # 长镜头中间额外锁几张关键帧（镜头号: [镜头内秒数]）
  2: [1.0]
frame_notes:           # 给单张关键帧的要求（first / last / a1 …）
  2: {a1: "穿第二套：焦糖色麂皮西装 + 米白针织内搭"}
cast:
  juicer:              # 换产品：产品不在角色表里，写 kind: object
    kind: object
    original: 灰紫色便携榨汁杯，透明杯身，底座数显屏
    name: 樱花粉便携榨汁杯
    look: 原创工业设计，无品牌字样……
```

常用参数：

| 命令 | 作用 |
|---|---|
| `analyze WORK [--source 视频] [--cuts 6.65,9.92]` | 导入并拆剧本 → `story.json` / `story.md`；`--cuts` 在溶解、穿门、遮挡转场处强制切镜（单镜需 ≤15s） |
| `init WORK NAME` | 按角色表生成 `remake.yaml` 模板 |
| `plan WORK SPEC` | 列出重做镜头、帧编辑方式、视频模式和调用次数 |
| `run WORK SPEC [--until cast\|frames] [--shots 1,28-30]` | 生成；可先停在设定图或关键帧；`--shots` 只处理这几镜，成片仍是整部片 |
| `run WORK SPEC --pilot` | 试跑 plan 选出的 3 个代表镜头，出视频、拼片、自查 |
| `run ... --redo 28 --force frames,clips` | 强制重做指定镜头的指定阶段 |
| `assemble WORK SPEC` | 只重新拼成片 |
| `check WORK SPEC` | 交付前自查：时间线、漏换镜头、口型同步分数、中段抽帧、字幕抽帧 → `check.md` |

## 产物

```
work/wedding/
  source.mp4  source.json
  decomp/               rea-video 拆解（切镜、分轨 stems/vocals.wav music.wav、报告）
  subtitles.json/.srt   带时间的字幕轨
  frames/               每镜原始首/中/尾帧
  story.json  story.md  剧本源码（可手改：角色归属、逐镜提示词）
  remakes/wukong_wedding/
    plan.json  results.json
    cast/       角色设定图
    frames/     每镜新首帧/尾帧（*.meta.json 记录提示词和输入 hash）
    clips/      每镜生成的视频
    final.mp4   compare.mp4（左原片 右复刻）  review.jpg（原首帧|新首帧|原尾帧|新尾帧）
```

## 局限

- 声音目前只支持保留原声；换角色的同时换音色（TTS/克隆）还没做。
- 口型是事后对齐（整段平移 + 换种子），不是逐字驱动；大运镜的对白镜头只能走首尾帧，口型常对不上。
- 参考原镜头生成时，原片的人偶尔会在片中途"长回来"（尤其群众和远处小人），要靠 `check_frames.jpg` 人工发现，
  列进 `no_motion_ref` 重做。
- 角色识别靠 VLM：远景小人、背影偶尔漏标，要在 `story.md` 里核一遍，必要时手改 `story.json`。
- 极短镜头（< 2s）只能生成更长再截取，快切镜头的动作会比原片少。

## 效果展示

### 《14亿人都来参加婚礼》→ 西游记

参考片：抖音《14亿人都来参加婚礼会怎样？》（115.5s，36 镜，对白 + 歌曲）。

| 版本 | 换了什么 | 配置 |
|---|---|---|
| 换主角 | 新郎 → 孙悟空，新娘 → 白晶晶，其余不变（9 镜重做、27 镜复用原片） | [wukong_wedding](examples/wukong_wedding/remake.yaml) |
| 全员西游记 | 12 个角色 + 群众全部换成西游记人物：司仪 → 唐僧、收礼大爷 → 太白金星、服务员 → 哪吒、吃席群众 → 各路神仙妖怪…… | [xiyou_wedding_h3local](examples/xiyou_wedding_h3local/remake.yaml) |

台词、歌曲、音效用原声，字幕按原时间重烧，对白镜头口型对齐原声。
