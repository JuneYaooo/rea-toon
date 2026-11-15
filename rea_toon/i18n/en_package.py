"""English for the package (VIDEO.md, video/*.md, SERIES.md: videomd.py), the risk tags and lint (checks.py) and the
display helpers of story.py."""

T = {
    # ---- shared words: roles, priorities, feels, line modes, punctuation ----
    "钩子": "Hook",
    "铺垫": "Setup",
    "冲突": "Conflict",
    "反转": "Reversal",
    "揭露": "Reveal",
    "高潮": "Climax",
    "爽点": "Payoff",
    "结尾钩子": "Cliffhanger",
    "反应": "Reaction",
    "转场": "Transition",
    "普通": "Normal",
    "必保": "Must-keep",
    "重要": "High",
    "爽": "Payoff",
    "憋": "Build-up",
    "虐": "Heartbreak",
    "甜": "Sweet",
    "笑": "Funny",
    "悬": "Suspense",
    "惊": "Shock",
    "燃": "Hype",
    "画外": "off-screen",
    "内心独白": "inner voice",
    "旁白": "narration",
    "、": ", ",
    "，": ", ",
    "；": "; ",
    "：": ": ",
    "。": ".",
    "（{x}）": " ({x})",
    "（{feel}）": " ({feel})",

    # ---- videomd.py: review state, transitions, overlays ----
    "已锁定（审过）": "locked (reviewed)",
    "锁定后改过，要重新 lock": "edited after lock: lock again",
    "待审核（模型拆的，还没人审）": "to review (broken down by the model, not yet reviewed by a person)",
    "闪白": "white flash",
    "黑场/白场过渡": "fade through black/white",
    "叠化": "dissolve",
    "甩镜": "whip pan",
    "匹配剪辑": "match cut",
    "字幕": "subtitles",
    "人名卡": "name card",
    "花字": "styled text",
    "AI 标识": "AI label",
    "水印": "watermark",
    "对话气泡": "speech bubble",

    # ---- videomd.py: timeline ----
    "说话人待确认": "speaker to confirm",
    "有要处理的问题": "has an issue to handle",
    "（必保）": " (must-keep)",
    "{who}{how}：{line}": "{who}{how}: {line}",
    "（说话人待确认）": " (speaker to confirm)",
    "每行一个镜头：`#镜号 起-止(秒) | 键 值 | …`，没内容的键不写。":
        "One line per shot: `#shot start-end(s) | key value | …`; keys with nothing to say are left out.",
    "`story` 这一镜的戏剧作用（钩子、反转、爽点…，带「必保」的是全片最要紧的一两成；普通镜不写）· `seen` 画面里发生的事 · "
    "`camera` 景别和运镜 · `transition` 和上一镜怎么接（硬切不写）· `overlay` 花字、人名卡等叠加层（字幕不列）· `flag` 要人确认的地方 · "
    "`x-feel` 观众此刻的情绪（爽 / 憋 / 虐 / 甜 / 笑 / 悬 / 惊 / 燃）· `x-say` 这一镜开始说的台词（说话人 id：台词；画外、内心独白、旁白会标出；跨到下一镜的一句只记在开始的那一镜）":
        "`story` the shot's job in the drama (Hook, Reversal, Payoff…; \"must-keep\" marks the most important 10-20% of the "
        "episode; normal shots are left blank) · `seen` what happens on screen · `camera` shot size and camera move · "
        "`transition` how it joins the shot before (a plain cut is not written) · `overlay` styled text, name cards and other "
        "overlays (subtitles not listed) · `flag` what a person should confirm · `x-feel` what the audience feels here "
        "(爽 Payoff / 憋 Build-up / 虐 Heartbreak / 甜 Sweet / 笑 Funny / 悬 Suspense / 惊 Shock / 燃 Hype) · `x-say` the lines "
        "that start in this shot (speaker id: line; off-screen, inner voice and narration are marked; a line running into "
        "the next shot is only written where it starts)",
    "某一镜为什么这么拍（剧情功能、必须保住什么）和全部细节在 `{dir}/shots/镜号.md`（如 `{dir}/shots/07.md`）；角色 id 对应谁，看 `{dir}/cast.md`。":
        "Why a shot is shot the way it is (its job in the story, what must be kept) and every detail are in "
        "`{dir}/shots/<shot>.md` (e.g. `{dir}/shots/07.md`); who a character id is: see `{dir}/cast.md`.",

    # ---- videomd.py: shot files ----
    "# 镜 {i} · {start:.2f}–{end:.2f}s": "# Shot {i} · {start:.2f}–{end:.2f}s",
    "## 看到的": "## What is seen",
    "- 场景：": "- Scene: ",
    "- 景别 / 运镜：{size} / {move}": "- Shot size / camera move: {size} / {move}",
    "- 发生了什么：{what}": "- What happens: {what}",
    "- 人物 `{id}`（{where}）：{action}": "- Character `{id}` ({where}): {action}",
    "；表情：{x}": "; expression: {x}",
    "；造型：{x}": "; look: {x}",
    "- 台词 {line}": "- Line {line}",
    "画面文字": "On-screen text",
    "机位和视线": "Camera angle and eyelines",
    "画面": "Picture",
    "- {name}：{text}": "- {name}: {text}",
    "- 身体接触 {t:.2f}s：{action}": "- Physical contact {t:.2f}s: {action}",
    "（音轨上有音效）": " (sound effect on the audio track)",
    "- 定格：": "- Freeze frame: ",
    "## 戏剧作用（拆解的判断）": "## Job in the drama (the breakdown's reading)",
    "- 剧情功能：{x}": "- Story function: {x}",
    "- 转场 / 特效：{x}": "- Transition / effects: {x}",
    "必须保住：{x}": "Must keep: {x}",
    "要确认：{x}": "To confirm: {x}",
    "## 这一镜要留意": "## Watch out in this shot",

    # ---- videomd.py: content.md ----
    "# 内容与结构": "# Content and structure",
    "## 前情": "## Previously",
    "## 戏剧节拍": "## Dramatic beats",
    "模型概括的骨架，一集五到八条；数情绪节点看 `x-feel`（逐镜），不看这里。":
        "The skeleton as the model summed it up, five to eight per episode; count emotional beats from `x-feel` (per shot), not here.",
    "- **{role}**{feel} 镜 {shots}：{what}": "- **{role}**{feel} shots {shots}: {what}",
    "；必须保住：{x}": "; must keep: {x}",
    "；兑现：{x}": "; pays off: {x}",
    "## 信息差": "## Information gaps",

    # ---- videomd.py: cast.md ----
    "# 角色、造型与画风": "# Characters, looks and art style",
    "## 画风档案": "## Style bible",
    "- 画风：{x}": "- Art style: {x}",
    "- {k}：{v}": "- {k}: {v}",
    "## 角色": "## Characters",
    "- `{id}`{name} {role} · {importance} · 镜 {shots}：{appearance}": "- `{id}`{name} {role} · {importance} · shots {shots}: {appearance}",
    "  - 表情：": "  - Expressions: ",
    "{name}（镜 {shots}）": "{name} (shots {shots})",
    "  - 另一套造型（镜 {shots}，原先认成 `{was}`）：{appearance}": "  - Another look (shots {shots}, first read as `{was}`): {appearance}",
    "## 分场造型": "## Looks per scene",
    "每个人在这一场穿什么、拿什么；场内换了造型的记在「镜 N 起」。":
        "What each person wears and carries in the scene; a change of look within a scene is under \"from shot N\".",
    "### {id} 镜 {a}–{b}：{location}（{time}）{day}": "### {id} shots {a}–{b}: {location} ({time}){day}",
    "- 镜 {shot} 起 `{who}`：{look}": "- From shot {shot} `{who}`: {look}",
    "{location}（{time}）{day}": "{location} ({time}){day}",

    # ---- videomd.py: scenes.md, timeline.md ----
    "# 场景": "# Scenes",
    "每行一场：`#S场号 起-止(秒) | shots 首镜-末镜 | seen 地点和时间 | x-cast 在场的角色 id`。":
        "One line per scene: `#S<scene> start-end(s) | shots first-last | seen place and time | x-cast ids of the characters present`.",
    "# 时间线": "# Timeline",

    # ---- videomd.py: VIDEO.md card and overview ----
    "《{title}》第 {ep} 集。": "{title}, episode {ep}. ",
    "{kind}，{secs:.0f} 秒 {shots} 镜。": " {kind}, {secs:.0f} s, {shots} shots.",
    "**一句话卖点**：{x}": "**Logline**: {x}",
    "**讲什么**：{x}": "**What it is about**: {x}",
    "**类型**：{kind}（{why}）": "**Type**: {kind} ({why})",
    "**戏剧骨架**：": "**Dramatic spine**: ",
    "**情绪**：{peaks} 个情绪节点，第一个在 {first}，有结尾钩子（情绪账见 `{dir}/x-report.md`）":
        "**Emotion**: {peaks} emotional beats, the first at {first}, ends on a cliffhanger (emotion ledger in `{dir}/x-report.md`)",
    "**情绪**：{peaks} 个情绪节点，第一个在 {first}，没有结尾钩子（情绪账见 `{dir}/x-report.md`）":
        "**Emotion**: {peaks} emotional beats, the first at {first}, no cliffhanger (emotion ledger in `{dir}/x-report.md`)",
    "**审核状态**：{x}": "**Review state**: {x}",
    "**还没拆**：只切了镜头、读了字幕，没有角色和逐镜拆解（`rea-toon analyze` 接着做）。":
        "**Not broken down yet**: shots cut and subtitles read only, no cast or per-shot breakdown (`rea-toon analyze` goes on).",
    "**还没做深度拆解**：没有分场造型、戏剧节拍和逐镜看点（`rea-toon redo breakdown`）。":
        "**No deep breakdown yet**: no looks per scene, dramatic beats or per-shot highlights (`rea-toon redo breakdown`).",
    "## 要处理的问题": "## Issues to handle",
    "- 必须修：{where}{msg}": "- Must fix: {where}{msg}",
    "- 要确认：{where}{msg}": "- To confirm: {where}{msg}",
    "镜 {shot}，": "shot {shot}, ",
    "- 没有": "- None",
    "- 另有 {n} 处，见 `{dir}/timeline.md` 里带 `flag` 的行和对应的镜头文件":
        "- {n} more: see the lines with `flag` in `{dir}/timeline.md` and their shot files",
    "- 要细拆时：{msg}": "- For a finer breakdown: {msg}",
    "## 地图": "## Map",
    "先读本页；要细节时只打开需要的文件。每行是 `路径 | 约 token | 里面有什么 · 什么时候读`，路径相对本目录。":
        "Read this page first; for details open only the files you need. Each line is `path | ~tokens | what is in it · when to read it`; paths are relative to this directory.",
    "## 读法": "## How to read",
    "- 「看到的」可以对着 `frames/` 里的帧核实；戏剧作用、情绪、必须保住什么是拆解的判断，两者对不上时以画面为准。":
        "- \"What is seen\" can be checked against the frames in `frames/`; the job in the drama, the emotion and what must be kept are the breakdown's reading: where the two disagree, the picture wins.",
    "- 这些 `.md` 是从 `story.json` 生成的，改了会被覆盖：要纠正拆解就改 `story.json`，再 `rea-toon redo pages <目录>` 重出。":
        "- These `.md` files are generated from `story.json` and overwritten on the next run: to correct the breakdown, edit `story.json`, then `rea-toon redo pages <dir>`.",

    # ---- videomd.py: map lines ----
    "场景：一行一场 · 先看这一集分几场、每场在哪": "Scenes: one line per scene · how many scenes the episode has and where each is",
    "时间线：一行一个镜头 · 某段讲什么、谁说了什么、哪镜是看点、哪镜要确认":
        "Timeline: one line per shot · what a stretch is about, who says what, which shots are highlights, which need confirming",
    "内容与结构 · 剧情、前情、戏剧节拍、信息差": "Content and structure · plot, previously, dramatic beats, information gaps",
    "角色、造型与画风 · 谁是谁、每场穿什么、画风规则；做角色参考图时读":
        "Characters, looks and art style · who is who, what each wears per scene, the style rules; read for character reference images",
    "爆款拆解 · 为什么火：卖点、情绪账、套路、人设、可复用公式、改编方向":
        "Viral breakdown · why it works: selling points, emotion ledger, tropes, characters, the reusable formula, adaptation ideas",
    "逐镜细节，token 是每个文件的平均值 · 核实某一镜：画面、台词、造型、戏剧作用、这一镜不能丢什么":
        "Per-shot details, tokens are the average per file · check one shot: picture, lines, looks, job in the drama, what it must not lose",
    "台词轨（WebVTT，带说话人和时间）· 查谁在什么时候说了什么": "Line track (WebVTT, with speakers and times) · who says what when",
    "剧本源码，全部字段 · 程序读；要改拆解时改它": "Script source, every field · for programs; edit it to change the breakdown",
    "爆款拆解的数据：节奏曲线、情绪账、戏份 · 程序读": "Data of the viral breakdown: pacing curve, emotion ledger, screen time · for programs",
    "字幕轨：每句的时间、位置、OCR 原文 · 程序读": "Subtitle track: time, position and OCR text of each line · for programs",
    "每镜的首 / 中 / 尾帧 · 看图核实": "First / middle / last frame of each shot · check against the picture",
    "每个角色的头像、造型图、配音样本 · 做角色参考": "Each character's portrait, look images and voice sample · for character reference",
    "声音（rea-video 拆的）：人声、配乐、音效和节拍 · 听配音配乐": "Sound (from rea-video): voice, music, sound effects and beats · for voice and music",
    "rea-video 量出来的：切点、人声 / 配乐分轨 · 听声音": "Measured by rea-video: cut points, voice / music stems · for the sound",
    "给人看的页面（从 index.html 进）· 模型不用读": "Pages for people (start at index.html) · models need not read them",
    "原片": "Source video",

    # ---- videomd.py: SERIES.md ----
    "**{name}**：{x}": "**{name}**: {x}",
    "一句话卖点": "Logline",
    "主冲突": "Main conflict",
    "情绪打法": "Emotional strategy",
    "**规模**：{eps} 集，拆完 {done} 集，共 {mins:.1f} 分钟": "**Size**: {eps} episodes, {done} broken down, {mins:.1f} minutes in all",
    "；类型：{kind}": "; type: {kind}",
    "**主要角色**：": "**Main characters**: ",
    "{name}（`{id}`，{role}）": "{name} (`{id}`, {role})",
    "每行是 `路径 | 约 token | 里面有什么 · 什么时候读`，路径相对本目录。每集是一个 VIDEO.md 拆片包，从它的 `VIDEO.md` 读起。":
        "Each line is `path | ~tokens | what is in it · when to read it`; paths are relative to this directory. Each episode is a VIDEO.md package: start at its `VIDEO.md`.",
    "跨集拆解全文 · 分阶段打法、人物弧光、伏笔和回收、集尾钩子、付费卡点、跨集情绪账":
        "Full cross-episode breakdown · play by stage, character arcs, setups and payoffs, episode-end hooks, paywall points, cross-episode emotion ledger",
    "全剧剧本（逐集逐场）· 要读原文台词和动作时": "Script of the whole drama (by episode and scene) · for the original lines and action",
    "第 {ep} 集（{secs:.0f}s，{peaks} 个情绪节点）：{about}": "Episode {ep} ({secs:.0f}s, {peaks} emotional beats): {about}",
    "- 问整部剧（套路、结构、人物）先读本页，不够再读跨集拆解全文；问某一集读那一集的 `VIDEO.md`，逐层往下。":
        "- For the whole drama (tropes, structure, characters) read this page first, then the full cross-episode breakdown if needed; for one episode read its `VIDEO.md` and go down layer by layer.",
    "- 这页和各集的拆片包都是生成的：跨集的内容改 `series.json`，某一集改它的 `story.json`，再 `rea-toon redo pages <目录>`。":
        "- This page and the episode packages are generated: edit `series.json` for what spans episodes, an episode's `story.json` for that episode, then `rea-toon redo pages <dir>`.",

    # ---- story.py: display helpers ----
    "（单集，或这是第一集：没有前情）": "(A single episode, or the first one: nothing came before)",
    "这是第 {ep} 集。": "This is episode {ep}.",
    "\n上一集结尾留的钩子：{hook}": "\nHook left at the end of the previous episode: {hook}",
    "镜 {to} 揭开": "revealed in shot {to}",
    "这一集没揭开": "not revealed in this episode",
    "{what}：{knows} 知道，{blind} 不知道；{opened}。{play}": "{what}: {knows} know(s), {blind} do(es) not; {opened}. {play}",
    " · 剧情日 {day}": " · story day {day}",
    " · 回忆": " · flashback",
    "发型：": "hair: ",
    "脸上：": "face: ",
    "道具：": "props: ",

    # ---- checks.py: risk tags ----
    "画外音：说话的人不在画面里，画面里的嘴要闭着": "Off-screen voice: the speaker is not in frame; mouths on screen stay shut",
    "内心独白/旁白：声音是画面里的人，但嘴要闭着，只用表情演": "Inner voice / narration: the voice is someone in frame, but the mouth stays shut; act with the face only",
    "一镜多人说话：每句台词要对上各自的嘴": "Several speakers in one shot: each line must match its own mouth",
    "有人中途入画：首帧没有，后面才出现": "Someone enters mid-shot: not in the first frame, appears later",
    "闪白/淡入开场：首帧几乎没有画面内容": "Opens on a white flash / fade-in: the first frame shows almost nothing",
    "不到 0.8 秒的短镜": "Short shot, under 0.8 s",
    "超过 15 秒：视频模型一次生成不了": "Over 15 s: too long for one video-model generation",
    "大运镜或画面变化大": "Big camera move or large change in the picture",
    "有群众/路人": "Crowd / passers-by",
    "三个及以上角色同框": "Three or more characters in frame",
    "乔装/换装：同一个角色这一镜是另一套造型": "Disguise / costume change: the same character in another look in this shot",
    "这一镜里造型变了（摘假发、脱外套…）": "The look changes within the shot (wig off, coat off…)",
    "人名字卡：不是台词，不能有嘴在动": "Name card: not a line, no mouth may move",
    "手部和道具动作（拿、递、推、挥…）": "Hand and prop action (hold, hand over, push, wave…)",
    "主角背对镜头": "Lead with back to camera",
    "画面里有场景文字（招牌/屏幕/文件）": "Scene text in the picture (sign / screen / document)",
    "有后期花字": "Styled text added in post",
    "身体接触（扇、泼、抓、推、递），动作落在音效点上": "Physical contact (slap, splash, grab, push, hand over) landing on a sound hit",
    "镜头里有定格": "Freeze frame in the shot",
    "慢动作": "Slow motion",
    "静图推拉（动态漫画）：一张图在推拉": "Still image pushed / panned (motion comic): one picture moving",
    "局部动：画面基本静止，只有一小块在动（眨眼、头发、嘴）": "Partial motion: the picture is mostly still, only a small part moves (blink, hair, mouth)",
    "术法/粒子特效（剑气、灵光、法阵）": "Magic / particle effects (sword energy, glow, magic circles)",
    "多宫格分屏": "Multi-panel split screen",
    "漫符（汗滴、怒筋、闪光、速度线）": "Manga marks (sweat drops, anger veins, sparkles, speed lines)",

    # ---- checks.py: lint ----
    "还没做深度拆解（rea-toon redo breakdown）": "No deep breakdown yet (rea-toon redo breakdown)",
    "逐镜拆解失败：{error}": "Per-shot breakdown failed: {error}",
    "没有结果": "no result",
    "台词「{line}」的说话人拿不准（现在标的是 {who}，看了图也定不下来）：对着原片确认，改好后删掉这句的 unsure":
        "Speaker of the line \"{line}\" is uncertain (marked {who} for now; the picture did not settle it): check against the source video, then remove this line's unsure",
    "台词「{line}」没有说话人": "The line \"{line}\" has no speaker",
    "台词「{line}」的说话人 {who} 不在角色表里": "The speaker {who} of the line \"{line}\" is not in the cast",
    "台词「{line}」标成 {who} 在画面里说，但这一镜没标出 {who}": "The line \"{line}\" is marked as said on screen by {who}, but this shot does not list {who}",
    "有主要角色但没有表演拆解（逐秒表情/口型）": "Has main characters but no acting breakdown (per-second expressions / mouth)",
    "{secs:.1f}s 超过 15 秒：用 analyze --cuts 切开": "{secs:.1f}s is over 15 s: split it with analyze --cuts",
    "身体接触拆解失败：这一镜的接触时间没有，重跑 rea-toon redo breakdown":
        "Physical contact breakdown failed: this shot has no contact times; rerun rea-toon redo breakdown",
    "画面里的字「{text}」没分出是场景文字还是花字（在 story.json 的 screen_text 里填 kind）":
        "On-screen text \"{text}\" was not sorted into scene text or styled text (fill in kind under screen_text in story.json)",
    "没有看点拆解（role/priority/keep）": "No highlight breakdown (role/priority/keep)",
    "必保镜头没有写剧情功能（这一镜做成了什么、观众什么感觉）": "Must-keep shot has no story function (what the shot achieves, what the audience feels)",
    "{who} 在第 {scene} 场没有造型描述": "{who} has no look described in scene {scene}",
    "第 {scene} 场造型拆解失败：{error}": "Look breakdown of scene {scene} failed: {error}",
    "{who} 在同一剧情日 {day} 里，第 {before} 场结尾穿「{was}」，第 {scene} 场开头穿「{now}」：确认是剧情里换了衣服，不是拆解看错":
        "{who}, within story day {day}, ends scene {before} wearing \"{was}\" and starts scene {scene} wearing \"{now}\": confirm the story changes clothes and the breakdown did not misread",
    "{a} 和 {b} 在镜{i}是{a}左{b}右，到镜{j}左右换了（同一场）：可能越轴，可能是原片就这样拍，也可能是拆解看错了位置":
        "{a} and {b}: in shot {i} {a} is left and {b} right, by shot {j} they have swapped sides (same scene): maybe a jump over the axis, maybe the source is shot that way, or the breakdown misread the positions",
    "{a} 和 {b} 可能是同一个人（乔装/换装），确认后在 story.json 合并：{clue}":
        "{a} and {b} may be the same person (disguise / costume change); once confirmed, merge them in story.json: {clue}",
    "画面里的 {who}（镜{shots}）不在角色表里：是有戏的人就加进角色表，是尸体/物件/背景就不用管":
        "{who} in the picture (shots {shots}) is not in the cast: add them if they play a part; ignore if a body / object / background",
    "快拆没做逐秒表演和身体接触拆解：只看拆解不用管；要补就跑 rea-toon deepen":
        "A fast breakdown has no per-second acting or physical contact passes: fine for reading the breakdown; to add them run rea-toon deepen",
    "必保镜头 {n} 个，太多就等于没有重点": "{n} must-keep shots: too many means no focus",
}
