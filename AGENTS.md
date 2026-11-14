# rea-toon：给 Agent 的说明

这个仓库是"逆向拆解一切短剧"，用于学习短剧：主包 `rea_toon` 把短剧拆成剧本源码、爆款拆解报告、剧本和分镜。

- 用户要拆解短剧（这条为什么火、套路、整部剧的结构）时，**先读 `skills/rea-toon/SKILL.md`**：
  `analyze`（剧本源码 → 深度拆解 → 爆款拆解报告，默认快拆）→ 和用户一起审 →（可选）`deepen` 补拆、`lock` 定稿；整部剧用 `series`。

## 仓库分工

- `skills/rea-toon/`：给 agent 用的 skill。`SKILL.md` 只写流程和命令，细节在 `references/`（`outputs` 产物和读法、`breakdown` 每一步和字段、
  `report`、`series`、`script`、`setup`）；只写怎么用，不放开发文档。
- `rea_toon/`：CLI 和拆解逻辑（下面）。`tests/`：测试，只在本地（.gitignore），不进仓库。
- `docs/brand/`：logo（和 rea-video 同一套造型，蓝色 `#2b6ef2`），站点顶栏里是同一个图形。

## 产物结构（`rea_toon/layout.py`）

**一个拆解目录里的路径只在 `layout.py` 里写，别的模块不拼路径。** 顶上三样东西对应三种读的人，其余按用途进子目录：

```
<work>/
  index.html   给人看的唯一入口            VIDEO.md   给模型和下游读的拆片包入口
  story.json   剧本源码，唯一手改、lock 锁它    source.mp4 原片
  video/       拆片包正文（生成的）          pages/     index.html 里显示的页面、PDF、script.md
  frames/      每镜首中尾帧                characters/ 角色图、配音样本
  data/        report.json、字幕轨、source.json、*.prev.json
  analysis/    答案缓存、等 agent 答的题、联系表     decomp/  rea-video 的输出
```

整部剧：`index.html`、`SERIES.md`（给模型读的索引：结论 + 每集一行）、`series.json`、`pages/`、`characters/`、`analysis/`，每集一个 `epNN/`（就是上面这种目录）。
集与集的关联是整部剧和一堆单集唯一的区别，改 series 时别破坏这三条：集号按片源顺序定、导入失败也留着；按顺序拆、每集带前情（`prior_of`），模型模式下遇到拆不了的集就停（`skip` 才能跳过）；每集记下带了哪几集的前情（`prior.eps`），缺了的重拆。
随集数增长的东西要有上限（`series.PRIOR_CAST` 前情带多少角色、`series.UNIFY_ALL` 一次合并看多少张脸），改 series 时按上百集想一遍。
页面在 `pages/` 里，比它引用的图和视频低一层：页面里的路径用 `page.up()` / `layout.from_page()`，不要手写 `../`。
`story.json` 里的帧路径、音轨路径相对 work 目录；`frames/`、`decomp/`、`analysis/` 的位置变了会让已有目录失效，没必要不动。

## 代码结构（拆解，`rea_toon/`）

流程按阶段排，**`pipeline.py` 是唯一串联各阶段的地方**（单集 `episode`，整部剧逐集调用它；
每个阶段读 story.json → 改 → 重算检查 → 存盘，并重出审核页和拆片包）。下面各阶段模块只改传进来的 story，不自己读写 story.json：

- `ingest.py` 导入视频：只收已经在本地的视频文件，不从链接下载
- `perceive.py` 感知：切镜/分轨（rea-video）+ 字幕 OCR + VLM 角色表和逐镜拆解（快拆 5 镜一批）
- `subtitles.py` 字幕轨（纯本地 OCR）：只认字幕行上的字（`find_row`），画面里的字不进台词；`redo subtitles` 只重读字幕轨
- `refine.py` 全剧级核对：字幕校对、逐句标说话人（先按上下文，和逐镜看图不一致的句子再看图裁决）、合并乔装/换装的同一角色
- `breakdown/` 深度拆解，一个文件一类：`scenes`（画风档案、分场造型）、`screen_text`（画面文字）、`staging`（机位视线特效）、
  `acting`（逐秒表演、身体接触、表情，deepen 补的就是这里）、`motion`（定格、动法、闪白，纯本地）、
  `beats`（戏剧节拍、逐镜看点和情绪、信息差；整部剧里带前情）
- `checks.py` 风险标签和 lint（纯代码）
- `memo.py`：慢的本地测量（画面文字 OCR、定格、画面动法）存在 `analysis/local/`，agent 模式每轮重跑时不再重算；只放只取决于原片和帧的纯函数结果
- `story.py` story.json 的读写、常量、"某人某镜穿什么"等查询、锁定状态
- `emotion.py`：情绪账（纯代码）。按逐镜的 `key.feel`（爽/憋/虐/甜/笑/悬/惊/燃）和戏剧作用数情绪节点、节点间隔、憋→爽；
  节拍 `beats` 只是模型概括的骨架，**不要拿它数爽点**
