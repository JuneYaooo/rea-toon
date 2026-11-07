"""Prompts of the breakdown (analysis, deep breakdown, report, series). The remake keeps its own in rea_toon_remake."""

CAST_PROMPT = """你在给一部 AI 短剧做"剧本逆向"。下面几张图是这条视频的分镜联系表：每个格子是一个镜头的中间帧，左上角白底黑字是镜头编号。
每个镜头的字幕（OCR，可能有误）：
{subtitles}

任务：
1. 用 2-4 句话概括剧情、笑点/爽点和情绪走向。
2. 判断整体画风（如：写实电影感 AI 短剧 / 3D 动画 / 国漫 等）和色调。
3. 列出所有"可识别的角色"：同一个人在多个镜头出现要合并成一个角色。群众演员不用逐个列，合并成 crowd。
   换装、乔装、女扮男装/男扮女装、戴假发的仍是同一个角色，不要拆成两个：看脸、画面上的人名字卡（如大字"温月"）、
   台词（"明天我亲自去"后穿着那套衣服出现）和剧情前后来判断。
   每个角色给：
   - id：英文小写短标识（如 groom、bride、chef）
   - role：中文身份（新郎、新娘、厨师…）
   - importance：lead（主角）| supporting（配角）| extra（群演）
   - appearance：外貌与服装的具体描述（性别、年龄感、发型、脸型、服装颜色款式、配饰），足够让画师单独画出这个人
   - shots：出现的镜头编号列表（包括背影、远景小人）

只返回 JSON：
{{"summary": "...", "style": "...", "palette": "...",
  "characters": [{{"id": "groom", "role": "新郎", "importance": "lead", "appearance": "...", "shots": [1, 4]}}]}}
"""


# Episode N of a series is analysed on its own; without what earlier episodes settled the model re-guesses who is who
# (episode 2 cast the time traveller as the wrong person). Appended to CAST_PROMPT only in a series, so a single video's
# prompt (and its cache) stays as it was.
SERIES_CONTEXT = """

这是一部连续剧的第 {ep} 集，下面是前面几集已经拆出来的情况，概括剧情和认角色时以此为准（身份、谁是穿越者/重生者、谁和谁什么关系），
只有画面和台词明显对不上时才按画面改：
前情：
{recap}
已出场的角色：同一个人在这一集出现时沿用这里的 id 和身份（换了衣服也是同一个人，外貌按这一集画面写）；新角色再起新 id：
{cast}"""


SHOT_PROMPT = """你在给一部 AI 短剧做逐镜头拆解，目的是之后用"首帧/尾帧 + 视频模型"把这个镜头重新生成出来，
并且可能把其中某些角色换成别的形象。剧情：{summary}

已知角色：
{cast}

这是第 {index} 镜（{start:.2f}s–{end:.2f}s，时长 {duration:.2f}s）。字幕（OCR）：{subtitle}
图片依次是：这个镜头的首帧、中间帧、尾帧。

只返回 JSON：
{{
  "scene": "场景与环境（地点、天气光线、背景元素）",
  "shot_size": "特写|近景|中景|全景|远景|航拍",
  "camera_move": "固定|推|拉|摇|移|跟|升|降|环绕|手持|快速冲刺 等，写清方向",
  "characters": [
    {{"id": "已知角色 id（不认识就写 crowd 或新起一个英文 id）",
      "where": "在画面中的位置与朝向（如：画面左侧、正对镜头、背影）",
      "visibility": "face|profile|back|tiny|partial",
      "action": "这个人在镜头里做什么",
      "expression": "表情与情绪"}}
  ],
  "in_first": ["首帧里能看到的角色 id（含背影、远处小人）"],
  "in_last": ["尾帧里能看到的角色 id"],
  "speaker": "正在说话的角色 id；没人说话写 null；画外音写 offscreen",
  "lip_visible": true,
  "emotion": "这个镜头的情绪基调（如：震惊、搞笑、得意、温馨）",
  "action_summary": "一句话：从首帧到尾帧发生了什么",
  "motion_prompt": "给视频模型的中文提示词：按时间顺序描述人物动作、表情变化、口型（说话就写'他激动地说话'），以及运镜。50-120字，不要写字幕内容，不要写画风",
  "change": "small|large（首帧到尾帧画面内容变化大不大）",
  "has_burned_text": true
}}
"""


