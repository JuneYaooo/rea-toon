"""Deep breakdown: what a remake must keep, worked out (and reviewed) before any remake work.

analyze.py perceives: shots, subtitles, cast, per-shot stills, acting beats, who says each line, one id per person.
This pass reads the drama as a whole, the way a director would before shooting it again:

- style_bible  the visual rules of the whole film (live-action AI drama vs anime/manga: rendering, design, recurring FX)
- scenes       continuous scenes: where, when, who is there and each one's look *in that scene* (outfit, wig, face mask,
               props, stains) plus changes inside the scene; the remake dresses characters from this, not from one
               global appearance (the brother in a hoodie and face mask, not in the pink dress of another scene)
- beats        the dramatic spine (hook, reversals, reveals, payoff, cliffhanger) and per shot: role, priority, the
               1-3 things that must survive the remake, FX and how the shot is entered (white flash, fade...)
- risks        generation risks derived by code (off-screen lines, two speakers, people walking in, flash cuts,
               disguises, look changes, hands on props...): the remake plans these shots more carefully
- lint         consistency problems; errors block `lock`

Everything goes into story.json, plus breakdown.md / breakdown.html for review. The remake only runs on a locked
story (`rea-toon lock`): the analysis is the contract, the remake never re-analyses anything.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .vlm import VLM
from .prompts import BEATS_PROMPT, SCENE_LOOKS_PROMPT, SCENES_PROMPT
from .subtitles import MODE_CN, NARRATOR, line_mode

LOG = logging.getLogger(__name__)

ROLES = ("hook", "setup", "conflict", "reversal", "reveal", "climax", "payoff", "cliffhanger", "reaction",
         "transition", "normal")
ROLE_CN = {"hook": "钩子", "setup": "铺垫", "conflict": "冲突", "reversal": "反转", "reveal": "揭露", "climax": "高潮",
           "payoff": "爽点", "cliffhanger": "结尾钩子", "reaction": "反应", "transition": "转场", "normal": "普通"}
PRIORITY_CN = {"must": "必保", "high": "重要", "normal": "普通"}
TRANSITIONS = ("cut", "white_flash", "fade", "dissolve", "whip", "match")
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
    "scene_text": "画面里有场景文字（招牌/屏幕/文件）：关键帧要原样保留、字要清楚",
    "overlay_text": "有后期花字：关键帧里去掉，成片按原位置、原时间重画",
    "contact": "身体接触（扇、泼、抓、推、递）：视频模型最容易崩，动作还要落在原音轨的音效点上",
    "freeze": "镜头里有定格：生成的视频不会停住，拼片时冻帧",
    "slow": "慢动作：生成时要按慢动作演，否则和原音轨的节奏对不上",
    "still_image": "静图推拉（动态漫画）：原片是一张图在推拉，复刻直接用新关键帧推拉，不调视频模型",
    "partial_motion": "局部动：画面基本静止，只有一小块在动（眨眼、头发、嘴），视频模型容易加多余动作",
    "vfx": "术法/粒子特效（剑气、灵光、法阵）：颜色和从谁身上发出要保住，换角后跟着新角色走",
    "panels": "多宫格分屏：格子边框和每格的内容要保持，视频模型容易合成一个画面",
    "manga_marks": "漫符（汗滴、怒筋、闪光、速度线）：关键帧里要画出来，位置和原片一样",
    "bubble": "对话气泡：气泡和里面的字要原样保留",
}
_HAND_WORDS = ("拿", "握", "递", "推", "端", "签", "举", "挥", "抓", "接过", "倒酒", "掏", "翻", "指着", "拍")


# ---- text views of the story, shared by the prompts ----

def _lines_of(shot: dict) -> list[dict]:
    return [x for x in shot.get("subtitles") or [] if not x.get("card") and x.get("speaker") != "title"]


def _said(x: dict) -> str:
    """How a line is spoken, as a short tag: (画外) / (内心独白) / (旁白); nothing for a line said on camera."""
    tag = MODE_CN[line_mode(x)]
    return f"({tag})" if tag else ""


def _cast_text(story: dict) -> str:
    rows = []
    for c in story["characters"]:
        row = f"- {c['id']}：{c.get('role', '')}（{c.get('importance', '')}），{str(c.get('appearance', ''))[:100]}"
        for o in c.get("outfits") or []:
            row += f"；另一套造型（镜头 {o.get('shots')}，{o.get('role', '')}）：{str(o.get('appearance', ''))[:80]}"
        rows.append(row)
    return "\n".join(rows)


def _shot_row(s: dict, emotion: bool = False) -> str:
    a = s.get("analysis") or {}
    who = ";".join(f"{c.get('id')}:{c.get('where', '')},{c.get('action', '')}" + (f",{c.get('expression', '')}" if emotion else "")
                   for c in a.get("characters") or []) or "无人"
    lines = " / ".join(f"{x.get('speaker', '?')}{_said(x)}：{x['text']}" for x in _lines_of(s)) or "无台词"
    cols = [str(s["index"]), f"{s['start']:.1f}-{s['end']:.1f}s", a.get("shot_size", ""),
            a.get("camera_move", "") if emotion else a.get("scene", ""), who, lines]
    if emotion:
        cols.append(a.get("emotion", ""))
    return "|".join(str(c) for c in cols)


def _table(story: dict, shots: list[dict] | None = None, emotion: bool = False) -> str:
    return "\n".join(_shot_row(s, emotion) for s in shots or story["shots"])


# ---- VLM passes ----

def _fix_scenes(scenes: list[dict], n: int) -> list[dict]:
    """Scenes as given by the model, made into ordered, gap-free, non-overlapping ranges over shots 1..n."""
    out, nxt = [], 1
    for sc in sorted((s for s in scenes if isinstance(s.get("shots"), list) and len(s["shots"]) == 2),
                     key=lambda s: int(s["shots"][0])):
        a, b = max(int(sc["shots"][0]), nxt), min(int(sc["shots"][1]), n)
        if b < a:
            continue
        if a > nxt and out:  # a gap: the previous scene runs on
            out[-1]["shots"][1] = a - 1
        elif a > nxt:
            a = 1
        out.append({**sc, "shots": [a, b]})
        nxt = b + 1
    if not out:
        return [{"id": "S1", "shots": [1, n], "location": "", "time": "", "present": []}]
    out[-1]["shots"][1] = n
    for k, sc in enumerate(out, 1):
        sc["id"] = f"S{k}"
    return out


def scene_of(story: dict, index: int) -> dict | None:
    for sc in (story.get("breakdown") or {}).get("scenes") or []:
        if sc["shots"][0] <= index <= sc["shots"][1]:
            return sc
    return None


LOOK_PARTS = ("wear", "hair", "face", "props")


def _day_tag(sc: dict) -> str:
    return (f" · 剧情日 {sc['day']}" if sc.get("day") else "") + (" · 回忆" if sc.get("flashback") else "")


def _parts(v) -> dict:
    if isinstance(v, dict):
        return {k: str(v.get(k) or "").strip() for k in LOOK_PARTS}
    return {"wear": str(v or "").strip(), "hair": "", "face": "", "props": ""}  # older / hand-written plain text


def _day(sc: dict) -> str:
    """Story day of a scene (D1, N1, D2 …); flashbacks get their own key, they never share a look with the present.
    A flashback labelled only "回忆" has no day: two such scenes can be years apart."""
    import re
    d = str(sc.get("day") or "").strip()
    if sc.get("flashback"):
        return f"{d}*" if re.search(r"\d", d) else ""
    return d


def _differs(a: str, b: str) -> bool:
    """Two descriptions of an outfit that do not look like the same clothes (the model words one outfit many ways)."""
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() < 0.45


def _has(sc: dict, cid: str) -> bool:
    return cid in (sc.get("looks") or {}) or any(c.get("who") == cid for c in sc.get("changes") or [])


def look_parts(story: dict, cid: str, index: int) -> dict:
    """A character's look in one shot from the scene breakdown, as wear / hair / face / props ({} if unknown).
    Not described in this scene (only a hand or a back in frame, say): the look they ended the latest earlier
    scene of the same story day with, since nobody changes clothes off screen within a day without the story saying so."""
    sc = scene_of(story, index)
    if not sc:
        return {}
    if not _has(sc, cid):
        scenes = (story.get("breakdown") or {}).get("scenes") or []
        same = [x for x in scenes if x["shots"][1] < sc["shots"][0] and _day(x) and _day(x) == _day(sc) and _has(x, cid)]
        return look_parts(story, cid, same[-1]["shots"][1]) if same else {}
    look = _parts((sc.get("looks") or {}).get(cid))
    for ch in sorted(sc.get("changes") or [], key=lambda c: int(c.get("shot", 0))):
        if ch.get("who") == cid and int(ch.get("shot", 0)) <= index:
            new = _parts(ch if any(ch.get(k) for k in LOOK_PARTS) else ch.get("look"))
            look = {k: new[k] or (look[k] if k != "face" and k != "props" else new[k]) for k in LOOK_PARTS}
    return look


def look_in(story: dict, cid: str, index: int, dress: bool = False) -> str:
    """The look as one line. dress=True is what a remake carries over to a new character: clothes, face extras
    (face mask, wine) and props; hair only when it is a wig (the new character brings its own hair or fur)."""
    p = look_parts(story, cid, index)
    if not p:
        return ""
    keep = [p["wear"], p["hair"] if not dress or "假发" in p["hair"] else "", p["face"], p["props"]]
    return "；".join(x for x in keep if x)


def _scene_frames(story: dict, work: Path, sc: dict, k: int = 6) -> list[tuple[int, Path]]:
    """Frames that show this scene's people best: closest framing, spread over the scene."""
    order = ("特写", "近景", "中景", "全景")
    shots = [s for s in story["shots"] if sc["shots"][0] <= s["index"] <= sc["shots"][1]]
    peopled = [s for s in shots if (s.get("analysis") or {}).get("characters")] or shots
    if len(peopled) <= k:
        return [(s["index"], work / s["frames"]["mid"]) for s in peopled]
    rank = lambda s: next((i for i, w in enumerate(order) if w in str((s.get("analysis") or {}).get("shot_size", ""))), 9)
    step = len(peopled) / k
    picks = []
    for j in range(k):  # one per slice of the scene, the closest shot of that slice
        part = peopled[int(j * step):int((j + 1) * step)] or peopled[-1:]
        picks.append(min(part, key=rank))
    return [(s["index"], work / s["frames"]["mid"]) for s in picks]


