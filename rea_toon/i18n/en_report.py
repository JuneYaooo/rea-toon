"""English for the hit-formula report (report.py), the drama formats (genre.py) and the emotion names."""

T = {
    # separators
    "、": ", ",
    "，": ", ",
    "：": ": ",

    # emotions (story.FEELS), as shown
    "爽": "Payoff",
    "憋": "Build-up",
    "虐": "Heartbreak",
    "甜": "Sweet",
    "笑": "Funny",
    "悬": "Suspense",
    "惊": "Shock",
    "燃": "Hype",

    # dramatic roles (story.ROLE_CN) and priorities (story.PRIORITY_CN), as shown
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

    # how a line is said (subtitles.MODE_CN, plus the on-screen default)
    "画内": "on screen",
    "画外": "off screen",
    "内心独白": "inner monologue",
    "旁白": "narration",

    # shot sizes
    "大特写": "extreme close-up",
    "特写": "close-up",
    "近景": "medium close-up",
    "中近景": "medium close-up",
    "中景": "medium shot",
    "全景": "full shot",
    "远景": "wide shot",

    # camera moves (the main word of the model's camera_move, from the prompt's option list)
    "固定": "static",
    "推": "push in",
    "微推": "slight push in",
    "拉": "pull out",
    "微拉": "slight pull out",
    "摇": "pan",
    "微摇": "slight pan",
    "移": "track",
    "右移": "track right",
    "左移": "track left",
    "跟": "follow",
    "升": "crane up",
    "降": "crane down",
    "环绕": "orbit",
    "手持": "handheld",
    "推镜头": "push in",
    "拉镜头": "pull out",
    "摇镜头": "pan",
    "移镜头": "track",
    "跟镜头": "follow",
    "升镜头": "crane up",
    "降镜头": "crane down",
    "跟拍": "follow",
    "?": "?",

    # formats (genre.FORMATS)
    "真人短剧": "Live-action short drama",
    "AI 真人感短剧": "AI photoreal short drama",
    "2D 漫剧": "2D anime drama",
    "3D 动画短剧": "3D animated short drama",
    "动态漫画": "Motion comic",
    "沙雕漫": "Goofy comic (shadiao)",
    "静态解说漫": "Narrated comic (still images)",
    "动态解说漫": "Narrated comic (animated)",
    "演员表演和台词冲突：每场戏谁压谁、靠哪句台词翻盘":
        "Acting and verbal conflict: who dominates each scene, and which line turns it around",
    "打脸/反转的节奏：铺垫几镜、爆点几秒、反应镜头给多长":
        "Rhythm of the comeuppance/reversal: how many shots of setup, how many seconds of payoff, how long the reaction shots run",
    "场景和道具成本：几个景、几套衣服撑起一集":
        "Set and prop cost: how many locations and outfits carry an episode",
    "人设靠什么一眼立住：造型、名字卡、第一句台词":
        "What makes a character land at first sight: look, name card, first line",
    "画面奇观在哪：AI 才拍得出的场面（排场、特效、动物/非人角色）占多少":
        "Where the spectacle is: how much is scenes only AI can make (grand settings, effects, animal/non-human characters)",
    "镜头短、口型少的段落怎么靠剪辑和配乐维持情绪":
        "How stretches of short shots with little lip-sync keep the emotion up through editing and music",
    "漫画语言：分格、漫符、特效、速度线在哪些镜头放大情绪":
        "Comic language: which shots use panels, manga marks, effects and speed lines to amplify emotion",
    "人设和变脸：主角几种表情撑起整集":
        "Character design and face changes: how many expressions carry the lead through the episode",
    "旁白/内心独白和画面怎么分工":
        "How narration/inner monologue and the picture divide the work",
    "动作和特效奇观的位置和时长":
        "Where the action and effects set pieces are, and how long they run",
    "角色设计记忆点（体型、配色、标志性道具）":
        "Memorable character design (build, color scheme, signature props)",
    "镜头运动比真人剧大多少、靠运镜制造的爽感":
        "How much bigger the camera moves are than in live action, and the payoff created by camera movement",
    "一张图撑多久：静图推拉的节奏和切图频率":
        "How long one image holds: the rhythm of push/pull on stills and how often the image changes",
    "旁白/对话气泡承担了多少叙事":
        "How much of the storytelling narration and speech bubbles carry",
    "哪些格子做了局部动或特效，钱花在哪":
        "Which panels got partial animation or effects: where the money went",
    "反差和梗的密度：多久一个笑点、靠台词梗还是画面梗":
        "Density of contrast and gags: how often a laugh lands, and whether it comes from lines or visuals",
    "简笔/Q 版人设靠什么一眼认出：配色、标志物、口头禅":
        "What makes simple/chibi characters recognizable at a glance: colors, signature items, catchphrases",
    "配音和音效怎么撑起笑点（变声、拟声、鬼畜重复）":
        "How voice acting and sound effects carry the jokes (voice changes, onomatopoeia, remix-style repetition)",
    "解说词结构：开头钩子句、悬念句、反问、金句各在哪":
        "Structure of the narration: where the opening hook line, suspense lines, rhetorical questions and punchlines fall",
    "一张图配多少句解说、多久换图，换图是不是落在信息点上":
        "How many narration lines per image, how often the image changes, and whether changes land on new information",
    "语速和信息密度：每秒几个字、多久抛一个新信息":
        "Pace and information density: characters per second, and how often a new piece of information drops",
    "画面和解说的配合：说到哪切到哪、有没有画不对词":
        "How picture and narration fit: whether cuts follow the words, and whether any images miss the line",

    # why a format was chosen (genre.why_shown)
    "story.json 里手动指定": "set by hand in story.json",
    "按整部剧定的类型": "the format set for the whole series",
    "{narr} 的台词是旁白，{still} 的时长是静图": "{narr} of the dialogue is narration, {still} of the running time is still images",
    "{narr} 的台词是旁白，画面是动的": "{narr} of the dialogue is narration, and the picture moves",
    "{still} 的时长是静图或静图推拉": "{still} of the running time is still images or push/pull on stills",
    "画风：{kind}（默认按 AI 真人感短剧）": "style: {kind} (treated as AI photoreal short drama by default)",
    "画风：{kind}": "style: {kind}",
    "未知": "unknown",

    # report: markdown
    "# 爆款拆解：{title}": "# Hit breakdown: {title}",
    "**类型**：{format}（{why}）": "**Format**: {format} ({why})",
    "**一句话卖点**：{logline}": "**Logline**: {logline}",
    "**题材**：{themes}　**受众**：{audience}　**爽感**：{pleasure}":
        "**Themes**: {themes}  **Audience**: {audience}  **Pleasures**: {pleasure}",
    "## 数据": "## Numbers",
    "时长 {duration}s · {shots} 镜 · {scenes} 场 · 平均镜长 {avg_shot}s · 每分钟 {cuts_per_min} 刀":
        "Runtime {duration}s · {shots} shots · {scenes} scenes · average shot {avg_shot}s · {cuts_per_min} cuts per minute",
    "台词每秒 {talk} 字 · 第一个冲突 {conflict} · 第一个情绪节点 {peak} · 情绪节点 {peaks} 个 · {cliff}":
        "{talk} dialogue characters per second · first conflict {conflict} · first emotional beat {peak} · {peaks} emotional beats · {cliff}",
    "有结尾钩子": "has a cliffhanger",
    "没有结尾钩子": "no cliffhanger",
    "节点之间的空档：平均 {avg_gap}s · 最长 {longest_gap}s · {hit_share:.0%} 的时长在节点里":
        "Gaps between beats: average {avg_gap}s · longest {longest_gap}s · {hit_share:.0%} of the runtime is inside a beat",
    "第一个情绪节点在 {t:.1f}s，晚于 {limit:.0f}s": "The first emotional beat is at {t:.1f}s, later than {limit:.0f}s",
    "{a}–{b}s 连续 {n}s 没有情绪节点": "{a}–{b}s: {n}s in a row with no emotional beat",
    "## 开头钩子": "## Opening hook",
    "## 情绪账": "## Emotion ledger",
    "情绪配比（节点数/秒数）：": "Emotion mix (beats/seconds): ",
    "{feel} {n} 个/{seconds}s": "{feel} {n}/{seconds}s",
    "憋→爽：{pairs}；没有先憋就放的爽点 {free} 个；片尾还欠着的憋 {owed}s":
        "Build-up → payoff: {pairs}; payoffs with no build-up before them: {free}; build-up still owed at the end: {owed}s",
    "{press}s 开始憋 → {release}s 放（等了 {hold}s，其中憋的镜头 {pressed}s）":
        "build-up from {press}s → released at {release}s (waited {hold}s, {pressed}s of it in build-up shots)",
    "没有成对的": "no pairs",
    "- {t:.1f}s **{feel}** 镜 {shots}：{what}": "- {t:.1f}s **{feel}** shots {shots}: {what}",
    "## 信息差": "## Information gaps",
    "{what}：{knows} 知道，{blind} 不知道；{opened}。{play}":
        "{what}: {knows} know(s), {blind} do(es) not; {opened}. {play}",
    "镜 {shot} 揭开": "revealed in shot {shot}",
    "这一集没揭开": "not revealed in this episode",
    "## 结构": "## Structure",
    "- **{part}**（镜 {shots}）：{what}": "- **{part}** (shots {shots}): {what}",
    "## 戏剧节拍": "## Dramatic beats",
    "- {t:.1f}s **{role}**{feel} 镜 {shots}：{what}": "- {t:.1f}s **{role}**{feel} shots {shots}: {what}",
    "（{feel}）": " ({feel})",
    "（兑现：{pays}）": " (pays off: {pays})",
    "## 套路": "## Tropes",
    "- **{name}**（镜 {shots}）：{how}": "- **{name}** (shots {shots}): {how}",
    "## 人设": "## Characters",
    "- **{id}** {persona}：{function}；记忆点：{memorable}": "- **{id}** {persona}: {function}; memorable for: {memorable}",
    "## 关系和张力": "## Relationships and tension",
    "- {a} × {b}：{relation}；{tension}": "- {a} × {b}: {relation}; {tension}",
    "为什么留得住人": "Why it holds viewers",
    "短板": "Weaknesses",
    "改编方向": "Adaptation ideas",
    "## 可复用公式": "## Reusable formula",
    "{i}. **{step}**（约 {share:.0%}）：{what}": "{i}. **{step}** (about {share:.0%}): {what}",
    "## 这类剧要看的": "## What to study in this kind of drama",

    # report: page
    "爆款拆解 {title}": "Hit breakdown {title}",
    "文字解读写在数据更新之前，引用的数字可能和图表对不上。":
        "The written reading predates the latest numbers; figures it quotes may not match the charts.",
    "模型解读失败（只有数据部分）：{error}；重跑 rea-toon redo report":
        "The model reading failed (numbers only): {error}; rerun rea-toon redo report",
    "镜": "shots",
    "第一个情绪节点": "first emotional beat",
    "个情绪节点": "emotional beats",
    "节点间平均空档": "average gap between beats",
    "有": "yes",
    "没有": "no",
    "可复用公式": "Reusable formula",
    "步骤": "Step",
    "占比": "Share",
    "要做到": "What it must do",
    "结构和节奏": "Structure and pacing",
    "{t0:.0f}–{t1:.0f}s 强度 {intensity} · 切 {cuts} 刀 · 每秒 {talk} 字":
        "{t0:.0f}–{t1:.0f}s intensity {intensity} · {cuts} cuts · {talk} chars/s of dialogue",
    " · 镜 {a}–{b}": " · shots {a}–{b}",
    "{t:.1f}s {what}：{how}": "{t:.1f}s {what}: {how}",
    "情绪强度随时间；圆点是钩子和情绪节点，悬停看是哪一镜。":
        "Emotional intensity over time; dots are hooks and emotional beats, hover to see which shot.",
    "情绪强度随时间": "Emotional intensity over time",
    "第一个情绪节点在 {t:.1f}s，晚于 {limit:.0f}s：开头可能留不住人":
        "The first emotional beat is at {t:.1f}s, later than {limit:.0f}s: the opening may lose viewers",
    "{a}–{b}s 连续 {n}s 没有情绪节点，也没有悬念撑着：容易划走":
        "{a}–{b}s: {n}s in a row with no emotional beat and no suspense holding it: viewers may swipe away",
    "{a}–{b}s 没有新的情绪节点，其中 {held}s 靠悬念撑着":
        "{a}–{b}s: no new emotional beat; {held}s of it is held by suspense",
    "镜 {shots}": "shots {shots}",
    "镜 {shot}": "shot {shot}",
    "套路": "Tropes",
    "开头钩子": "Opening hook",
    "开头钩子和套路": "Opening hook and tropes",
    "前几秒怎么留人": "how the first seconds hold viewers",
    "每种情绪占的秒数（爽、虐、甜、笑、惊、燃是打到观众的；憋、悬是在攒）":
        "Seconds of each emotion (Payoff, Heartbreak, Sweet, Funny, Shock and Hype hit the audience; Build-up and Suspense store it up)",
    "{feel}：{n} 段，共 {seconds}s": "{feel}: {n} runs, {seconds}s in all",
    "开始憋": "Build-up starts",
    "放（爽）": "Released (payoff)",
    "等了": "Waited",
    "其中憋的镜头": "Of it in build-up shots",
    "有 {owed} 秒的憋到片尾也没放，留给后面的集。":
        "{owed}s of build-up is still unreleased at the end, left for later episodes.",
    "时间": "Time",
    "情绪": "Emotion",
    "镜头": "Shots",
    "做成了什么": "What it achieves",
    "{feel} {seconds:g}s": "{feel} {seconds:g}s",
    "情绪账": "Emotion ledger",
    "{n} 个节点": "{n} beats",
    "信息差": "Information gaps",
    "{n} 条 · 谁知道、谁不知道、哪一镜揭开": "{n} · who knows, who doesn't, which shot reveals it",
    "是什么": "What",
    "谁知道": "Who knows",
    "谁不知道": "Who doesn't",
    "揭开": "Revealed",
    "怎么做戏": "How it's played",
    "没揭开": "not revealed",
    "角色": "Character",
    "出镜": "Screen time",
    "台词": "Dialogue",
    "人设": "Persona",
    "作用": "Function",
    "记忆点": "Memorable for",
    "{lines} 句 / {chars} 字": "{lines} lines / {chars} chars",
    "对手戏": "Two-handers",
    "两人": "Pair",
    "同框": "Together on screen",
    "你来我往": "Exchanges",
    "关系和张力": "Relationship and tension",
    "{n} 次": "{n}",
    "人物和对手戏": "Characters and two-handers",
    " 等": " etc.",
    "制作量": "Production scale",
    "{places} 个场景 · {cast} 个角色 · {shots} 镜": "{places} locations · {cast} characters · {shots} shots",
    "<li><b>场景</b> {n} 个：{places}</li>": "<li><b>Locations</b> {n}: {places}</li>",
    "<li><b>演员</b> {n} 个有戏份的角色": "<li><b>Cast</b> {n} characters with a part",
    "，分场造型共 {looks} 套次": ", {looks} looks across scenes",
    "<li><b>镜头</b> {n} 个，平均 {avg}s，每分钟切 {cuts} 刀；景别：{sizes}；运镜：{moves}</li>":
        "<li><b>Shots</b> {n}, average {avg}s, {cuts} cuts per minute; shot sizes: {sizes}; camera moves: {moves}</li>",
    "<li><b>台词</b> 每秒 {talk} 字；{modes}</li>": "<li><b>Dialogue</b> {talk} chars per second; {modes}</li>",
    "<li><b>后期</b> 特效 {vfx} 镜、花字 {overlays} 处</li>":
        "<li><b>Post</b> effects in {vfx} shots, {overlays} text overlays</li>",
    "依据和细节，点开看": "Evidence and details, open to read",
}