PERFORMANCE_PROMPT = """你在给一部 AI 短剧做"表演拆解"，目的是让视频模型在换了角色之后，逐秒复现原镜头里演员的表演。
下面 {n} 张图是同一个镜头按时间顺序抽的帧（镜头时长 {duration:.2f}s），每张图的时间点（相对镜头开头）依次是：{times}。
这一镜的字幕（相对时间）：{subtitles}
画面里的角色（id：身份）：
{cast}

只看画面，如实描述每个角色的脸和身体在时间上怎么变化。要求：
- 表情要写具体的五官动作（眉毛、眼睛、嘴、头部、肩手），不要只写"开心/惊讶"这种标签。
- 嘴：写清楚每段时间是在说话（张合幅度大/小）、大笑、微笑、抿嘴还是闭嘴。
- 情绪强度如实写：夸张的就写夸张；如果全程都是惊慌，不要写成"转为微笑"。
- 只写看得到的角色；背对镜头的写"背对镜头，看不到脸"。

只输出 JSON：
{{"overall": "这一镜整体情绪和强度，一句话",
  "beats": [{{"t0": 0.0, "t1": 1.2, "who": "角色 id", "face": "五官动作", "mouth": "说话/大笑/微笑/闭嘴…及幅度", "body": "头/手/身体动作"}}],
  "camera": "镜头运动，没有就写固定",
  "motion": "给视频模型的一段镜头描述：场景、人物站位和朝向、运镜、动作和表情变化，必须和 beats 一致",
  "avoid": ["和原片相比视频模型容易做错、需要明确禁止的表演，比如：不要笑、不要转头看镜头"]}}"""


SUBS_PROOFREAD_PROMPT = """下面是用 OCR 从一部短剧画面里识别出的字幕，按时间顺序编号。OCR 常把形近字认错，
例如 首→百、自→目、丢→去、糟→禮、帅→师、吧→叫。剧情：{summary}

请只修正 OCR 认错的字（形近字、漏字、多出的杂字），不要改写、润色或统一说法，口语和语气词保持原样；
拿不准的不改。只输出 JSON：{{"fixes": [{{"i": 编号, "text": "改正后的整句"}}]}}，没有要改的输出 {{"fixes": []}}。

{lines}"""

# Restyled remakes: the shot's action text names the original things; rewrite it for the new keyframes.


LINE_SPEAKER_PROMPT = """这是一部短剧的完整台词，按时间顺序，每行：序号|镜头|时间|该镜头画面里能看到的角色(id:可见程度)|台词。
剧情：{summary}
角色：
{cast}

根据剧情和上下文判断每一句台词是谁说的（角色 id）。说话的人不一定在画面里，也不一定是画面里正对镜头的那个人；
一来一回的对话要逐句分清。mode 是这句话怎么说出来的，决定画面里谁的嘴在动：
- on：说话的人在这个镜头画面里，嘴真的在说（看得到正脸或侧脸）
- off：说话的人不在画面里，或者背对镜头（画外音）
- inner：内心独白 / 心声 / 被"读心"听到的想法：声音是这个角色的，但他的嘴闭着，哪怕他就在画面正中
- narration：旁白、解说（第三人称讲故事、"三年后"这类），画面里没有人在说；讲述者不是剧中角色时 speaker 写 "narrator"
判断 inner 的线索：台词是心里话（"这女人在想什么""他居然不知道我是…"），和画面里正在发生的对话对不上，前后有人回应的是别的话；
有"读心术""听到心声"设定的剧里尤其多。不确定就按 on/off 判断。
同一句台词跨过切点时会在相邻两个镜头里各出现一行（常见：前一镜他在说，后一镜切到听的人的反应），要按每一行所在镜头的画面分别判断 mode，
后一镜里说话的人不在画面里或背对，就是 off。
人名字卡（画面上单独出现的角色名，不是台词）speaker 写 "title"，mode 写 off。
台词：
{lines}

只输出 JSON：{{"lines": [{{"i": 序号, "speaker": "角色id、narrator 或 title", "mode": "on|off|inner|narration"}}]}}"""