def _one(v) -> str:
    p = _parts(v)
    return "，".join(f"{n}{p[k]}" for k, n in (("wear", ""), ("hair", "发型："), ("face", "脸上："), ("props", "道具："))
                    if p[k])


def scene_looks(story: dict, work: Path, sc: dict, vlm: VLM) -> dict:
    by_id = {c["id"]: c for c in story["characters"]}
    shots = [s for s in story["shots"] if sc["shots"][0] <= s["index"] <= sc["shots"][1]]
    here = list(dict.fromkeys(c.get("id") for s in shots for c in (s.get("analysis") or {}).get("characters") or []))
    here = [c for c in dict.fromkeys([*(sc.get("present") or []), *here]) if c in by_id]
    if not here:
        return {"looks": {}, "changes": []}
    frames = _scene_frames(story, work, sc)
    pics = "，".join(f"图{n} 是第 {i} 镜" for n, (i, _) in enumerate(frames, 1))
    cast = "\n".join(f"- {c}：{by_id[c].get('role', '')}（{str(by_id[c].get('appearance', ''))[:80]}）" for c in here)
    prompt = SCENE_LOOKS_PROMPT.format(sid=sc["id"], a=sc["shots"][0], b=sc["shots"][1], location=sc.get("location", ""),
                                       time=sc.get("time", ""), pics=pics, cast=cast, table=_table(story, shots))
    try:
        r = vlm.ask_json(prompt, [p for _, p in frames], max_tokens=4096, timeout=300)
    except Exception as exc:  # leave the scene without looks: lint reports it
        LOG.warning("第 %s 场造型拆解失败：%s", sc["id"], exc)
        return {"looks": {}, "changes": [], "error": str(exc)}
    looks = {k: _parts(v) for k, v in (r.get("looks") or {}).items() if k in by_id and v}
    changes = [{"shot": int(c["shot"]), "who": c["who"], **_parts(c if any(c.get(k) for k in LOOK_PARTS) else c.get("look"))}
               for c in r.get("changes") or [] if c.get("who") in by_id and str(c.get("shot", "")).isdigit()
               and sc["shots"][0] <= int(c["shot"]) <= sc["shots"][1]]
    return {"looks": looks, "changes": changes}


