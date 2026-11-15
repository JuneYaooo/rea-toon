"""English for the screenplay and the shot script (rea_toon/script.py)."""

T = {
    # scene headings: the stored 日/夜/晨/昏 and 内/外
    "日": "DAY", "夜": "NIGHT", "晨": "MORNING", "昏": "DUSK",
    "内": "INT.", "外": "EXT.",
    "（回忆）": "(FLASHBACK)",
    # speakers and their tags
    "旁白": "Narrator",
    "OS": "V.O.",
    "内心OS": "thinking",
    "{who}（{tag}）": "{who} ({tag})",
    "（{mood}）": "({mood}) ",
    "{who}：": "{who}: ",
    "{who}：{text}": "{who}: {text}",
    # lists
    "、": ", ",
    "无": "none",
    # cast
    "主角": "Lead", "配角": "Supporting", "龙套": "Extra",
    "出场 {n} 集": "in {n} ep.",
    "{n} 镜": "{n} shots",
    "第 {ep} 集「{text}」": "Ep. {ep} “{text}”",
    "配音样本（原片人声轨）：{tip}": "Voice sample (from the source's vocal track): {tip}",
    "配音样本 {s:g}s：{said}": "Voice sample {s:g}s: {said}",
    "（配音样本 {src}）": " (voice sample {src})",
    "- **{name}**：{role}": "- **{name}**: {role}",
    # the screenplay
    "人物": "Cast",
    "人物：{people}": "Cast: {people}",
    "第 {ep} 集": "Episode {ep}",
    "梗概：{text}": "Synopsis: {text}",
    "△ {text}": "{text}",
    "【字幕】{text}": "SUPER: {text}",
    "剧本 · 共 {n} 集": "Screenplay · {n} episodes",
    "分镜脚本 →": "Storyboard →",
    "{title} · 剧本": "{title} · Screenplay",
    # the shot script
    "{title} · 分镜脚本": "{title} · Storyboard",
    "{eps} 集 · {shots} 镜。动效：全动 / 局部动 / 静图推拉，后面是运镜和特效；"
    "音效是从原片音轨里听出来的提示音，仅供参考；左边红线是必保镜头，镜号旁的小字是这一镜的戏剧作用。"
    "点缩略图播原片的这一镜。":
        "{eps} episodes · {shots} shots. Motion: full motion / partial motion / still push-pull, then the camera move "
        "and effects; sound effects are cues heard on the source's audio track, a hint only; a red bar on the left marks "
        "a must-keep shot, the small words by the shot number are its dramatic role. Click a thumbnail to play that shot "
        "of the source.",
    "角色设定": "Characters",
    "← 剧本": "← Screenplay",
    "镜号": "Shot", "画面": "Frame", "景别": "Size", "画面描述": "Description",
    "台词 / 旁白 / OS": "Dialogue / V.O.", "音效": "SFX", "动效": "Motion", "时长": "Dur.",
    "{t} 起 · {n} 镜": "from {t} · {n} shots",
    "第 {ep} 集 镜 {no}": "Ep. {ep} · Shot {no}",
    "点开播这一镜": "Click to play this shot",
    # shot sizes
    "大特写": "Extreme close-up", "特写": "Close-up", "近景": "Medium close-up", "中近景": "Medium close-up",
    "中景": "Medium shot", "全景": "Full shot", "远景": "Wide shot", "大远景": "Extreme wide shot", "航拍": "Aerial",
    # angles (breakdown.staging.ANGLE_CN)
    "平视": "Eye level", "俯拍": "High angle", "仰拍": "Low angle", "斜角": "Dutch angle", "主观视角": "POV",
    "过肩": "Over the shoulder",
    # dramatic roles (story.ROLE_CN)
    "钩子": "Hook", "铺垫": "Setup", "冲突": "Conflict", "反转": "Reversal", "揭露": "Reveal", "高潮": "Climax",
    "爽点": "Payoff", "结尾钩子": "Cliffhanger", "反应": "Reaction", "转场": "Transition", "普通": "Normal",
    # motion kinds and the camera moves the breakdown picks from
    "全动": "Full motion", "局部动": "Partial motion", "静图推拉": "Still push/pull", "静图": "Still",
    "推": "Push in", "拉": "Pull out", "摇": "Pan", "移": "Track", "跟": "Follow", "升": "Crane up", "降": "Crane down",
    "环绕": "Orbit", "手持": "Handheld", "快速冲刺": "Rush in",
    # sound effects (SFX_CN, SFX_ON)
    "叮": "Ding", "嗖": "Whoosh", "啵": "Pop", "打击": "Hit", "轰": "Boom", "咔": "Click", "上扬": "Riser",
    "重击": "Impact", "刺音": "Sting", "打字": "Typing",
    "贴纸": "sticker", "花字": "text pop", "标注": "label",
    "{what}（{on}）": "{what} ({on})",
}
