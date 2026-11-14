"""Code-only checks of a broken-down story: generation risks per shot and consistency problems (lint).
No model calls; cheap to redo after any edit. lint errors block `lock`."""

from __future__ import annotations

from .refine import identity_pairs
from .story import differs, has_look, lines_of, look_in, look_parts, look_split, scene_of, story_day
from .subtitles import NARRATOR, line_mode


RISK_CN = {
    "offscreen_line": "画外音：说话的人不在画面里，画面里的嘴要闭着",
    "inner_line": "内心独白/旁白：声音是画面里的人，但嘴要闭着，只用表情演",
    "multi_speaker": "一镜多人说话：每句台词要对上各自的嘴",
    "entrance": "有人中途入画：首帧没有，后面才出现",
    "flash_in": "闪白/淡入开场：首帧几乎没有画面内容",
    "short": "不到 0.8 秒的短镜",
    "long": "超过 15 秒：视频模型一次生成不了",
    "big_move": "大运镜或画面变化大",
    "crowd": "有群众/路人",
    "many_people": "三个及以上角色同框",
    "disguise": "乔装/换装：同一个角色这一镜是另一套造型",
    "look_change": "这一镜里造型变了（摘假发、脱外套…）",
    "name_card": "人名字卡：不是台词，不能有嘴在动",
    "hands": "手部和道具动作（拿、递、推、挥…）",
    "back_view": "主角背对镜头",
    "scene_text": "画面里有场景文字（招牌/屏幕/文件）",
    "overlay_text": "有后期花字",
    "contact": "身体接触（扇、泼、抓、推、递），动作落在音效点上",
    "freeze": "镜头里有定格",
    "slow": "慢动作",
    "still_image": "静图推拉（动态漫画）：一张图在推拉",
    "partial_motion": "局部动：画面基本静止，只有一小块在动（眨眼、头发、嘴）",
    "vfx": "术法/粒子特效（剑气、灵光、法阵）",
    "panels": "多宫格分屏",
    "manga_marks": "漫符（汗滴、怒筋、闪光、速度线）",
    "bubble": "对话气泡",
}
_HAND_WORDS = ("拿", "握", "递", "推", "端", "签", "举", "挥", "抓", "接过", "倒酒", "掏", "翻", "指着", "拍")


def risks(story: dict, shot: dict, key: dict | None = None) -> list[str]:
    """What to watch in one shot, from the analysis alone (no model call)."""
    a = shot.get("analysis") or {}
    key = key or shot.get("key") or {}
    chars = [c for c in a.get("characters") or [] if c.get("id")]
    ids = {c["id"] for c in chars}
    by_id = {c["id"]: c for c in story["characters"]}
    lines = lines_of(shot)
    out = []
    if any(line_mode(x) == "off" for x in lines):
        out.append("offscreen_line")
    if any(line_mode(x) in ("inner", "narration") for x in lines):
        out.append("inner_line")
    if len({x.get("speaker") for x in lines if x.get("speaker")}) >= 2:
        out.append("multi_speaker")
    first, last = a.get("in_first"), a.get("in_last")
    if isinstance(first, list) and isinstance(last, list) and set(last) - set(first) - {"crowd"}:
        out.append("entrance")
    if key.get("transition_in") in ("white_flash", "fade", "dissolve"):
        out.append("flash_in")
    if shot["duration"] < 0.8:
        out.append("short")
    if shot["duration"] > 15:
        out.append("long")
    if a.get("change") == "large":
        out.append("big_move")
    if any("crowd" in i for i in ids):
        out.append("crowd")
    if len({i for i in ids if "crowd" not in i}) >= 3:
        out.append("many_people")
    if any(shot["index"] in (o.get("shots") or []) for c in story["characters"] for o in c.get("outfits") or []):
        out.append("disguise")
    sc = scene_of(story, shot["index"])
    if sc and any(int(c.get("shot", 0)) == shot["index"] for c in sc.get("changes") or []):
        out.append("look_change")
    if any(x.get("card") for x in shot.get("subtitles") or []):
        out.append("name_card")
    if any(w in str(c.get("action", "")) for c in chars for w in _HAND_WORDS):
        out.append("hands")
    if any(c.get("visibility") == "back" and by_id.get(c["id"], {}).get("importance") == "lead" for c in chars):
        out.append("back_view")
    if shot.get("contacts"):
        out.append("contact")
    if shot.get("freeze"):
        out.append("freeze")
    if any("慢" in str(f) for f in key.get("fx") or []):
        out.append("slow")
    kinds = {t.get("kind") for t in shot.get("screen_text") or []}
    if kinds & {"scene", "unknown"}:
        out.append("scene_text")
    if "overlay" in kinds:
        out.append("overlay_text")
    if "bubble" in kinds:
        out.append("bubble")
    kind = (shot.get("motion") or {}).get("kind")
    if kind in ("still", "pan"):
        out.append("still_image")
    elif kind == "partial":
        out.append("partial_motion")
    st = shot.get("staging") or {}
    if st.get("effects"):
        out.append("vfx")
    if (st.get("panels") or 1) > 1:
        out.append("panels")
    if st.get("marks"):
        out.append("manga_marks")
    return out