# ---- text in the picture (not subtitles) ----

TEXT_KINDS = ("scene", "bubble", "overlay", "aigc", "watermark")
_TEXT_CN = {"scene": "场景里的字", "bubble": "对话气泡", "overlay": "花字", "aigc": "AI 标识", "watermark": "水印"}


def _overlaps(a: list, b: list, pad: float = 0.01) -> bool:
    return not (a[2] < b[0] - pad or b[2] < a[0] - pad or a[3] < b[1] - pad or b[3] < a[1] - pad)


def _where(box: list) -> str:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return ("上" if cy < 0.33 else "下" if cy > 0.67 else "中") + ("左" if cx < 0.33 else "右" if cx > 0.67 else "")


def _ocr_shot(work: Path, shot: dict) -> list[dict]:
    """Text in the shot's first / mid / last frames that is not one of its subtitle lines (or name cards)."""
    from .subtitles import read_text, similar_text
    subs = [x["box"] for x in shot.get("subtitles") or [] if x.get("box")]
    subs += [b for x in shot.get("subtitles") or [] for b in x.get("boxes") or []]
    found: list[dict] = []
    for name in ("first", "mid", "last"):
        f = (shot.get("frames") or {}).get(name)
        if not f or not (work / f).is_file():
            continue
        for t in read_text(work / f):
            b = t["box"]
            # small text counts too: AIGC labels and corner marks are ~1% of the frame tall
            if len(t["text"]) < 2 or b[3] - b[1] < 0.01 or any(_overlaps(b, sb) for sb in subs):
                continue
            same = next((x for x in found if similar_text(x["text"], t["text"]) >= 0.6 and _overlaps(x["box"], b, 0.05)), None)
            if same:
                if name not in same["frames"]:
                    same["frames"].append(name)
            else:
                found.append({"text": t["text"], "box": [round(v, 4) for v in b], "frames": [name]})
    return found


def _text_color(img, box: list) -> str:
    """Glyph colour of a text box: the pixels that differ most from the box's border (its background)."""
    import numpy as np
    h, w = img.shape[:2]
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), max(int(box[2] * w), int(box[0] * w) + 2), max(int(box[3] * h), int(box[1] * h) + 2)
    crop = img[y0:y1, x0:x1].reshape(-1, 3).astype(float)
    ring = np.concatenate([img[y0, x0:x1], img[y1 - 1, x0:x1], img[y0:y1, x0], img[y0:y1, x1 - 1]]).astype(float)
    if not len(crop) or not len(ring):
        return "#FFFFFF"
    bg = np.median(ring, axis=0)
    d = np.linalg.norm(crop - bg, axis=1)
    fg = crop[d >= np.percentile(d, 75)]
    b, g, r = np.median(fg, axis=0).astype(int)
    return f"#{r:02X}{g:02X}{b:02X}"


def _overlay_span(work: Path, shot: dict, item: dict, step: float = 0.2) -> dict:
    """When a 花字 is on screen (OCR of its box every `step` s) and its colour: assembly redraws it there."""
    import cv2
    from .subtitles import read_text, similar_text
    cap = cv2.VideoCapture(str(work / "source.mp4"))
    seen, color = [], None
    t = shot["start"] + 0.02
    while t < shot["end"]:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        if not ok:
            break
        h, w = img.shape[:2]
        x0, y0, x1, y1 = item["box"]
        px, py = 0.03, 0.03
        crop = img[max(0, int((y0 - py) * h)):int((y1 + py) * h), max(0, int((x0 - px) * w)):int((x1 + px) * w)]
        if crop.size and any(similar_text(r["text"], item["text"]) >= 0.6 for r in read_text(crop, 0.6)):
            seen.append(t)
            color = color or _text_color(img, item["box"])
        t += step
    cap.release()
    if not seen:
        return {"t0": shot["start"], "t1": shot["end"], "color": "#FFFFFF"}
    return {"t0": round(max(shot["start"], seen[0] - step / 2), 2), "t1": round(min(shot["end"], seen[-1] + step / 2), 2),
            "color": color or "#FFFFFF"}


def screen_text(story: dict, work: Path, vlm: VLM, workers: int = 6) -> int:
    """Text in the picture besides the subtitles, per shot: scene text (signs, phone screens, documents) the keyframe
    edit must keep legible, post-production 花字 the edit drops and assembly redraws, watermarks that go.
    OCR is local; one VLM call per shot that has any text says which is which."""
    from .prompts import SCREEN_TEXT_PROMPT

    def one(s: dict) -> list[dict]:
        found = _ocr_shot(work, s)
        if not found:
            return []
        rows = "\n".join(f"{k}|{x['text']}|{_where(x['box'])}" for k, x in enumerate(found))
        frame = work / s["frames"][found[0]["frames"][0]]
        try:
            r = vlm.ask_json(SCREEN_TEXT_PROMPT.format(index=s["index"], texts=rows), [frame], max_tokens=1024, timeout=120)
        except Exception as exc:  # unknown kind: lint shows it, the remake treats it as scene text (kept)
            LOG.warning("第 %s 镜画面文字分类失败：%s", s["index"], exc)
            return [{**x, "kind": "unknown"} for x in found]
        kinds = {int(k["i"]): k.get("kind") for k in r.get("texts") or [] if str(k.get("i", "")).isdigit()}
        out = []
        for k, x in enumerate(found):
            kind = kinds.get(k) if kinds.get(k) in (*TEXT_KINDS, "noise") else "unknown"
            if kind == "noise":
                continue
            x["kind"] = kind
            if kind == "overlay":
                x.update(_overlay_span(work, s, x))
            out.append(x)
        return out

    with ThreadPoolExecutor(workers) as ex:
        for s, texts in zip(story["shots"], ex.map(one, story["shots"])):
            s["screen_text"] = texts
    return sum(len(s["screen_text"]) for s in story["shots"])


