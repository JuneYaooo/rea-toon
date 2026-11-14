# 产物：一个拆解目录里有什么、怎么读

拆一集得到一个目录。顶上只有三样东西，对应三种读法；其余按用途放在子目录里。

```
work/<名字>/
  index.html     给人看：唯一要打开的页面（学习分析 / 剧本 / 分镜拆解）
  VIDEO.md       给模型和下游读：拆片包的入口（卡片 + 概览 + 地图）
  story.json     剧本源码：唯一手改的文件，lock 锁的就是它
  source.mp4     原片
  video/         拆片包正文：scenes.md timeline.md content.md cast.md x-report.md shots/NN.md transcript.vtt
  pages/         index.html 里显示的页面：report.html script.html storyboard.html breakdown.html；script.md、*.pdf
  frames/        每镜的首 / 中 / 尾帧（story.json 里的路径相对本目录）
  characters/    每个角色的头像、造型图、配音样本；分镜缩略图在 characters/shots/
  data/          程序数据：source.json report.json subtitles.json / .srt，*.prev.json（重做前的备份）
  analysis/      中间产物：模型答案缓存 vlm_cache/、等 agent 作答的题 agent/、联系表、看图用的拼图
  decomp/        rea-video 量出来的：切点、人声 / 配乐分轨
```

整部剧：

```
work/<剧名>/
  index.html     给人看的入口：选哪一集（或全剧）、看什么
  SERIES.md      给模型读的入口：整部剧的结论 + 每集一行的地图
  series.json    跨集的源码：集列表、整部剧的角色表、类型、跨集解读
  pages/         series.html / .md、整部剧的 script.* 和 storyboard.*
  characters/  analysis/
  ep01/ ep02/ …  每集一个上面那样的目录，各有自己的 VIDEO.md
```

## 你（agent）怎么读：拆片包

`VIDEO.md` 是开放格式（规范：<https://github.com/videomd/videomd>，rea-video 写的也是它），逐层读，够用就停：

1. `VIDEO.md`（约 2k token）：一句话卖点、讲什么、类型、戏剧骨架、审核状态、要处理的问题、地图。多数问题到这里能答。
   地图一行一个文件：`路径 | 约 token | 里面有什么 · 什么时候读`。
2. `video/timeline.md`：一行一个镜头。`story` 戏剧作用（钩子、反转、爽点…，带「必保」的是关键镜）、`seen` 画面、`camera` 景别运镜、
   `x-feel` 观众情绪、`x-say` 这一镜开始说的台词和说话人、`flag` 要人确认。按镜头回答问题时读它；
   某一镜为什么这么拍（剧情功能、必须保住什么）在它的镜头文件里。
3. 按地图打开一个主题：`video/x-report.md`（为什么火：情绪账、套路、公式）、`video/cast.md`（谁是谁、每场穿什么、画风）、
   `video/content.md`（剧情、节拍、信息差）、`video/scenes.md`（分几场）、`video/transcript.vtt`（带说话人和时间的台词）。
4. 核实某一镜再开 `video/shots/<镜号>.md` 和它 `frames` 里的图。

不要读 `story.json`（几万 token）和 `pages/` 里的 html（给人看的）；`story.json` 只在要改拆解时按字段去改。

整部剧从根目录的 `SERIES.md` 读起：一句话卖点、主冲突、情绪打法、主要角色，然后是地图——`pages/series.md`（跨集拆解全文）、
`pages/script.md`（全剧剧本）、每集一行的 `epNN/VIDEO.md`（时长、情绪节点数、这一集的卖点）。上百集时这一页也只有几千 token，
按它挑要读的集。`SERIES.md` 是 rea-toon 自己的索引，不属于 VIDEO.md 规范；每集的卡片里 `related` 写着它是哪部剧的第几集、接哪一集。
「要处理的问题」里标着"要细拆时"的那条（快拆没做逐秒表演）只学剧不用管。

卡片里 rea-toon 自己加的字段：`x-format`（类型 id，见 [report.md](report.md)）、`x-review`（`unlocked` 待审 / `locked` 已锁定 / `stale` 锁定后改过）。
`readers` 里有 `human` 表示有人审过并 lock。

## 哪些能改

- 只改 `story.json`（整部剧的跨集部分改 `series.json`）。改完 `rea-toon redo pages <目录>` 重出页面和拆片包；要定稿再 `lock`。
- `VIDEO.md`、`video/`、`pages/` 都是生成的，每次保存整个重写，手改会丢。
- `rea-toon redo pages` 不调模型，不动 story.json / report.json，单集目录或整部剧目录都行。
- 校验拆片包：`pip install videomd && videomd check work/<名字>`。