# ---- lint ----

def _side(where: str) -> int:
    """-1 left, 1 right, 0 centre / not said, from a position like 画面左侧前景 or 画面中央偏右."""
    w = str(where or "")
    left, right = "左" in w, "右" in w
    return -1 if left and not right else 1 if right and not left else 0


def axis_flips(story: dict, near: int = 6) -> list[tuple[int, int, str, str]]:
    """Two people who swap screen sides between two nearby shots of the same scene that both show them (a jump over
    the 180° line, or a misread position): (earlier shot, later shot, the one on the left before, the other).
    Two-shots far apart are not compared: the camera has had time to move round on screen."""
    out, last = [], {}  # (scene, a, b) -> (shot, a is left of b)
    for s in story.get("shots") or []:
        sc = scene_of(story, s["index"])
        side = {c.get("id"): _side(c.get("where")) for c in (s.get("analysis") or {}).get("characters") or []
                if c.get("id") and "crowd" not in str(c.get("id"))}
        ids = sorted(k for k, v in side.items() if v)
        for n, a in enumerate(ids):
            for b in ids[n + 1:]:
                if side[a] == side[b]:
                    continue
                key, left = ((sc or {}).get("id"), a, b), side[a] < side[b]
                if key in last and last[key][1] != left and s["index"] - last[key][0] <= near:
                    i = last[key][0]
                    out.append((i, s["index"], *((a, b) if not left else (b, a))))
                last[key] = (s["index"], left)
    return out