# ---- staging, contacts, freezes ----

ANGLES = ("eye", "high", "low", "dutch", "pov", "ots")
ANGLE_CN = {"eye": "平视", "high": "俯拍", "low": "仰拍", "dutch": "斜角", "pov": "主观视角", "ots": "过肩"}
FACING_CN = {"left": "画面左侧", "right": "画面右侧", "camera": "镜头", "away": "背对镜头", "down": "下方"}
CONTACT_WORDS = ("扇", "打了", "打向", "拍桌", "拍了", "摔", "推", "踢", "抱", "递", "砸", "泼", "撞", "亲吻", "吻",
                 "抓住", "抓着", "拽", "扯", "掐", "握住", "碰杯", "关门", "甩", "扔", "掀", "撕")


def staging(story: dict, work: Path, vlm: VLM, workers: int = 6) -> int:
    """Per shot: camera angle, which way each face points and who looks at whom; in the same call the things a comic
    drama draws on top of the people: effects (剑气、灵光、法阵 and their colour / source), manga marks, split panels."""
    from .prompts import STAGING_PROMPT

    def one(s: dict) -> dict | None:
        a = s.get("analysis") or {}
        people = [c for c in a.get("characters") or [] if c.get("id") and c.get("visibility") not in ("tiny",)]
        desc = "；".join(f"{c['id']}（{c.get('where', '')}）" for c in people) or "（没有看得清的角色）"
        try:
            r = vlm.ask_json(STAGING_PROMPT.format(index=s["index"], size=a.get("shot_size", ""), people=desc),
                             [work / s["frames"]["mid"]], max_tokens=1536, timeout=120)
        except Exception as exc:
            LOG.warning("第 %s 镜机位/视线拆解失败：%s", s["index"], exc)
            return None
        ids = {c["id"] for c in people} | {c["id"] for c in story["characters"]}
        ppl = {k: {"facing": v.get("facing") if v.get("facing") in FACING_CN else "",
                   "looking_at": str(v.get("looking_at") or "")}
               for k, v in (r.get("people") or {}).items() if k in ids and isinstance(v, dict)}
        fx = [{"what": str(e.get("what") or ""), "color": str(e.get("color") or ""),
               "from": str(e.get("from") or "") if str(e.get("from") or "") in ids else ""}
              for e in r.get("effects") or [] if isinstance(e, dict) and e.get("what")]
        try:
            panels = max(1, min(9, int(r.get("panels") or 1)))
        except (TypeError, ValueError):
            panels = 1
        st = {"angle": r.get("angle") if r.get("angle") in ANGLES else "eye", "ots_of": r.get("ots_of") or "",
              "people": ppl, "power": str(r.get("power") or "")}
        st.update({k: v for k, v in (("effects", fx), ("marks", [str(x) for x in r.get("marks") or [] if x]),
                                     ("panels", panels if panels > 1 else 0)) if v})
        return st if people or len(st) > 4 else None

    with ThreadPoolExecutor(workers) as ex:
        for s, st in zip(story["shots"], ex.map(one, story["shots"])):
            if st:
                s["staging"] = st
            else:
                s.pop("staging", None)
    return sum(1 for s in story["shots"] if s.get("staging"))


def contacts(story: dict, work: Path, vlm: VLM, workers: int = 6) -> int:
    """Physical contacts (slap, splash, grab, push, hand-over) and the moment they land, per shot. The picture says
    which shots have one and roughly when (8 frames); a sharp onset in the non-vocal stem within 0.3 s pins it
    to the hit the original mix plays there. The remake has to put its action on that moment."""
    from .media import frame_at, transients
    from .prompts import CONTACT_PROMPT
    music = (story.get("audio") or {}).get("music")
    hits = transients(work / music) if music and (work / music).is_file() else []

    def text(s: dict) -> str:
        a = s.get("analysis") or {}
        return "；".join(f"{c.get('id')}：{c.get('action', '')}" for c in a.get("characters") or []) + "；" + str(a.get("action_summary") or "")

    def one(s: dict) -> list[dict] | None:
        if not any(w in text(s) for w in CONTACT_WORDS):
            return []
        k = 8 if s["duration"] >= 1.5 else 5
        times = [round(s["duration"] * (j + 0.5) / k, 2) for j in range(k)]
        frames = []
        for j, t in enumerate(times):
            f = work / "frames" / f"{s['id']}_c{j}.jpg"
            if not f.is_file():
                frame_at(work / "source.mp4", s["start"] + t, f)
            frames.append(f)
        try:
            r = vlm.ask_json(CONTACT_PROMPT.format(index=s["index"], duration=s["duration"], times="、".join(f"{t}s" for t in times),
                                                   actions=text(s)), frames, max_tokens=1024, timeout=120)
        except Exception as exc:
            LOG.warning("第 %s 镜接触拆解失败：%s", s["index"], exc)
            return None
        out = []
        for c in r.get("contacts") or []:
            try:
                t = min(max(float(c.get("t", 0)), 0.0), s["duration"])
            except (TypeError, ValueError):
                continue
            near = [(z, h) for h, z in hits if abs(h - (s["start"] + t)) <= 0.3 and s["start"] + 0.1 < h < s["end"] - 0.1]
            sound = bool(near)
            if sound:
                t = round(max(near)[1] - s["start"], 2)
            out.append({"who": str(c.get("who") or ""), "to": str(c.get("to") or ""), "action": str(c.get("action") or ""),
                        "t": round(t, 2), "hand": str(c.get("hand") or ""), "sound": sound})
        return out

    with ThreadPoolExecutor(workers) as ex:
        for s, cs in zip(story["shots"], ex.map(one, story["shots"])):
            if cs is None:
                s["contacts_error"] = True
            else:
                s.pop("contacts_error", None)
                s["contacts"] = cs
    return sum(len(s.get("contacts") or []) for s in story["shots"])