# One person split into two ids (disguise, costume change, cross-dressing): the remake would give them two looks.
# Asked about the whole cast at once the model explains clues away; asked about one suspect pair it weighs them.


IDENTITY_PROMPT = """核对一部短剧的角色表：{a} 和 {b} 会不会其实是同一个人（换装、乔装、女扮男装/男扮女装、戴假发后被当成了新角色）？
剧情：{summary}
- {a}：{a_info}
- {b}：{b_info}
图{a_pics} 是 {a}，图{b_pics} 是 {b}。
确定的线索（代码统计，不会错）：
{clues}
相关台词（按时间，已标说话人）：
{lines}

先在 analysis 里比较两人的脸（脸型、眉眼、鼻子、嘴）、身材，再结合线索和剧情逻辑下结论。没有把握就写 false。
只输出 JSON：{{"analysis": "...", "same": true 或 false, "reason": "一句话依据"}}"""

# Every generated clip, any mode: identity, human leaks and whose mouth moves, frame by frame.


SCENES_PROMPT = """你在给一部短剧/漫剧做深度拆解（复刻之前的"读剧本"）。图是分镜联系表：每格是一个镜头的中间帧，左上角是镜头编号。
剧情：{summary}
角色：
{cast}
逐镜（编号|时间|景别|场景|画面里的人(id:位置,动作)|台词(说话人)）：
{table}

任务：
1. style_bible：画风档案，复刻时整部片都要守住的视觉规则：
   - kind：真人感AI短剧 | 2D动漫 | 3D动画 | 动态漫画 | 实拍 | 其他
   - rendering：渲染和质感（如：写实电影感、柔光磨皮；赛璐璐平涂、粗描边、网点）
   - palette：主色调；lighting：光线
   - character_design：角色设计的共同规则（头身比、眼睛画法、美颜程度…）；真人感写"写实比例"
   - fx：这部剧反复使用的特效和包装（闪白转场、震屏、速度线、推近特写、花字/拟声字、慢动作、分屏…），没有就空列表
2. scenes：把镜头按"同一地点、同一段连续时间"分场（插入的回忆、转场后到了别处就是新的一场）。每场：
   id（S1、S2…）、shots：[起始镜号, 结束镜号]（连续区间；所有场按顺序覆盖全部镜头，不重叠）、
   location（地点）、time（白天/夜晚/回忆…）、present（这一场出现的角色 id）、
   day：剧情日，按故事里的时间顺序编号，白天 D1、当晚 N1、第二天 D2……（同一天的不同场同一个编号；过了一夜就加一）、
   flashback：这一场是不是回忆/闪回/想象（true/false）。闪白、黑白或泛黄、柔光朦胧、"三年前"字幕常常表示回忆；
   回忆场的 day 写它在故事里发生的那天（不知道就写 "回忆"），不要打断现在时间线的编号

只返回 JSON：
{{"style_bible": {{"kind": "", "rendering": "", "palette": "", "lighting": "", "character_design": "", "fx": []}},
  "scenes": [{{"id": "S1", "shots": [1, 8], "location": "", "time": "", "day": "D1", "flashback": false, "present": ["groom"]}}]}}"""


SCENE_LOOKS_PROMPT = """这是一部短剧第 {sid} 场（镜头 {a}–{b}，{location}，{time}）的几张画面：{pics}。
这一场的角色（括号里是角色表的外貌，只供参考，以画面为准）：
{cast}
这一场的逐镜描述：
{table}

为每个角色写出这一场里的"造型状态"，分四项：
- wear：服装（颜色、款式）和配饰（项链、眼镜、领巾、鞋）
- hair：发型；戴假发要写明"假发"
- face：脸上额外的东西（面膜、伤口、污渍、被泼的红酒、明显的妆），没有就空
- props：拿在手里或随身的道具，写清哪只手（左手/右手/双手/放在桌上/夹在腋下）和状态
  （满杯/半杯/空杯、点着的烟、合上的文件夹、碎了的手机），没有就空。看不清哪只手就只写道具
不要写五官和长相（复刻换角色时脸会换掉，造型要留下）。
同一场里中途有变化（摘假发、脱外套、被泼红酒、敷上面膜，道具换手、喝空、摔碎、递给别人）写进 changes：
从哪一镜开始，变化后的完整四项（没变的项也要照抄，道具给了别人就在两个人的 changes 里都写）。
画面里看不到的角色不写。
只返回 JSON：{{"looks": {{"角色id": {{"wear": "", "hair": "", "face": "", "props": ""}}}},
  "changes": [{{"shot": 镜号, "who": "角色id", "wear": "", "hair": "", "face": "", "props": ""}}]}}"""


