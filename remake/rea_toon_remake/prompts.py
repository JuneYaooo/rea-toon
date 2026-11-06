"""Prompts of the remake: cast sheets, keyframe edits and checks, video prompts, clip checks."""

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
3. 有没有新增、删除或交换人物；{places}
4. 被替换角色的表情和情绪是否和图1 一致。
另外判断 tiny：除群众/路人之外的被替换角色，在图1 中是否都小到看不清五官（或根本看不到）；如果只替换了群众，则群众全是远处小人时为 true。只要有一个主要角色看得清脸，tiny 就是 false。
但如果新角色不是人（动物、机器人等），图1 里看得见这个角色的手、脚、腿或身体轮廓时 tiny 一定是 false：换成动物后这些部位也必须变成动物的。
如果新角色不是人（动物、机器人等），图2 里任何位置（包括远处背景的顾客、路人、服务员的手）出现人类的脸、手、皮肤，ok 一定是 false。
只输出 JSON：{{"ok": true 或 false, "tiny": true 或 false, "problems": ["具体问题，写清楚应该怎样，例如：新人在图1 中只占画面中心很小的一点，图2 把他们放大到占画面三分之一"]}}"""


LAST_FRAME_HINT = "\n- 图{n} 是同一镜头已经改好的首帧：新画面中角色的长相、服装、画风必须与图{n}完全一致。"

# Same scene, another shot already edited (the scene's anchor): one look per scene instead of one per shot.


SCENE_REF_HINT = ("\n- 图{n} 是同一场戏里已经改好的另一个镜头（第 {anchor} 镜）：{names}的长相、毛色/发色/发型、体型、标志性特征"
                  "{dress}必须和图{n}里一模一样；构图、机位、姿势、表情、背景只以图1为准，不要照抄图{n}的画面。")


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


RESTYLE_MOTION_PROMPT = """图1 是改编后这一镜的首帧（如有图2，是尾帧）。原片这一镜的动作描述是：
{motion}
改编把场景、道具、主体按这个对应关系换掉了（只是总表，这一镜里不一定都有）：{style}
请把动作描述改写成改编后的版本：动作、手势、运镜、节奏、先后顺序完全不变；把描述里提到的原片物件、场景、角色换成图里对应的新东西；
只写这一镜画面里有的东西，不要提别的镜头的场景，也不要再出现任何原片的物件名。
只输出 JSON：{{"motion": "改写后的动作描述"}}"""

# Who says each line. Shot-level analysis sees only stills and allows one speaker per shot: back-and-forth dialogue
# and off-screen voices then move the wrong mouth. The whole script gives the context to tell who is talking.


CLIP_CHECK_PROMPT = """这些图按时间顺序取自生成视频的同一个镜头（时长 {duration:.1f}s，第一张是已审过的新首帧，代表正确的样子）。
这一镜的角色应该是：{who}
这一镜的台词（相对镜头开头的时间、说话人）：{lines}
检查，只输出 JSON：
1. identity：有没有角色中途变了样（物种、品种、长相、服装变成另一个人），或者出现了原片的人（人脸、人手、人腿）{nonhuman}？
2. mouth：在每句台词的时间里，说话人的嘴在动吗？说话人在画外或背对镜头时，画面里其他角色的嘴应该闭着；
   标着"内心独白"或"旁白"的台词，画面里所有人（包括这句的说话人）的嘴都应该闭着；
   有没有"A 的台词却是 B 的嘴在动"或该说话的人没张嘴？没有台词就不用查。
3. gaze：原片里的朝向和视线是：{gaze}。生成的画面里谁的脸转错了方向、看错了人？没有要求就不用查。
运镜造成的大小、角度变化和运动模糊是正常的，不算问题。
{{"identity_ok": true 或 false, "identity": "具体哪张图谁变成了什么，没有就空",
  "mouth_ok": true 或 false, "mouth": "具体说明，没有就空",
  "gaze_ok": true 或 false, "gaze": "具体说明，没有就空"}}"""


# ---- deep breakdown (breakdown.py): what a remake must keep, worked out before any remake work ----


ORIGINAL_FACE_NOTE = "\n脸必须是原创的：不要像任何真实存在的明星、演员、网红或公众人物。"


LIKENESS_PROMPT = """这是一部 AI 短剧里一个原创角色的设定图（角色：{name}）。平台审核会拒绝"长得像真实明星/公众人物"的角色。
只判断风险，不需要也不要说出像谁：这张脸是不是一眼就会让很多观众联想到某位真实存在的知名人物（脸型、五官组合、标志性发型妆容都很接近）？
大众脸、常见的帅哥美女长相不算；只有"明显像某个具体的人"才算。
只输出 JSON：{{"risk": true 或 false, "reason": "一句话：哪些特征让人联想到真人（不写名字），没有风险就空"}}"""


# Staging of one shot (breakdown.staging): camera angle, where faces point and who looks at whom.
# Shot analysis gives positions only; a regenerated clip turns heads and flattens a low-angle hero shot.


EXPRESSION_SHEET_PROMPT = """角色表情设定图（expression sheet），{style}。图1 是角色「{name}」的设定图。
画同一个角色的 {n} 个头像（胸口以上、正面或 3/4 侧面），排成整齐的网格，纯浅灰背景，每格下方用小字标表情名：
{items}
要求：每一格都是图1 里同一个人：脸型、五官比例、发型、发色、肤色、服装领口完全一样，只有表情不同；表情清楚到位，不夸张变形。"""


EXPRESSION_REF_HINT = ("\n- 图{n} 是「{name}」的表情设定图：这一帧 {name} 的表情是「{expr}」，照表情图里这一格的眉眼和嘴来画；"
                       "脸型五官仍然照设定图，不要因为表情变成另一个人。")