def freezes(story: dict, work: Path) -> int:
    """Freeze frames (定格) inside shots: a generated clip never holds still, so assembly freezes it there."""
    from .media import still_runs
    n = 0
    for s in story["shots"]:
        runs = still_runs(work / "source.mp4", s["start"], s["duration"])
        if runs:
            s["freeze"] = [list(r) for r in runs]
            n += 1
        else:
            s.pop("freeze", None)
    return n


def motions(story: dict, work: Path) -> dict:
    """How each shot's picture moves (local, no model): a still picture held or pushed / panned (动态漫画 Ken Burns)
    is rendered from the new keyframe directly; a picture where only a small part moves gets told so."""
    from collections import Counter
    from .media import image_motion
    for s in story["shots"]:
        masks = [x["box"] for x in s.get("subtitles") or [] if x.get("box")]
        masks += [t["box"] for t in s.get("screen_text") or [] if t.get("kind") in ("overlay", "aigc", "watermark")]
        s["motion"] = image_motion(work / "source.mp4", s["start"], s["duration"], masks)
    return dict(Counter(s["motion"]["kind"] for s in story["shots"]))


def expressions(story: dict, vlm: VLM) -> int:
    """The expressions each main character plays in this episode (from the per-shot analysis), merged into a short
    list with the shots of each: the remake can draw an expression sheet so faces stay one person across moods."""
    from .prompts import EXPRESSIONS_PROMPT
    main = [c for c in story["characters"] if c.get("importance") in ("lead", "supporting") and "crowd" not in c["id"]]
    rows = []
    for c in main:
        seen = [f"{s['index']}:{x.get('expression')}" for s in story["shots"]
                for x in (s.get("analysis") or {}).get("characters") or []
                if x.get("id") == c["id"] and x.get("expression") and x.get("visibility") in ("face", "profile", None)]
        if len(seen) >= 3:
            rows.append(f"{c['id']}（{c.get('role', '')}）：" + "；".join(seen))
    if not rows:
        return 0
    r = vlm.ask_json(EXPRESSIONS_PROMPT.format(rows="\n".join(rows)), [], max_tokens=4096, timeout=180)
    by_id = {c["id"]: c for c in story["characters"]}
    n = 0
    for cid, items in (r.get("characters") or {}).items():
        if cid not in by_id or not isinstance(items, list):
            continue
        ex = [{"name": str(x.get("name") or ""), "look": str(x.get("look") or ""),
               "shots": sorted({int(i) for i in x.get("shots") or [] if str(i).isdigit()})}
              for x in items if isinstance(x, dict) and x.get("name")][:8]
        by_id[cid]["expressions"] = ex
        n += len(ex)
    return n


def _transition(work: Path, shot: dict) -> str | None:
    """Flash / fade-in seen in the pixels: the first frame far brighter (white flash) or darker than the middle."""
    from .media import mean_luma
    try:
        first, mid = (mean_luma(work / shot["frames"][k]) for k in ("first", "mid"))
    except Exception:
        return None
    if first > 200 and first > mid + 50:
        return "white_flash"
    if first < 0.5 * mid:
        return "fade"
    return None


def risks(story: dict, shot: dict, key: dict | None = None) -> list[str]:
    """Generation risks of one shot, from the analysis alone (no model call)."""
    a = shot.get("analysis") or {}
    key = key or shot.get("key") or {}
    chars = [c for c in a.get("characters") or [] if c.get("id")]
    ids = {c["id"] for c in chars}
    by_id = {c["id"]: c for c in story["characters"]}
    lines = _lines_of(shot)
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
    from .analyze import identity_pairs
    out = []
    add = lambda level, shot, msg: out.append({"level": level, "shot": shot, "msg": msg})
    ids = {c["id"] for c in story["characters"]}
    main = {c["id"] for c in story["characters"] if c.get("importance") != "extra" and "crowd" not in c["id"]}
    bd = story.get("breakdown") or {}
    if not bd:
        add("error", None, "还没做深度拆解（rea-toon breakdown）")
    unknown: dict[str, list[int]] = {}  # ids a shot names that the cast does not have
    for s in story["shots"]:
        a = s.get("analysis") or {}
        i = s["index"]
        if not a or a.get("error"):
            add("error", i, f"逐镜拆解失败：{a.get('error', '没有结果')}")
            continue
        here = {c.get("id") for c in a.get("characters") or []}
        for x in _lines_of(s):
            if not x.get("speaker"):
                add("error", i, f"台词「{x['text']}」没有说话人")
            elif x["speaker"] not in ids | {NARRATOR}:
                add("error", i, f"台词「{x['text']}」的说话人 {x['speaker']} 不在角色表里")
            elif x.get("on_screen") and x["speaker"] not in here:
                add("warn", i, f"台词「{x['text']}」标成 {x['speaker']} 在画面里说，但这一镜没标出 {x['speaker']}")
        for c in here - ids - {"crowd", None}:
            unknown.setdefault(c, []).append(i)
        perf = s.get("performance")
        if here & main and (not perf or perf.get("error")):
            add("error", i, "有主要角色但没有表演拆解（逐秒表情/口型）")
        if s["duration"] > 15 and not story.get("cuts"):
            add("warn", i, f"{s['duration']:.1f}s 超过 15 秒：用 analyze --cuts 切开")
        if s.get("contacts_error"):
            add("warn", i, "身体接触拆解失败：这一镜的接触时间没有，重跑 breakdown")
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
            if not _has(sc, cid) or not _day(sc):
                continue
            start = _parts((sc.get("looks") or {}).get(cid))["wear"] if cid in (sc.get("looks") or {}) else ""
            prev = last.get((_day(sc), cid))
            if prev and start and prev[1] and _differs(start, prev[1]):
                add("warn", sc["shots"][0], f"{cid} 在同一剧情日 {sc.get('day')} 里，第 {prev[0]['id']} 场结尾穿「{prev[1]}」，"
                                            f"第 {sc['id']} 场开头穿「{start}」：确认是剧情里换了衣服，不是拆解看错")
            end = look_parts(story, cid, sc["shots"][1]).get("wear", "")
            if end:
                last[(_day(sc), cid)] = (sc, end)
    for i, j, a, b in axis_flips(story):
        add("warn", j, f"{a} 和 {b} 在镜{i}是{a}左{b}右，到镜{j}左右换了（同一场）：可能越轴，"
                       f"原片就是这样拍的复刻照做，否则是拆解看错了位置")
    chars = [c for c in story["characters"] if c.get("importance") != "extra" and "crowd" not in c["id"]]
    distinct = [sorted(x) for x in (story.get("refined") or {}).get("distinct") or []]
    for (a, b), clues in identity_pairs(story, chars).items():
        if sorted((a, b)) in distinct:  # a model already looked at both and said they are different people
            continue
        add("warn", None, f"{a} 和 {b} 可能是同一个人（乔装/换装），确认后在 story.json 合并：{clues[0]}")
    for c, at in sorted(unknown.items(), key=lambda kv: kv[1][0]):
        add("warn", at[0], f"画面里的 {c}（镜{'、'.join(map(str, at))}）不在角色表里：是有戏的人就加进角色表，"
                           f"是尸体/物件/背景就不用管")
    must = sum(1 for s in story["shots"] if (s.get("key") or {}).get("priority") == "must")
    if bd and must > 0.35 * len(story["shots"]):
        add("warn", None, f"必保镜头 {must} 个，太多就等于没有重点")
    return out