BEATS_PROMPT = """你是短剧/漫剧的编剧兼剪辑指导，在给一集剧做"看点拆解"：之后会换角色、换画风复刻，你要指出哪些东西丢了观众就不买账。
剧情：{summary}
角色：
{cast}
分场：
{scenes}
逐镜（编号|时间|景别|运镜|画面里的人(id:动作,表情)|台词(说话人)|情绪）：
{table}

1. beats：这一集的戏剧骨架，按时间顺序。type 取：hook（开头钩子）、setup（铺垫）、conflict（冲突）、reversal（反转）、
   reveal（揭露身份或真相）、climax（高潮）、payoff（爽点、打脸、甜点、笑点）、cliffhanger（结尾钩子）。
   每条写 type、shots（镜号列表）、what（发生了什么）、must_keep（复刻必须保住的具体东西：哪个表情、动作、台词口型、道具、镜头节奏）。
2. shots：每一镜都要给：
   - role：上面的 type 之一，或 reaction（反应镜头）、transition（转场）、normal
   - priority：must（看点镜头，丢了这集就不成立：每个 beat 里最关键的那一两镜，合计约占全片一到两成）| high（重要情绪或信息）| normal
   - function：这一镜在剧情里的作用和情绪落点，一句话，写"谁对谁做成了什么、观众此刻什么感觉"
     （如"姐姐当场威慑弟弟，观众觉得好笑""沈总身份第一次露馅，观众期待打脸"）。换角色、换画风甚至换道具时，这个不能丢
   - keep：这一镜复刻必须保住的 1–3 个具体元素（短语，如"沈总推眼镜的慢动作""姐姐举鸡毛掸子作势要打"），normal 可为空；
     keep 是实现 function 的手段，改编换了场景道具时可以换成对应的新东西
   - fx：这一镜的特效和包装（闪白、震屏、花字、推近、慢动作…），没有就空
   - transition_in：从上一镜进到这一镜的方式：cut | white_flash | fade | dissolve | whip | match

只返回 JSON：
{{"beats": [{{"type": "hook", "shots": [1, 2], "what": "", "must_keep": ""}}],
  "shots": [{{"i": 1, "role": "hook", "priority": "must", "function": "", "keep": [""], "fx": [], "transition_in": "cut"}}]}}"""


# Text in the picture that is not a subtitle line (breakdown.screen_text): OCR finds it, this says what it is.


SCREEN_TEXT_PROMPT = """图是短剧第 {index} 镜的画面。OCR 在画面里（字幕之外）读到这些文字，序号|文字|大概位置：
{texts}
判断每段文字是什么：
- scene：场景里真实存在的字：招牌、门牌、手机/电脑屏幕、文件、合同、菜单、包装、衣服上的字、车牌
- bubble：漫画对话气泡、思考泡泡里的字（气泡是画面的一部分）
- overlay：后期叠加上去的包装字：花字、拟声字（"啪""震惊"）、强调大字、标题、贴纸、箭头旁的说明、"三年后"这类时间字幕
- aigc：AI 生成内容标识（"AI生成""人工智能合成""本视频由AI生成""AIGC"之类）
- watermark：台标、平台水印、账号名、角标
OCR 认错的乱码、看不出是字的纹理写 noise。
只输出 JSON：{{"texts": [{{"i": 序号, "kind": "scene|bubble|overlay|aigc|watermark|noise"}}]}}"""