def lint(story: dict) -> list[dict]:
    """Consistency problems of the breakdown. level error = must be fixed before lock; warn = look at it."""
    out = []
    add = lambda level, shot, msg: out.append({"level": level, "shot": shot, "msg": msg})
    ids = {c["id"] for c in story["characters"]}
    main = {c["id"] for c in story["characters"] if c.get("importance") != "extra" and "crowd" not in c["id"]}
    bd = story.get("breakdown") or {}
    if not bd:
        add("error", None, "还没做深度拆解（rea-toon redo breakdown）")
    unknown: dict[str, list[int]] = {}  # ids a shot names that the cast does not have
    asked: set = set()
    for s in story["shots"]:
        a = s.get("analysis") or {}
        i = s["index"]
        if not a or a.get("error"):
            add("error", i, f"逐镜拆解失败：{a.get('error', '没有结果')}")
            continue
        here = {c.get("id") for c in a.get("characters") or []}
        for x in lines_of(s):
            if x.get("unsure") and x.get("speaker") and (x.get("start"), x.get("text")) not in asked:
                asked.add((x.get("start"), x.get("text")))  # a line across a cut sits in both shots: asked once
                add("warn", i, f"台词「{x['text']}」的说话人拿不准（现在标的是 {x['speaker']}，看了图也定不下来）：对着原片确认，"
                               f"改好后删掉这句的 unsure")
            if not x.get("speaker"):
                add("error", i, f"台词「{x['text']}」没有说话人")
            elif x["speaker"] not in ids | {NARRATOR}:
                add("error", i, f"台词「{x['text']}」的说话人 {x['speaker']} 不在角色表里")
            elif x.get("on_screen") and x["speaker"] not in here:
                add("warn", i, f"台词「{x['text']}」标成 {x['speaker']} 在画面里说，但这一镜没标出 {x['speaker']}")
        for c in here - ids - {"crowd", None}:
            unknown.setdefault(c, []).append(i)
        perf = s.get("performance")
        if here & main and (not perf or perf.get("error")) and story.get("depth") != "fast":
            add("error", i, "有主要角色但没有表演拆解（逐秒表情/口型）")
        if s["duration"] > 15 and not story.get("cuts"):
            add("warn", i, f"{s['duration']:.1f}s 超过 15 秒：用 analyze --cuts 切开")
        if s.get("contacts_error"):
            add("warn", i, "身体接触拆解失败：这一镜的接触时间没有，重跑 rea-toon redo breakdown")
        for t in s.get("screen_text") or []:
            if t.get("kind") == "unknown":
                add("warn", i, f"画面里的字「{t['text']}」没分出是场景文字还是花字（在 story.json 的 screen_text 里填 kind）")
        if bd and (not s.get("key") or s["key"].get("missing")):
            add("error", i, "没有看点拆解（role/priority/keep）")
        elif bd and s["key"].get("priority") == "must" and not s["key"].get("function"):
            add("warn", i, "必保镜头没有写剧情功能（这一镜做成了什么、观众什么感觉）")
        if bd:
            sc = scene_of(story, i)
            for c in sorted(here & main):
                if sc and not look_in(story, c, i):
                    add("warn", i, f"{c} 在第 {sc['id']} 场没有造型描述")
    scenes = bd.get("scenes") or []
    for sc in scenes:
        if sc.get("error"):
            add("error", None, f"第 {sc['id']} 场造型拆解失败：{sc['error']}")
    # same story day, next scene with this person: an outfit that changes in between is either the story (a costume
    # change the scene shows) or a misread that would put two outfits on one person within the day
    last: dict[tuple[str, str], tuple[dict, str]] = {}
    for sc in scenes:
        for cid in sorted(main):
            if not has_look(sc, cid) or not story_day(sc):
                continue
            start = look_split((sc.get("looks") or {}).get(cid))["wear"] if cid in (sc.get("looks") or {}) else ""
            prev = last.get((story_day(sc), cid))
            if prev and start and prev[1] and differs(start, prev[1]):
                add("warn", sc["shots"][0], f"{cid} 在同一剧情日 {sc.get('day')} 里，第 {prev[0]['id']} 场结尾穿「{prev[1]}」，"
                                            f"第 {sc['id']} 场开头穿「{start}」：确认是剧情里换了衣服，不是拆解看错")
            end = look_parts(story, cid, sc["shots"][1]).get("wear", "")
            if end:
                last[(story_day(sc), cid)] = (sc, end)
    for i, j, a, b in axis_flips(story):
        add("warn", j, f"{a} 和 {b} 在镜{i}是{a}左{b}右，到镜{j}左右换了（同一场）：可能越轴，"
                       f"可能是原片就这样拍，也可能是拆解看错了位置")
    chars = [c for c in story["characters"] if c.get("importance") != "extra" and "crowd" not in c["id"]]
    distinct = [sorted(x) for x in (story.get("refined") or {}).get("distinct") or []]
    for (a, b), clues in identity_pairs(story, chars).items():
        if sorted((a, b)) in distinct:  # a model already looked at both and said they are different people
            continue
        add("warn", None, f"{a} 和 {b} 可能是同一个人（乔装/换装），确认后在 story.json 合并：{clues[0]}")
    for c, at in sorted(unknown.items(), key=lambda kv: kv[1][0]):
        add("warn", at[0], f"画面里的 {c}（镜{'、'.join(map(str, at))}）不在角色表里：是有戏的人就加进角色表，"
                           f"是尸体/物件/背景就不用管")
    if story.get("depth") == "fast":  # enough to read the drama; deepen adds the acting and contact passes
        add("error", None, "快拆没做逐秒表演和身体接触拆解：只看拆解不用管；要补就跑 rea-toon deepen")
        out[-1]["for"] = "deepen"  # nothing to fix for someone who only reads the breakdown
    must = sum(1 for s in story["shots"] if (s.get("key") or {}).get("priority") == "must")
    if bd and must > 0.35 * len(story["shots"]):
        add("warn", None, f"必保镜头 {must} 个，太多就等于没有重点")
    return out
