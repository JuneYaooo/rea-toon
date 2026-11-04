"""All model prompts in one place."""

CAST_PROMPT = """你在给一部 AI 短剧做"剧本逆向"。下面几张图是这条视频的分镜联系表：每个格子是一个镜头的中间帧，左上角白底黑字是镜头编号。
每个镜头的字幕（OCR，可能有误）：
{subtitles}

任务：
1. 用 2-4 句话概括剧情、笑点/爽点和情绪走向。
2. 判断整体画风（如：写实电影感 AI 短剧 / 3D 动画 / 国漫 等）和色调。
3. 列出所有"可识别的角色"：同一个人在多个镜头出现要合并成一个角色。群众演员不用逐个列，合并成 crowd。
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

SHEET_PROMPT = """角色设定图（character reference sheet），{style}。
同一角色画三次：左边正面全身、中间正面半身特写（看清脸和发型）、右边 3/4 侧面半身。纯浅灰背景，没有文字、没有标注。
角色：{name}。{look}
服装：{costume}
要求：五官、发型、服装在三个视图里完全一致；比例自然；高清细节。"""

CROWD_SHEET_PROMPT = """群像设定图，{style}。
十几个各不相同的角色站成前后两排、全身可见，用来统一一大群路人/宾客的造型：{name}。{look}
服装：{costume}
纯浅灰背景，没有文字、没有标注；每个角色造型都不同，但整体风格统一；高清细节。"""

SHEET_FROM_IMAGE_PROMPT = """以图1中的角色为准，画一张角色设定图（character reference sheet），{style}。
同一角色画三次：左边正面全身、中间正面半身特写、右边 3/4 侧面半身。纯浅灰背景，没有文字。
保持图1角色的脸、发型、标志性特征完全一致。{extra}
服装：{costume}"""

FRAME_SWAP_PROMPT = """图1 是一部短剧的原始画面。请在图1基础上做"换角色"编辑，输出一张新的画面：
- 严格保持图1的构图、机位、景别、透视、光线、色调、背景、道具和其他所有人物不变；亮度和曝光也和图1一样（图1是暗的、逆光的或在转场里，新画面也一样暗）。
{swaps}
- 被替换的角色保持图1里原来的姿势、动作、朝向、表情和情绪（{emotion}），只替换身份外貌{costume_rule}。
- 去掉画面上后期叠加的字幕、标题、水印和台标（场景里真实存在的文字保留）。
{style_rule}
输出与图1相同比例的完整画面。"""

FRAME_RESTYLE_PROMPT = """图1 是一部短剧的原始画面。请把整个画面重绘为：{style}。
- 保持图1的构图、机位、景别、人物位置、姿势、动作、表情、情绪（{emotion}）和场景元素一一对应。
{swaps}
- 去掉画面上后期叠加的字幕、标题、水印和台标。
输出与图1相同比例的完整画面。"""

FRAME_CHECK_PROMPT = """图1 是短剧的原始画面，图2 是把其中角色换成新形象后的画面。被替换的角色：{who}。
换脸、换发型发色、妆容、服装按设定变化，以及去掉了后期叠加的字幕/水印，都是预期的，不算问题。只检查图2 是否保住了图1 的画面：
1. 被替换角色在图2 中的位置、大小（占画面的比例）、朝向、姿势是否和图1 一致（远处的小人不能被放大）；
2. 图1 里的画面元素（镜头框/取景器等叠加元素、前景人物、道具、背景、景别）是否都还在、位置一致；
3. 有没有新增、删除或交换人物；
4. 被替换角色的表情和情绪是否和图1 一致。
另外判断 tiny：除群众/路人之外的被替换角色，在图1 中是否都小到看不清五官（或根本看不到）；如果只替换了群众，则群众全是远处小人时为 true。只要有一个主要角色看得清脸，tiny 就是 false。
但如果新角色不是人（动物、机器人等），图1 里看得见这个角色的手、脚、腿或身体轮廓时 tiny 一定是 false：换成动物后这些部位也必须变成动物的。
只输出 JSON：{{"ok": true 或 false, "tiny": true 或 false, "problems": ["具体问题，写清楚应该怎样，例如：新人在图1 中只占画面中心很小的一点，图2 把他们放大到占画面三分之一"]}}"""

LAST_FRAME_HINT = "\n- 图{n} 是同一镜头已经改好的首帧：新画面中角色的长相、服装、画风必须与图{n}完全一致。"

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

VIDEO_PROMPT = """{motion}
景别：{shot_size}；运镜：{camera_move}。整体情绪：{emotion}。
{performance}{speaking}画面保持首帧的人物长相、服装和画风一致，表演力度和原片一致，不要出现字幕和文字。"""

# Self-hosted H3 Ref2VA: the original shot drives motion/camera/timing, the new keyframes drive identity.
# (English framing follows the deployment's verified action-replacement prompts; faces in <Video 1> are blurred.)
REF_VIDEO_PROMPT = """{pictures}
<Video 1> is ONLY the motion reference: copy its camera framing, camera movement, scene layout, every person's position, body motion, head turns, gestures, mouth timing and rhythm, moment by moment. The faces in <Video 1> are blurred on purpose; never reconstruct or copy the original actors' faces or identities.
{replace}
<Audio 1> is the synchronized voice track of <Video 1>{speech}. No text, subtitles, logo, or watermark.
以下是这一镜的表演说明（以参考图里的新角色来演）：
{base}"""

# Clips that follow the original shot (Ref2VA) can let the original people "grow back" mid-clip: faces are blurred
# in the reference, but clothes, hair and build are still copied. The new first frame is the ground truth.
CLIP_LEAK_PROMPT = """图1 是复刻短剧这一镜已经审过的新首帧（正确的样子），图2、图3 是生成视频的中间帧。
这一镜要换掉的角色：{who}。
请检查图2、图3 里的人是否都还是图1 那批角色（长相、服装、物种和画风一致）。重点看：有没有人变回了另一种样子，
例如图1 是神仙妖怪/古装，图2 却出现了穿现代便装、西装的普通人；或者新角色的脸、发型变回了别人。
镜头运动导致人物大小、角度、远近变化，以及运动模糊，都是正常的，不算问题。
只输出 JSON：{{"leak": true 或 false, "problems": "有问题时写清楚哪一帧、画面哪个位置的谁变成了什么样"}}"""


SUBS_PROOFREAD_PROMPT = """下面是用 OCR 从一部短剧画面里识别出的字幕，按时间顺序编号。OCR 常把形近字认错，
例如 首→百、自→目、丢→去、糟→禮、帅→师、吧→叫。剧情：{summary}

请只修正 OCR 认错的字（形近字、漏字、多出的杂字），不要改写、润色或统一说法，口语和语气词保持原样；
拿不准的不改。只输出 JSON：{{"fixes": [{{"i": 编号, "text": "改正后的整句"}}]}}，没有要改的输出 {{"fixes": []}}。

{lines}"""

# Restyled remakes: the shot's action text names the original things; rewrite it for the new keyframes.
RESTYLE_MOTION_PROMPT = """图1 是改编后这一镜的首帧（如有图2，是尾帧）。原片这一镜的动作描述是：
{motion}
改编把场景、道具、主体按这个对应关系换掉了（只是总表，这一镜里不一定都有）：{style}
请把动作描述改写成改编后的版本：动作、手势、运镜、节奏、先后顺序完全不变；把描述里提到的原片物件、场景、角色换成图里对应的新东西；
只写这一镜画面里有的东西，不要提别的镜头的场景，也不要再出现任何原片的物件名。
只输出 JSON：{{"motion": "改写后的动作描述"}}"""