STAGING_PROMPT = """图是短剧第 {index} 镜的画面（{size}）。画面里的角色：{people}
回答，只输出 JSON：
- angle：机位角度 eye（平视）| high（俯拍，显得弱小被压制）| low（仰拍，显得强势高大）| dutch（斜角）| pov（某个角色的主观视角）| ots（过肩，前景是某人的肩背）
- ots_of：过肩镜头前景那个人的角色 id，不是过肩就空
- people：每个看得见的角色：facing（脸朝 left 画面左 | right 画面右 | camera 镜头 | away 背对 | down 低头）、
  looking_at（看着谁：角色 id；或 camera、offscreen_left、offscreen_right、down、none）
- power：这一镜画面里谁居高位、谁居低位（站着对坐着、俯视对仰视），一句话，没有就空
- effects：画上去的特效（剑气、灵光、火焰、雷电、法阵、护盾、光效、粒子、气场），每个：what（是什么）、color（颜色）、
  from（从哪个角色身上发出，角色 id；不是从人身上发出就空）。普通的灯光、阳光、反光不算。没有就空列表
- marks：漫画符号（汗滴、怒筋"井"字、头顶问号/感叹号、脸红斜线、闪光星星、速度线、集中线、头上冒烟），没有就空列表
- panels：画面被分成几格（多宫格/分屏：几个画面用边框拼在一起）。一个完整画面就是 1
{{"angle": "eye", "ots_of": "", "people": {{"角色id": {{"facing": "left", "looking_at": "另一个角色id"}}}}, "power": "",
  "effects": [{{"what": "", "color": "", "from": ""}}], "marks": [], "panels": 1}}"""

# An episode's expressions per main character (breakdown.expressions): the remake can draw an expression sheet,
# the most common comic-drama failure being a face that turns into someone else when the mood changes.


EXPRESSIONS_PROMPT = """下面是一集短剧里每个主要角色逐镜的表情（镜号:表情）：
{rows}
把每个角色的表情归并成 3 到 8 种（意思相近的合成一种，比如"冷笑""嘴角上扬的嘲讽"合成"冷笑"），每种给：
name（两到四个字）、look（脸上具体什么样：眉、眼、嘴，一句话，用来画表情设定图）、shots（用到这种表情的镜号）。
只输出 JSON：{{"characters": {{"角色id": [{{"name": "", "look": "", "shots": [1]}}]}}}}"""

# A physical contact (slap, splash, push, grab, hug, hand-over) and when it happens: the original mix has the hit,
# the regenerated action must land on it. Frames are numbered with their time in the shot.


CONTACT_PROMPT = """这些图按时间顺序取自短剧第 {index} 镜（时长 {duration:.2f}s），每张图的时间（相对镜头开头）依次是：{times}。
这一镜的动作描述：{actions}
这一镜里有没有身体接触或道具碰到人/物的瞬间（扇巴掌、泼水泼酒、推搡、抓手腕、拥抱、亲吻、递交东西、拍桌、摔东西、关门、碰杯）？
有的话给出每个接触瞬间：who（动作发出者角色 id）、to（承受者角色 id 或物件）、action（短语，如"姐姐把红酒泼到沈总脸上"）、
t（接触发生的时间，秒，相对镜头开头：取刚好接触的那两张图之间）、hand（左手/右手/双手/不涉及）。没有就空列表。
只输出 JSON：{{"contacts": [{{"who": "", "to": "", "action": "", "t": 0.0, "hand": ""}}]}}"""


# Expression sheet of a swapped character (remake.expression_sheet, opt-in): the episode's moods on one face.