- `genre.py`：按数据判断类型（真人/AI 真人感/2D/3D/动态漫/解说）和每种类型要看的点

从 story.json 生成的东西（都不写 story.json，不会让锁失效）：

- `videomd.py`：拆片包 `VIDEO.md` + `video/`（纯代码；规范 https://github.com/videomd/videomd ，和 rea-video 同一格式）。
  每次保存整个重写；时间线一行只放戏剧作用、画面、台词（每句只记一次），"为什么这么拍"在镜头文件里，别往时间线里加长字段。
  整部剧的 `SERIES.md` 也在这里写（`write_series`）。短剧自己的内容走格式的扩展位：时间线的 `x-feel` / `x-say`、镜头文件的 `x-drama`、主题 `x-report`、卡片的 `x-format` / `x-review`。
  改了它写的字段，跑测试（装了 `videomd` 包会用官方校验器检查）。同一份内容不再另出 story.md / breakdown.md / report.md。
- `report.py`：爆款拆解报告（代码算节奏曲线/情绪账/戏份/对手戏，一次模型调用写卖点、情绪打法、套路、可复用公式）→ `data/report.json`、`pages/report.html`
- `review.py`：逐镜审核页 `pages/breakdown.html`（审拆解、lock 前单独打开）
- `script.py`：剧本（`pages/script.html/.pdf/.md`，标准短剧格式）和分镜脚本（`pages/storyboard.html/.pdf`，角色设定 + 逐镜表）+ 角色图和配音样本 `characters/`
  （`<id>_voice.mp3`：从原片人声轨里切这个角色自己说的几句，纯 ffmpeg）；除了每集两次纯文本模型调用（台词补标点、剧本润色）外都是纯代码，单集目录或整部剧目录都行
- `series.py`：整部剧（每集一个 `epNN/` 单集目录，可续跑；跨集合并角色、整部剧的类型只记在 series.json，不改各集 story.json）→ `pages/series.html`
- `page.py`：页面共用样式、SVG 图表、折叠的依据区（`fold`）、点镜号播放原片的播放器（`player`）和站点 `index.html`
  （`page.site`：一条栏选哪一集、看三个方向之一：学习分析 / 剧本 / 分镜拆解；站点里不放审核入口，内容在下面原地换，页面之间不互相跳）。
  页面排版原则：结论在页面上，依据折叠在底部，同一件事只出现一次

其他：

- `cli.py` 命令：analyze / series / script / deepen / lock / redo <步骤>（`redo pages` 只重出页面和拆片包，不调模型）/ ingest
- `media.py` 分析用的 ffmpeg/OpenCV 和联系表；所有拆解提示词在 `prompts.py`
- `vlm.py`：谁来看图作答。默认 agent（题写进 `<work>/analysis/agent/`，命令以退出码 3 停下，你看图写 `<key>.answer.json` 后原样重跑，
  步骤见 `skills/rea-toon/SKILL.md`）；用户配了 `REA_TOON_VLM_*` 或加 `--backend api` 才调接口模型。
  未答的题用 `NeedAgent`（BaseException）中断，各阶段的 `except Exception` 不要改成会吞掉它的写法；
  互相独立的题用 `vlm.Pool` 一轮出齐（它把每一题都出完才停），不要一题一题顺序问，那样 agent 每答一题就要重跑一轮

## 开发约定

- 支持 Python 3.10+：别用只有 3.12 才认的 f-string 写法（表达式里的反斜杠、和外层相同的引号），本地测试 `tests/test_py310.py` 会查。
- 安装：`pip install -e ".[dev]"`；改完在仓库根目录跑 `python -m pytest -q`（本地有 `tests/` 时）。
- 给模型的图是一集里最大的 token 开销（联系表、逐镜拼图、认脸参考图）：加图、放大图之前先算像素；长剧的省法和实测见 `skills/rea-toon/references/series.md`。
- 改拆解提示词会让已有工作目录的答案缓存失效（重跑要重新付费，或由 agent 重新作答），没必要不改措辞。
- 不要提交密钥、服务器密码和 `work/`。
- 协议：PolyForm Noncommercial 1.0.0（不允许商用，`LICENSE`）。README 有中英两版（`README.md` 英文、`i18n/README_zh.md` 中文，结构参照 rea-video），改一版要同步另一版；只写结合 agent 的用法，不写手动安装和命令表；片源只说本地视频。