def breakdown(work: Path, vlm: VLM | None = None, workers: int = 6) -> dict:
    """Run the deep passes on an analysed work dir and write the review pages. Unlocks the story."""
    from .analyze import contact_sheets
    vlm = vlm or VLM(cache_dir=work / "analysis" / "vlm_cache")
    path = work / "story.json"
    before = path.read_text(encoding="utf-8")
    story = json.loads(before)
    if story.get("breakdown"):  # a redo replaces hand edits of key / scenes: keep the old file to copy them back from
        (work / "story.prev.json").write_text(before, encoding="utf-8")
        LOG.info("重做深度拆解：旧版本存在 story.prev.json（手改过的看点/造型可以从这里抄回来）")
    n = len(story["shots"])
    cast = _cast_text(story)
    sheets = sorted((work / "analysis").glob("contact_*.jpg")) or contact_sheets(story["shots"], work)

    LOG.info("深度拆解：画风档案 + 分场 …")
    r = vlm.ask_json(SCENES_PROMPT.format(summary=story.get("summary", ""), cast=cast, table=_table(story)),
                     sheets, max_tokens=12000, timeout=400)
    scenes = _fix_scenes(r.get("scenes") or [], n)
    bd = {"version": 1, "style_bible": r.get("style_bible") or {}, "scenes": scenes}
    story["breakdown"] = bd

    LOG.info("深度拆解：%d 场的造型 …", len(scenes))
    with ThreadPoolExecutor(workers) as ex:
        for sc, res in zip(scenes, ex.map(lambda sc: scene_looks(story, work, sc, vlm), scenes)):
            sc.update(res)

    LOG.info("深度拆解：画面里的文字（招牌/屏幕/花字/水印）…")
    try:
        n = screen_text(story, work, vlm, workers)
        LOG.info("画面文字 %d 处", n)
    except Exception as exc:  # e.g. OCR not installed: the breakdown goes on, lint says the step is missing
        LOG.warning("画面文字识别失败：%s", exc)
    for label, fn in (("机位角度和视线", lambda: staging(story, work, vlm, workers)),
                      ("身体接触和音效点", lambda: contacts(story, work, vlm, workers)),
                      ("定格", lambda: freezes(story, work)),
                      ("画面动法（静图推拉/局部动/全动画）", lambda: motions(story, work)),
                      ("角色表情清单", lambda: expressions(story, vlm))):
        LOG.info("深度拆解：%s …", label)
        try:
            LOG.info("%s：%s", label, fn())
        except Exception as exc:  # the breakdown goes on; the remake simply has less to go by
            LOG.warning("%s失败：%s", label, exc)
    LOG.info("深度拆解：戏剧节拍和逐镜看点 …")
    scene_text = "\n".join(f"{sc['id']} 镜{sc['shots'][0]}-{sc['shots'][1]} {sc.get('location', '')} {sc.get('time', '')}："
                           + "；".join(f"{k}={_one(v)}" for k, v in (sc.get("looks") or {}).items()) for sc in scenes)
    r = vlm.ask_json(BEATS_PROMPT.format(summary=story.get("summary", ""), cast=cast, scenes=scene_text,
                                         table=_table(story, emotion=True)), sheets, max_tokens=20000, timeout=400)
    bd["beats"] = [b for b in r.get("beats") or [] if b.get("type")]
    given = {int(x["i"]): x for x in r.get("shots") or [] if str(x.get("i", "")).isdigit()}
    for s in story["shots"]:
        g = given.get(s["index"], {})
        key = {"role": g.get("role") if g.get("role") in ROLES else "normal",
               "priority": g.get("priority") if g.get("priority") in PRIORITY_CN else "normal",
               "function": str(g.get("function") or "").strip(),
               "keep": [str(x) for x in g.get("keep") or [] if x][:3],
               "fx": [str(x) for x in g.get("fx") or [] if x],
               "transition_in": g.get("transition_in") if g.get("transition_in") in TRANSITIONS else "cut"}
        seen = _transition(work, s)  # the pixels win over the model on flash / fade
        if seen and key["transition_in"] == "cut":
            key["transition_in"] = seen
        if not g:
            key["missing"] = True  # lint: the model skipped this shot
        s["key"] = key
    for s in story["shots"]:
        s["risks"] = risks(story, s)
    story["lint"] = lint(story)
    story.pop("lock", None)
    path.write_text(json.dumps(story, ensure_ascii=False, indent=1), encoding="utf-8")
    write_review(story, work)
    return story


# ---- lock: the remake runs only on a reviewed breakdown ----