REPORT_PROMPT = """你是短剧爆款拆解师，给编剧和制片写一份"这一集为什么好看、套路是什么、怎么拿来用"的拆解。
类型：{format}（判断依据：{format_why}）。这类剧要重点看：
{lens}

剧情：{summary}
角色（id|身份|重要度|出镜秒数|台词字数）：
{cast}
分场：
{scenes}
戏剧节拍（类型|开始秒|镜号|发生了什么）：
{beats}
逐镜（编号|开始秒|时长|作用|优先级|剧情功能|台词(说话人)）：
{table}
代码量出来的数据：
{metrics}

要求：每个判断都落到具体的镜号、秒数或台词上，不写放之四海皆准的空话；数据已经算好的不要重算，用来下判断。
只返回 JSON：
{{
  "logline": "一句话卖点（谁、遇到什么、爽在哪），30 字内",
  "tags": {{"themes": ["题材，如 霸总、重生、替身、赘婿、萌宝、仙侠、悬疑、甜宠、家庭伦理"], "audience": "女频|男频|全年龄|下沉中老年 等",
           "pleasure": ["爽感类型，如 打脸、逆袭、身份揭露、虐后甜、反差萌、复仇"]}},
  "hook": "开头 3 秒和前 10 秒怎么抓住人：具体到镜号和台词，以及它让观众问了一个什么问题",
  "structure": [{{"part": "这一段的名字（如 钩子/铺垫/第一次冲突/反转/高潮/结尾钩子）", "shots": [起始镜号, 结束镜号], "what": "这一段做成了什么"}}],
  "tropes": [{{"name": "套路名（如 当众羞辱→身份揭露→跪舔）", "shots": [镜号], "how": "这一集是怎么用的、和常规用法有什么不同"}}],
  "characters": [{{"id": "角色 id", "persona": "人设标签（4-10 字）", "function": "在这一集里的戏剧作用", "memorable": "让人记住的一个具体点（造型/口头禅/动作）"}}],
  "relations": [{{"a": "角色 id", "b": "角色 id", "relation": "关系", "tension": "两人之间的张力来自哪里"}}],
  "why_it_works": ["为什么能留住人，3-5 条，每条带证据（镜号/秒数）"],
  "weaknesses": ["短板或掉人的风险点，1-3 条，带证据"],
  "formula": [{{"step": "可复用模板的一步", "share": 0.1, "what": "这一步要做到什么（不写这一集的具体人名）"}}],
  "remix": ["拿这个模板做新剧/换皮的 2-4 个方向，每条一句话"]
}}
formula 的 share 是这一步占全集时长的比例，加起来约等于 1。"""


SERIES_CAST_PROMPT = """一部短剧有好几集，每集是分开拆解的，所以同一个角色在不同集里可能用了不同的 id。请把同一个人合并起来。
图是角色头像表：每格左上角的标签就是下面列表里的「标签」。
角色列表（标签|身份|重要度|出镜镜头数|外貌|名字线索）：
{rows}

判断依据：脸和标志性造型、名字（人名卡、台词里被叫的名字、身份）、剧情位置（同一个"男主"在每集都在）。
换了衣服、换了发型仍是同一个人；只是身份相同的不同路人（"服务员"）不要合并。群众（crowd）不用管。
只返回 JSON：{{"characters": [{{"id": "整部剧用的英文小写 id", "name": "中文名或称呼", "role": "身份", "members": ["标签", "标签"]}}]}}
每个标签只能出现在一个角色里；没有和别人合并的标签也要单独列出来。"""


SERIES_PROMPT = """你是短剧总编剧，给一整部剧做跨集拆解，读者是要复用这套打法的编剧和制片。
剧名：{title}，共 {n} 集，类型：{format}。
整部剧的角色（id|名字|身份|出现在哪几集|总出镜秒数）：
{cast}
逐集（集号|时长|剧情|开头钩子|结尾钩子|节拍序列）：
{episodes}
代码量出来的逐集数据（集号|时长|镜头数|爽点数|第一个爽点在几秒|有无结尾钩子）：
{metrics}

要求：每个判断都要落到具体集号和事件上。
只返回 JSON：
{{
  "logline": "整部剧一句话卖点",
  "main_conflict": "贯穿全剧的主冲突",
  "arcs": [{{"id": "角色 id", "arc": "这个人从哪里走到哪里", "turns": [{{"ep": 集号, "what": "转折"}}]}}],
  "threads": [{{"what": "伏笔/悬念/秘密", "planted": 埋下的集号, "paid": 回收的集号或 null, "note": "怎么埋、怎么收"}}],
  "links": [{{"ep": 集号, "end_hook": "这一集结尾留的钩子", "picked_up": "下一集开头怎么接的；没接写'没接'"}}],
  "paywall": [{{"ep": 集号, "why": "适合做付费卡点的理由（钩子强度、悬念）"}}],
  "pacing": ["跨集节奏的判断：哪几集拖、哪几集密、爽点分布是否均匀，2-4 条"],
  "formula": [{{"stage": "阶段（如 第1-3集）", "what": "这一阶段的打法"}}],
  "tropes": ["全剧反复使用的套路"],
  "remix": ["复用这套打法的 2-4 个方向"]
}}"""