def digest(story: dict) -> str:
    body = {k: v for k, v in story.items() if k != "lock"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def lock(work: Path, force: bool = False) -> dict:
    path = work / "story.json"
    story = json.loads(path.read_text(encoding="utf-8"))
    story["lint"] = lint(story)
    errors = [x for x in story["lint"] if x["level"] == "error"]
    if errors and not force:
        raise ValueError(f"拆解还有 {len(errors)} 个必须修的问题（看 breakdown.md 顶部），修好再 lock；"
                         "确实要带着这些问题复刻就加 --force")
    story["lock"] = {"hash": digest(story), "at": time.strftime("%Y-%m-%d %H:%M:%S"), "forced": bool(errors)}
    path.write_text(json.dumps(story, ensure_ascii=False, indent=1), encoding="utf-8")
    write_review(story, work)
    return story["lock"]


def lock_state(story: dict) -> str:
    """locked | stale (edited after lock) | unlocked"""
    lk = story.get("lock") or {}
    if not lk:
        return "unlocked"
    return "locked" if lk.get("hash") == digest(story) else "stale"


def require_locked(story: dict) -> None:
    state = lock_state(story)
    if state == "unlocked":
        raise RuntimeError("剧本拆解还没确认：先 `rea-toon breakdown WORK`，审 WORK/breakdown.html，"
                           "改好后 `rea-toon lock WORK`，再复刻")
    if state == "stale":
        raise RuntimeError("story.json 在 lock 之后被改过：审一下改动（WORK/breakdown.html），再 `rea-toon lock WORK`")


# ---- review pages ----

def _staging_text(s: dict) -> str:
    st = s.get("staging") or {}
    ppl = "；".join(f"{k} 朝{FACING_CN.get(v.get('facing'), '?')}、看{v.get('looking_at') or '?'}"
                   for k, v in (st.get("people") or {}).items())
    return "，".join(x for x in (ANGLE_CN.get(st.get("angle"), ""), ppl, st.get("power", "")) if x)


MOTION_CN = {"still": "静图", "pan": "静图推拉", "partial": "局部动", "full": ""}


def _extras(s: dict) -> str:
    """Comic-drama elements of a shot for the review: how it moves, effects, manga marks, panels."""
    st = s.get("staging") or {}
    out = [MOTION_CN.get((s.get("motion") or {}).get("kind"), "")]
    out += [f"特效 {e['what']}（{e.get('color') or '?'}，{e.get('from') or '?'} 发出）" for e in st.get("effects") or []]
    out += ["漫符 " + "、".join(st["marks"])] if st.get("marks") else []
    out += [f"{st['panels']} 宫格"] if st.get("panels") else []
    return "；".join(x for x in out if x)


def _texts(s: dict) -> str:
    return "；".join(f"{_TEXT_CN.get(t.get('kind'), '待定')}「{t['text']}」" for t in s.get("screen_text") or [])


def _risk_cn(r: str) -> str:
    return RISK_CN.get(r, r).split("：")[0]


def write_review(story: dict, work: Path) -> None:
    _write_md(story, work)
    _write_html(story, work)


def _write_md(story: dict, work: Path) -> None:
    bd = story.get("breakdown") or {}
    sb = bd.get("style_bible") or {}
    L = [f"# 深度拆解：{(story.get('source') or {}).get('title') or 'source'}", "",
         f"状态：**{ {'locked': '已锁定', 'stale': '锁定后改过，需要重新 lock', 'unlocked': '待审核'}[lock_state(story)] }**", ""]
    lint_rows = story.get("lint") or []
    if lint_rows:
        L += ["## 要处理的问题", ""] + [f"- {'❌' if x['level'] == 'error' else '⚠️'} "
                                        f"{'镜' + str(x['shot']) + '：' if x['shot'] else ''}{x['msg']}" for x in lint_rows] + [""]
    L += ["## 剧情", "", story.get("summary", ""), "", "## 画风档案", ""]
    L += [f"- {k}：{v if not isinstance(v, list) else '、'.join(v)}" for k, v in sb.items()] + [""]
    L += ["## 角色", ""]
    for c in story["characters"]:
        L.append(f"- **{c['id']}** {c.get('role', '')}（{c.get('importance', '')}），镜头 {c.get('shots')}：{c.get('appearance', '')}")
        if c.get("expressions"):
            L.append("  - 表情：" + "；".join(f"{x['name']}（镜{','.join(map(str, x['shots']))}）" for x in c["expressions"]))
        for o in c.get("outfits") or []:
            L.append(f"  - 另一套造型（原 id {o.get('was')}，镜头 {o.get('shots')}）：{o.get('appearance', '')}")
    L += ["", "## 戏剧节拍", ""]
    L += [f"- **{ROLE_CN.get(b['type'], b['type'])}** 镜{b.get('shots')}：{b.get('what', '')}；必须保住：{b.get('must_keep', '')}"
          for b in bd.get("beats") or []] + [""]
    L += ["## 分场与造型", ""]
    for sc in bd.get("scenes") or []:
        L.append(f"### {sc['id']} 镜 {sc['shots'][0]}–{sc['shots'][1]}：{sc.get('location', '')}（{sc.get('time', '')}）"
                 f"{_day_tag(sc)}")
        L += [f"- {k}：{_one(v)}" for k, v in (sc.get("looks") or {}).items()]
        L += [f"- 镜{c['shot']}起 {c['who']}：{_one(c)}" for c in sc.get("changes") or []]
        L.append("")
    L += ["## 逐镜", "", "| # | 时间 | 作用 | 优先级 | 剧情功能 | 必须保住 | 转场/特效 | 台词（说话人） | 风险 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for s in story["shots"]:
        k = s.get("key") or {}
        lines = " / ".join(f"{x.get('speaker', '?')}{_said(x)}：{x['text']}" for x in _lines_of(s))
        fx = "、".join([k.get("transition_in", "")] * (k.get("transition_in", "cut") != "cut") + list(k.get("fx") or []))
        L.append(f"| {s['index']} | {s['start']:.1f}-{s['end']:.1f} | {ROLE_CN.get(k.get('role'), '')} | "
                 f"{PRIORITY_CN.get(k.get('priority'), '')} | {k.get('function', '')} | {'；'.join(k.get('keep') or [])} | {fx} | {lines} | "
                 f"{'、'.join(_risk_cn(r) for r in s.get('risks') or [])} |")
    (work / "breakdown.md").write_text("\n".join(L) + "\n", encoding="utf-8")


from .page import CSS as _CSS  # noqa: E402  (shared with report / series pages)


def _write_html(story: dict, work: Path) -> None:
    from .analyze import _portraits
    e = lambda x: html.escape(str(x or ""))
    bd = story.get("breakdown") or {}
    state = {"locked": "已锁定，可以复刻", "stale": "锁定后改过，需要重新 lock", "unlocked": "待审核"}[lock_state(story)]
    H = [f"<!doctype html><html lang=zh><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>深度拆解</title><style>{_CSS}</style></head><body><main>",
         f"<h1>深度拆解：{e((story.get('source') or {}).get('title') or 'source')}</h1>",
         f"<p><span class=state>{state}</span> <span class=muted>{len(story['shots'])} 镜 · {story.get('duration', 0):.0f}s · "
         f"审完改好后运行 <code>rea-toon lock {e(work.name)}</code></span></p>"]
    lint_rows = story.get("lint") or []
    if lint_rows:
        H.append("<h2>要处理的问题</h2><ul>")
        H += [f"<li class={'err' if x['level'] == 'error' else 'warn'}>{'镜' + str(x['shot']) + '：' if x['shot'] else ''}{e(x['msg'])}</li>"
              for x in lint_rows]
        H.append("</ul>")
    H += [f"<h2>剧情</h2><p>{e(story.get('summary'))}</p>", "<h2>画风档案</h2><ul>"]
    H += [f"<li><b>{e(k)}</b>：{e('、'.join(v) if isinstance(v, list) else v)}</li>" for k, v in (bd.get("style_bible") or {}).items()]
    H.append("</ul><h2>角色</h2><div class=grid>")
    for c in story["characters"]:
        pics = "".join(f"<img src='{e(p.relative_to(work))}' alt=''>" for p in _portraits(story, work, c["id"]))
        outfits = "".join(f"<li>另一套造型（镜 {e(o.get('shots'))}）：{e(o.get('appearance'))}</li>" for o in c.get("outfits") or [])
        H.append(f"<div class=card><div class=pics>{pics}</div><p><b>{e(c['id'])}</b> {e(c.get('role'))} "
                 f"<span class=chip>{e(c.get('importance'))}</span></p><p class=muted>{e(c.get('appearance'))}</p>"
                 f"{'<ul>' + outfits + '</ul>' if outfits else ''}"
                 + (f"<p class=muted>表情：{e('；'.join(x['name'] for x in c['expressions']))}</p>" if c.get("expressions") else "")
                 + "</div>")
    H.append("</div><h2>戏剧节拍</h2><ul>")
    H += [f"<li><b>{e(ROLE_CN.get(b['type'], b['type']))}</b> 镜{e(b.get('shots'))}：{e(b.get('what'))}"
          f"<br><span class=muted>必须保住：{e(b.get('must_keep'))}</span></li>" for b in bd.get("beats") or []]
    H.append("</ul><h2>逐镜（按场）</h2>")
    shots = {s["index"]: s for s in story["shots"]}
    for sc in bd.get("scenes") or [{"id": "", "shots": [1, len(story["shots"])]}]:
        H.append(f"<div class=scene>{e(sc['id'])} 镜 {sc['shots'][0]}–{sc['shots'][1]}：{e(sc.get('location'))} {e(sc.get('time'))}"
                 f"{e(_day_tag(sc))}</div>")
        looks = "".join(f"<li><b>{e(k)}</b>：{e(_one(v))}</li>" for k, v in (sc.get("looks") or {}).items())
        looks += "".join(f"<li>镜{e(c['shot'])}起 <b>{e(c['who'])}</b>：{e(_one(c))}</li>" for c in sc.get("changes") or [])
        if looks:
            H.append(f"<ul class=muted>{looks}</ul>")
        for i in range(sc["shots"][0], sc["shots"][1] + 1):
            s = shots.get(i)
            if not s:
                continue
            k = s.get("key") or {}
            a = s.get("analysis") or {}
            pr = k.get("priority", "normal")
            lines = "".join(f"<div class={'off' if line_mode(x) != 'on' else ''}>{e(x.get('speaker', '?'))}"
                            f"{e(_said(x))}：{e(x['text'])}</div>" for x in _lines_of(s))
            chips = [ROLE_CN.get(k.get("role"), "")] + ([k["transition_in"]] if k.get("transition_in", "cut") != "cut" else []) + list(k.get("fx") or [])
            H.append(f"<div class='shot {pr}'><img loading=lazy src='{e((s.get('frames') or {}).get('mid'))}' alt=''><div>"
                     f"<b>#{i}</b> <span class=muted>{s['start']:.1f}–{s['end']:.1f}s · {e(a.get('shot_size'))} · {e(a.get('camera_move'))}</span> "
                     f"<span class={pr}>{PRIORITY_CN.get(pr, '')}</span> " + "".join(f"<span class=chip>{e(c)}</span>" for c in chips if c)
                     + f"<div>{e(a.get('action_summary'))}</div>"
                     + (f"<div><b>剧情功能</b>：{e(k['function'])}</div>" if k.get("function") else "")
                     + (f"<div><b>必须保住</b>：{e('；'.join(k['keep']))}</div>" if k.get("keep") else "")
                     + (f"<div class=lines>{lines}</div>" if lines else "")
                     + (f"<div class=muted>画面文字：{e(_texts(s))}</div>" if s.get("screen_text") else "")
                     + (f"<div class=muted>机位/视线：{e(_staging_text(s))}</div>" if s.get("staging") else "")
                     + (f"<div class=muted>画面：{e(_extras(s))}</div>" if _extras(s) else "")
                     + "".join(f"<div><b>接触</b> {c['t']:.2f}s：{e(c['action'])}{'（有音效）' if c.get('sound') else ''}</div>"
                               for c in s.get("contacts") or [])
                     + (f"<div class=muted>风险：{e('、'.join(_risk_cn(r) for r in s.get('risks') or []))}</div>" if s.get("risks") else "")
                     + "</div></div>")
    H.append("</main></body></html>")
    (work / "breakdown.html").write_text("\n".join(H), encoding="utf-8")
