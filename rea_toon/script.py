"""The full script of an episode or a whole series, as the two documents a short drama / 漫剧 is made from.

    pages/script.html / .pdf / .md   剧本: 人物, then per episode 梗概 and every scene in screenplay form —
                               场次（集-场　日/夜　内/外　地点，回忆）, 人物, △动作, 台词（说话人（OS / 内心OS）：（神情）……）,
                               【字幕】. The lines get their punctuation from one text call per episode (cached).
    pages/storyboard.html / .pdf     分镜脚本: 角色设定 (face + costume picture, identity, appearance), then per scene one row
                               a shot — 镜号（必保镜, 戏剧作用）, 缩略图, 景别, 画面描述, 台词 / 旁白 / OS, 音效, 动效, 时长
    characters/<id>_face.jpg   the clearest face of the character (YuNet on the frames of shots they are in)
    characters/<id>_look.jpg   a frame showing them whole, for the costume; characters/shots/ the shot thumbnails

Where these sit is layout.py; the pages name pictures and clips as pages/ reaches them. PDFs are the pages printed
by a headless Chrome. Works on one work dir (story.json) or a series root
(series.json + epNN/): the series cast decides names there.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from . import layout
from .breakdown.staging import ANGLE_CN
from .checks import _side
from .media import faces, run
from .page import esc, page, player, site, stamp
from .story import ROLE_CN, lines_of, load, name_in
from .subtitles import NARRATOR, line_mode, similar_text, unique_lines

LOG = logging.getLogger(__name__)
IMPORTANCE_CN = {"lead": "主角", "supporting": "配角", "support": "配角", "extra": "龙套"}
CLOSE = ("特写", "近景", "中近景", "中景", "全景", "远景")  # closest framing first


# ---- who is who ----

def _cast(root: Path) -> tuple[list[dict], list[dict]]:
    """(episodes, characters). Each episode: {ep, dir, story}; each character: {id, name, role, members[(ep, cid)], …}."""
    if layout.series(root).is_file():
        from . import series
        ser = series.load(root)
        eps = [{"ep": e["ep"], "dir": root / e["dir"], "story": series._story(root, e)} for e in ser["episodes"]]
        eps = [e for e in eps if e["story"] and any(s.get("analysis") for s in e["story"]["shots"])]
        chars = [{**c, "members": [(int(m[1:].split(":", 1)[0]), m.split(":", 1)[1]) for m in c.get("members") or []]}
                 for c in ser.get("characters") or []]
    else:
        story = load(root)
        eps = [{"ep": 1, "dir": root, "story": story}]
        subs = [x for s in story["shots"] for x in s.get("subtitles") or []]
        said, cards = ("\n".join(x["text"] for x in subs if bool(x.get("card")) is k) for k in (False, True))
        chars = [{"id": c["id"], "name": name_in(c, said, cards), "role": c.get("role", ""),
                  "members": [(1, c["id"])]} for c in story["characters"]]
    by_ep = {e["ep"]: e for e in eps}
    for c in chars:
        own = [x for ep, cid in c["members"] if ep in by_ep
               for x in by_ep[ep]["story"]["characters"] if x["id"] == cid]
        first = own[0] if own else {}
        c.update(importance=c.get("importance") or first.get("importance", ""),
                 appearance=first.get("appearance", ""),
                 expressions=[x["name"] for o in own for x in o.get("expressions") or []],
                 shots=sum(len(o.get("shots") or []) for o in own))
        c["label"] = c.get("name") or c.get("role") or c["id"]
    chars = [c for c in chars if c["shots"]]
    order = {"lead": 0, "supporting": 1, "support": 1, "extra": 3}
    chars.sort(key=lambda c: (order.get(c["importance"], 2), "crowd" in c["id"], -c.get("seconds", 0), -c["shots"]))
    return eps, chars


def _namer(chars: list[dict], eps: list[dict] = ()):
    names = {m: c["label"] for c in chars for m in c["members"]}
    for e in eps:  # extras are not in the cast list: the episode's own word for them (广播站/客服台工作人员（…） → 广播站)
        for c in e["story"].get("characters") or []:
            short = re.split(r"[（(/，,]", c.get("role") or "")[0].strip()
            if short:
                names.setdefault((e["ep"], c["id"]), short)

    def name(ep: int, cid: str | None) -> str:
        if cid == NARRATOR:
            return "旁白"
        return names.get((ep, cid), cid or "？")
    return name


# ---- pictures ----

def _rank(s: dict) -> int:
    size = str((s.get("analysis") or {}).get("shot_size", ""))
    return next((i for i, w in enumerate(CLOSE) if w in size), len(CLOSE))


def _frame(work: Path, s: dict, name: str) -> Image.Image:
    """A shot frame with the burned-in subtitles of that moment painted out (they would sit on every picture)."""
    import cv2
    import numpy as np
    t = {"first": s["start"] + 0.04, "mid": (s["start"] + s["end"]) / 2, "last": s["end"] - 0.07}[name]
    img = cv2.imread(str(work / s["frames"][name]))
    boxes = [x["box"] for x in s.get("subtitles") or [] if x.get("box") and x["start"] - 0.1 <= t <= x["end"] + 0.1]
    if boxes:
        h, w = img.shape[:2]
        mask = np.zeros((h, w), np.uint8)
        for x0, y0, x1, y1 in boxes:
            cv2.rectangle(mask, (int(x0 * w) - 6, int(y0 * h) - 6), (int(x1 * w) + 6, int(y1 * h) + 6), 255, -1)
        img = cv2.inpaint(img, mask, 7, cv2.INPAINT_TELEA)
    return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))


def _crop_face(img: Image.Image, f: tuple, out: Path, size: int = 320) -> Path:
    x, y, w, h = f[:4]
    side = 2.3 * max(w, h)
    cx, cy = x + w / 2, y + h * 0.62  # a little below the face centre: head and shoulders
    box = [cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2]
    dx = max(0, -box[0]) - max(0, box[2] - img.width)
    dy = max(0, -box[1]) - max(0, box[3] - img.height)
    box = [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]
    box = [max(0, box[0]), max(0, box[1]), min(img.width, box[2]), min(img.height, box[3])]
    out.parent.mkdir(parents=True, exist_ok=True)
    img.crop(tuple(int(v) for v in box)).resize((size, size), Image.LANCZOS).save(out, quality=90)
    return out


def _pick_face(work: Path, s: dict, cid: str) -> tuple[str, tuple, float] | None:
    """The face of `cid` in one shot's frames: the only face when they are alone, else the one on their side."""
    people = [c for c in (s.get("analysis") or {}).get("characters") or []
              if c.get("id") and "crowd" not in str(c["id"]) and c.get("visibility") in ("face", "profile")]
    me = next((c for c in people if c["id"] == cid), None)
    if not me:
        return None
    best = None
    for name in ("mid", "first", "last"):
        p = work / (s.get("frames") or {}).get(name, "")
        if not p.is_file():
            continue
        fs = [f for f in faces(p) if f[4] >= 0.8]  # weaker hits are fur, masks, patterns
        if not fs:
            continue
        width, height = Image.open(p).size
        if len(people) == 1:
            f = fs[0]
        else:  # several people: the face on the side the breakdown puts them
            side = _side(me.get("where"))
            pos = lambda f: (f[0] + f[2] / 2) / width
            hits = [f for f in fs if side == 0 and 0.3 < pos(f) < 0.7 or side < 0 and pos(f) < 0.45 or side > 0 and pos(f) > 0.55]
            if len(hits) != 1:  # not sure whose face it is: no picture from this frame
                continue
            f = hits[0]
        # a head-and-shoulders shot reads best: a face filling the frame crops to half a face, a tiny one is mush
        share = f[3] / height
        fit = min(share, 0.3) - max(0.0, share - 0.3) * 1.5
        whole = 1.0 if f[1] >= 0 and f[0] >= 0 and f[0] + f[2] <= width else 0.5
        score = fit * f[4] * whole * (1.0 if len(people) == 1 else 0.7)
        if not best or score > best[2]:
            best = (name, f, score)
    return best


VOICE_SECONDS = 14.0  # a sample of someone's voice: a few lines, about this long


def voice_lines(eps: list[dict], c: dict, k: int = 3) -> list[dict]:
    """The lines to hear one character by: said by them alone (nobody else's line over it), long enough to carry a
    tone, said on camera before said off it; in the order they are said. Each: {ep, dir, start, end, text}."""
    by_ep = {e["ep"]: e for e in eps}
    found = []
    for ep, cid in c["members"]:
        e = by_ep.get(ep)
        if not e:
            continue
        lines = unique_lines(e["story"])
        for x in lines:
            text = clean_line(str(x.get("text") or ""))
            a, b = float(x.get("start") or 0), float(x.get("end") or 0)
            if x.get("speaker") != cid or x.get("unsure") or len(text) < 4 or not 0.8 <= b - a <= 9.0:
                continue
            if any(y is not x and y.get("speaker") != cid and min(b, float(y.get("end") or 0)) - max(a, float(y.get("start") or 0)) > 0.15
                   for y in lines):
                continue
            # the subtitle goes before the voice does: room after it (and a little before), up to the lines next to it
            before = max((float(y.get("end") or 0) for y in lines if y is not x and float(y.get("end") or 0) <= a + 0.05), default=0.0)
            after = min((float(y.get("start") or 0) for y in lines if y is not x and float(y.get("start") or 0) >= b - 0.05), default=b + 1.0)
            found.append({"ep": ep, "dir": e["dir"], "start": a, "end": b, "text": text, "on": line_mode(x) == "on",
                          "cut": (max(before, a - 0.15, 0.0), max(b, min(after - 0.03, b + 0.5)))})
    found.sort(key=lambda x: (not x["on"], -len(x["text"])))
    picked, total = [], 0.0
    for x in found:
        if len(picked) < k and total + x["end"] - x["start"] <= VOICE_SECONDS:
            picked.append(x)
            total += x["end"] - x["start"]
    return sorted(picked, key=lambda x: (x["ep"], x["start"]))


def voice(eps: list[dict], c: dict, out_dir: Path) -> dict:
    """A sample of one character's voice, cut from the vocal stem of the source (the music is not in it):
    characters/<id>_voice.mp3 with the lines it holds; {} when they say nothing usable or there is no audio."""
    lines = voice_lines(eps, c)
    track = lambda x: next((p for p in (layout.decomp(x["dir"], "stems", "vocals.wav"), layout.source(x["dir"])) if p.is_file()), None)
    lines = [x for x in lines if track(x)]
    if not lines:
        return {}
    out = out_dir / f"{c['id']}_voice.mp3"
    newest = max(layout.story(x["dir"]).stat().st_mtime for x in lines if layout.story(x["dir"]).is_file())
    if not out.is_file() or out.stat().st_mtime < newest:
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd, graph = ["ffmpeg", "-y", "-v", "error"], []
        for i, x in enumerate(lines):
            a, b = x["cut"]
            cmd += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(track(x))]
            graph.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=mono,afade=t=in:d=0.04,"
                         f"afade=t=out:st={max(0.0, b - a - 0.06):.3f}:d=0.06,apad=pad_dur=0.3[a{i}]")
        graph.append("".join(f"[a{i}]" for i in range(len(lines))) + f"concat=n={len(lines)}:v=0:a=1,loudnorm=I=-18:TP=-2[o]")
        run(cmd + ["-filter_complex", ";".join(graph), "-map", "[o]", "-ac", "1", "-b:a", "80k", str(out)])
    return {"src": out, "lines": [{"ep": x["ep"], "text": x["text"]} for x in lines],
            "seconds": round(sum(x["end"] - x["start"] for x in lines), 1)}


def pictures(eps: list[dict], c: dict, out_dir: Path, tries: int = 12) -> dict:
    """{"face": path, "look": path} of one character ({} where nothing usable was found)."""
    by_ep = {e["ep"]: e for e in eps}
    shots = [(by_ep[ep], s) for ep, cid in c["members"] if ep in by_ep
             for s in by_ep[ep]["story"]["shots"]
             if any(x.get("id") == cid and x.get("visibility") in ("face", "profile")
                    for x in (s.get("analysis") or {}).get("characters") or [])]
    cid_of = {ep: cid for ep, cid in c["members"]}
    out = {}
    close = sorted(shots, key=lambda es: (_rank(es[1]), -es[1]["duration"]))[:tries]
    best = None
    for e, s in close:
        got = _pick_face(e["dir"], s, cid_of[e["ep"]])
        if got and (not best or got[2] > best[4]):
            best = (e, s, *got)
    if best:
        e, s, name, f, _ = best
        out["face"] = _crop_face(_frame(e["dir"], s, name), f, out_dir / f"{c['id']}_face.jpg")
    elif close:  # no human face found (an animal, a mask, a back): their closest shot as it is
        e, s = close[0]
        out["face"] = out_dir / f"{c['id']}_face.jpg"
        out["face"].parent.mkdir(parents=True, exist_ok=True)
        im = _frame(e["dir"], s, "mid")
        im.thumbnail((480, 480))
        im.save(out["face"], quality=90)
    # the costume: a medium / wide shot with them alone in it, from their main look (not a flashback), preferring
    # the scene they spend the most shots in
    alone = lambda s: sum(1 for x in (s.get("analysis") or {}).get("characters") or []
                          if x.get("id") and "crowd" not in str(x["id"])) == 1

    def scene(e, s):
        return next((sc for sc in (e["story"].get("breakdown") or {}).get("scenes") or []
                     if sc["shots"][0] <= s["index"] <= sc["shots"][1]), {})
    count: dict[tuple, int] = {}
    for e, s in shots:
        k = (e["ep"], scene(e, s).get("id"))
        count[k] = count.get(k, 0) + 1
    framing = {3: 0, 2: 1, 4: 2}  # 中景, 中近景, 全景: the whole costume, still big enough to see
    wide = sorted(shots, key=lambda es: (bool(scene(*es).get("flashback")), not alone(es[1]),
                                         framing.get(_rank(es[1]), 3), -count[(es[0]["ep"], scene(*es).get("id"))]))
    if wide:
        e, s = wide[0]
        dst = out_dir / f"{c['id']}_look.jpg"
        dst.parent.mkdir(parents=True, exist_ok=True)
        im = _frame(e["dir"], s, "mid")
        im.thumbnail((640, 640))
        im.save(dst, quality=88)
        out["look"] = dst
    return out


# ---- the lines, punctuated ----

_MARKS = r"[\s，。？！、；：…—“”‘’\"'《》（）()!?,.;:~～·]"


def _bare(t: str) -> str:
    return re.sub(_MARKS, "", t)


def punctuate(ep: dict, name, vlm=None) -> dict:
    """{(start, text): the line with punctuation} for one episode: subtitles carry none, a script reads badly
    without. One text call per episode, cached like every model answer; a reply that changed any character is
    dropped (the line stays as OCR read it)."""
    story = ep["story"]
    seen, lines = set(), []
    for s in story["shots"]:
        for x in lines_of(s):
            if (x["start"], x["text"]) not in seen:
                seen.add((x["start"], x["text"]))
                lines.append(x)
    if not lines:
        return {}
    if vlm is None:
        from .vlm import for_work
        vlm = for_work(ep["dir"])
    from .prompts import PUNCT_PROMPT
    body = "\n".join(f"{i}|{'旁白' if x.get('speaker') == NARRATOR else name(ep['ep'], x.get('speaker'))}|{x['text']}"
                     for i, x in enumerate(lines))
    res = vlm.ask_json(PUNCT_PROMPT.format(summary=story.get("summary") or "（未知）", lines=body), [], max_tokens=8192)
    out = {}
    for r in (res.get("lines") if isinstance(res, dict) else res) or []:
        try:
            i, text = int(r.get("i", -1)), str(r.get("text") or "").strip()
        except (AttributeError, TypeError, ValueError):
            continue
        if 0 <= i < len(lines) and text and _bare(text) == _bare(lines[i]["text"]):
            out[(lines[i]["start"], lines[i]["text"])] = text
    LOG.info("第 %d 集台词标点：%d/%d 句", ep["ep"], len(out), len(lines))
    return out


# ---- scenes ----

_UNSEEN = ("不可见", "看不清", "模糊", "未露", "出画", "背对", "看不出", "被遮", "无", "侧脸", "遮挡")
_OUT_FIRST, _IN_FIRST = ("室外", "门外", "门口", "门前"), ("室内", "内部", "屋内", "车内")
_IN = ("厅", "房", "室", "酒吧", "礼堂", "舞台", "走廊", "宴会", "拍卖", "沙发", "店", "馆", "办公", "电梯", "病", "车厢", "楼内")
_OUT = ("街", "巷", "胡同", "路", "空地", "广场", "院", "庭", "天井", "战场", "荒", "野", "林", "道", "园", "外", "山", "河", "海")


def _place(loc: str) -> str:
    """内 / 外 from the scene's location words ("" when they don't say)."""
    for words, tag in ((_OUT_FIRST, "外"), (_IN_FIRST, "内"), (_IN, "内"), (_OUT, "外")):
        if any(w in loc for w in words):
            return tag
    return ""


def _daypart(t: str) -> str:
    t = t or ""
    for words, tag in ((("傍晚", "黄昏"), "昏"), (("清晨", "早上", "黎明"), "晨"), (("白天", "日", "上午", "下午", "中午"), "日"),
                       (("夜", "晚"), "夜")):
        if any(t.startswith(w) or w in t[:3] for w in words):
            return tag
    return ""


def _mood(s: dict, cid: str | None) -> str:
    """The speaker's look while saying the line, short enough for a parenthesis (热情讨好), or ""."""
    x = next((c for c in (s.get("analysis") or {}).get("characters") or [] if c.get("id") == cid), None)
    e = str((x or {}).get("expression") or "").strip()
    if not e or any(w in e for w in _UNSEEN):
        return ""
    e = re.split(r"[，,、；;。]", e)[0]
    e = re.sub(r"^(神情|神态|神色|面带|面露|略显|显得|满脸|一脸|表情|眼神)", "", e).strip()
    return e if 1 < len(e) <= 6 else ""


def _thumb(ep: dict, s: dict, root: Path) -> str:
    """A small frame of the shot for the shot script (characters/shots/epNN_SSS.jpg), as the pages of root reach it."""
    src = ep["dir"] / ((s.get("frames") or {}).get("mid") or "")
    if not src.is_file():
        return ""
    dst = layout.characters(root, "shots", f"ep{ep['ep']:02d}_{s['index']:03d}.jpg")
    if not dst.is_file() or dst.stat().st_mtime < src.stat().st_mtime:
        dst.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(src).convert("RGB")
        im.thumbnail((180, 180))
        im.save(dst, quality=80)
    return layout.from_page(root, dst)


SFX_CN = {"ding": "叮", "whoosh": "嗖", "pop": "啵", "hit": "打击", "boom": "轰", "swoosh": "嗖", "click": "咔",
          "riser": "上扬", "impact": "重击", "sting": "刺音", "typing": "打字"}


SFX_ON = {"sticker_in": "贴纸", "huazi_in": "花字", "label_in": "标注"}


def _sfx(ep: dict) -> list[tuple[float, str]]:
    """Sound effects the decomposition heard (叮、嗖、啵…) that are likely real: on a sticker / 花字 / label popping in,
    or very loud. (seconds, "啵（贴纸）"); still a hint, the detector only hears onsets."""
    import json
    p = layout.decomp(ep["dir"], "breakdown.json")
    if not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return []
    out = []
    for e in (d.get("audio_plan") or {}).get("sfx_merged") or []:
        on = (e.get("on") or {}).get("type")
        # an editor drops a sound on a sticker / 花字 / label as it pops in; a hit at a subtitle is mostly the voice
        if e.get("t") is None or not (on in SFX_ON or float(e.get("strength") or 0) >= 6):
            continue
        what = SFX_CN.get(e.get("type"), e.get("type") or "音效")
        out.append((float(e["t"]), what + (f"（{SFX_ON[on]}）" if on in SFX_ON else "")))
    return out


def _speaker(x: dict, name, n: int) -> tuple[str, str]:
    """(name, tag) in screenplay form: 广播（OS）, 容遇（内心OS）, 旁白."""
    mode = line_mode(x)
    if mode == "narration" or x.get("speaker") == NARRATOR:
        return "旁白", ""
    return name(n, x.get("speaker")), {"off": "OS", "inner": "内心OS"}.get(mode, "")


def _speech_to_join(rows: list[dict], who: str, tag: str) -> dict | None:
    """The line a new piece of speech continues: the row right before it by the same speaker, or their last line
    with only actions since when it stopped mid-sentence (ends with a comma). Inner voice never joins spoken lines."""
    for k in range(len(rows) - 1, -1, -1):
        r = rows[k]
        if r["kind"] == "line":
            if r["who"] != who or ("内心OS" in (r["tag"], tag) and r["tag"] != tag):
                return None
            return r if k == len(rows) - 1 or re.search(r"[，、]$", r["text"]) else None
        if r["kind"] != "action":
            return None
    return None


def _join(a: str, b: str) -> str:
    return a + b if re.search(r"[，。？！…、；：”]$", a) else a + "，" + b


def _junk(m: re.Match) -> str:
    w = m.group(0)
    mixed = len(w) >= 5 and re.search(r"\d", w) and re.search(r"[A-Za-z]", w)  # B0SUPN, 777LLL111 (not 3D, MP3)
    return "" if mixed or re.fullmatch(r"\d{7,}", w) else w  # a phone number on a wall


def clean_line(t: str) -> str:
    """A subtitle line without what OCR read off a sign behind it (letters mixed with digits, long digit runs)."""
    t = re.sub(r"[A-Za-z0-9]+", _junk, t)
    t = re.sub(r"([，、；。！？…])[，、；\s]+", r"\1", t)
    t = re.sub(r"^[，、；。！？\s.]+", "", t).strip()
    return t if _bare(t) else ""


def _in_scene(seen: dict, cid: str | None, shot: int, extra: bool) -> bool:
    """Is the speaker of an off-screen line in the scene? A named character: on screen anywhere in it. An extra:
    within three shots, since one extra id often stands for several people (a voice on the speaker, the clerk)."""
    where = seen.get(cid) or []
    return any(abs(i - shot) <= 3 for i in where) if extra else bool(where)


def scenes(ep: dict, name, root: Path | None = None, punct: dict | None = None,
           extras: dict | None = None) -> list[dict]:
    """Scenes of one episode: the screenplay rows (actions, lines, cards) and the shot script rows."""
    story, n = ep["story"], ep["ep"]
    punct, extras = punct or {}, extras or {}
    bd = story.get("breakdown") or {}
    shots = {s["index"]: s for s in story["shots"]}
    sfx = _sfx(ep) if root else []
    # the episode's video as the shot script reaches it: a thumbnail there plays its shot
    clip = layout.from_page(root, layout.source(ep["dir"])) if root and layout.source(ep["dir"]).is_file() else ""
    starts = {}
    for b in bd.get("beats") or []:
        if b.get("shots"):
            starts.setdefault(min(int(i) for i in b["shots"]), []).append(b)
    out, said = [], set()  # a line running over a cut (or a scene change) is in both shots: written once
    for k, sc in enumerate(bd.get("scenes") or [{"id": "S1", "shots": [1, len(shots)]}], 1):
        a, b = sc["shots"]
        here = [shots[i] for i in range(a, b + 1) if i in shots]
        seen: dict[str, list[int]] = {}  # who is on screen in which shots of the scene
        for s in here:
            for c in (s.get("analysis") or {}).get("characters") or []:
                if c.get("id") and "crowd" not in str(c["id"]):
                    seen.setdefault(c["id"], []).append(s["index"])
        people = list(seen)
        where = re.sub(r"[（(][^）)]*[）)]", "", sc.get("location") or "").strip()
        block = {"no": f"{n}-{k}", "where": where, "time": _daypart(sc.get("time")), "place": _place(where),
                 "raw_where": sc.get("location") or "", "raw_time": sc.get("time") or "",
                 "flashback": bool(sc.get("flashback")), "shots": [a, b], "start": here[0]["start"] if here else 0,
                 "people": [name(n, c) for c in people], "rows": [], "board": []}
        for s in here:
            an = s.get("analysis") or {}
            act = str(an.get("action_summary") or "").strip().rstrip("。")
            prev = block["rows"][-1] if block["rows"] else {}
            if act:
                if prev.get("kind") == "action":  # no line in between: one paragraph, without saying it twice
                    if max(similar_text(act, x) for x in prev["parts"]) < 0.5:
                        prev["parts"].append(act)
                else:
                    block["rows"].append({"kind": "action", "parts": [act]})
            for x in s.get("subtitles") or []:
                if x.get("card") or x.get("speaker") == "title":
                    cards = [r["text"] for r in block["rows"] if r["kind"] == "card"]
                    if not cards or similar_text(cards[-1], x["text"]) < 0.6:  # a card over several shots: once
                        block["rows"].append({"kind": "card", "text": x["text"]})
            spoken = []
            for x in lines_of(s):
                who, tag = _speaker(x, name, n)
                k = (x["start"], x["text"])
                text = clean_line(punct.get(k, x["text"]))
                if not _bare(text):
                    continue  # nothing but a sign / phone number read as a subtitle
                if spoken and spoken[-1]["who"] == who and spoken[-1]["tag"] == tag:
                    spoken[-1]["text"] = _join(spoken[-1]["text"], text)
                    spoken[-1]["keys"].append(k)
                else:
                    spoken.append({"who": who, "tag": tag, "text": text, "keys": [k]})
                if k in said:
                    continue
                said.add(k)
                cid = x.get("speaker")
                if tag == "OS" and _in_scene(seen, cid, s["index"], cid in extras):
                    tag = ""  # off this shot but in the scene: a screenplay writes it as a plain line
                mood = _mood(s, cid) if who != "旁白" and tag != "OS" else ""
                speech = _speech_to_join(block["rows"], who, tag)
                if speech:  # one speech split over several subtitles / shots: one line, actions after it
                    speech["text"] = _join(speech["text"], text)
                    speech["tag"] = speech["tag"] if speech["tag"] == tag else ""  # OS only if all of it is off screen
                    speech["mood"] = speech["mood"] or mood
                    speech["keys"].append(k)
                else:
                    block["rows"].append({"kind": "line", "who": who, "tag": tag, "mood": mood, "text": text,
                                          "keys": [k], "cid": cid})
            key = s.get("key") or {}
            move = an.get("camera_move") if an.get("camera_move") not in (None, "", "固定") else ""
            angle = (s.get("staging") or {}).get("angle")
            motion = (s.get("motion") or {}).get("kind")
            fx = [str(f) for f in key.get("fx") or []]
            block["board"].append({
                "no": s["index"], "t": s["start"], "dur": s["duration"], "clip": clip,
                "thumb": _thumb(ep, s, root) if root else "",
                "size": an.get("shot_size") or "", "angle": ANGLE_CN.get(angle, "") if angle not in (None, "eye") else "",
                "picture": act, "who": [name(n, c.get("id")) for c in an.get("characters") or []
                                        if c.get("id") and "crowd" not in str(c["id"])],
                "lines": spoken, "sfx": list(dict.fromkeys(w for t, w in sfx if s["start"] <= t < s["end"])),
                "motion": " · ".join(x for x in ({"full": "全动", "partial": "局部动", "pan": "静图推拉", "still": "静图"}.get(motion, ""),
                                                  move, *fx) if x),
                "mood": an.get("emotion") or "", "beat": [ROLE_CN.get(x["type"], x["type"]) for x in starts.get(s["index"], [])],
                "must": key.get("priority") == "must"})
        for r in block["rows"]:
            if r["kind"] == "action":
                r["text"] = "。".join(r.pop("parts")) + "。"
        out.append(block)
    return out


def _same_cards(blocks: list[dict]) -> None:
    """One spelling per on-screen card across the drama (OCR reads VICTORY as VICTOR / ICTORY now and then: the
    commonest, then longest, reading wins); a card of one letter is a misread and goes."""
    from collections import Counter
    rows = [r for b in blocks for r in b["rows"] if r["kind"] == "card"]
    seen = Counter(r["text"] for r in rows)
    for r in rows:
        r["text"] = max((t for t in seen if similar_text(t, r["text"]) >= 0.7), key=lambda t: (seen[t], len(t)))
    for b in blocks:
        b["rows"] = [r for r in b["rows"] if r["kind"] != "card" or len(re.findall(r"\w", r["text"])) > 1]


def _strip(text: str, cut: list[str]) -> str:
    """`text` without the pieces the model cut out (each found with or without punctuation inside)."""
    for c in sorted(cut, key=len, reverse=True):
        text = re.sub((_MARKS + "*").join(map(re.escape, c)), "", text, count=1)
    return clean_line(text)


def _only_cut(old: str, new: str, cjk: int = 6) -> bool:
    """Is `new` `old` with some characters deleted (and at most `cjk` Chinese ones)? The model may only cut junk."""
    o, n_ = _bare(old), _bare(new)
    it = iter(o)
    if not all(ch in it for ch in n_):
        return False
    gone = len(re.findall(r"[\u4e00-\u9fff]", o)) - len(re.findall(r"[\u4e00-\u9fff]", n_))
    if not n_:  # the whole line dropped: only when it is mostly letters / digits
        return len(re.findall(r"[A-Za-z0-9]", o)) >= 0.4 * max(1, len(o))
    return gone <= cjk


def polish(ep: dict, blocks: list[dict], extras: dict[str, str], name, vlm=None) -> int:
    """Rewrite one episode's screenplay with one text call (cached): the action paragraphs as a writer would put
    them (no camera talk, no repeats), each scene's 日/夜 内/外 and a short place name, a name for each extra.
    Changes `blocks` in place; whatever the reply leaves out stays as it was. Returns the actions rewritten."""
    if vlm is None:
        from .vlm import for_work
        vlm = for_work(ep["dir"])
    from .prompts import SCRIPT_POLISH_PROMPT
    acts, lines, body = {}, {}, []
    for b in blocks:
        body.append(f"【{b['no']}】地点：{b['raw_where']}｜时间：{b['raw_time'] or '未知'}" + ("｜回忆" if b["flashback"] else ""))
        for r in b["rows"]:
            if r["kind"] == "action":
                aid = f"A{len(acts)}"
                acts[aid] = r
                body.append(f"{aid}: {r['text']}")
            elif r["kind"] == "line":
                lid = f"L{len(lines)}"
                lines[lid] = r
                extra = f"〔龙套 {r['cid']}〕" if r.get("cid") in extras else ""
                body.append(f"    {lid} {r['who']}{extra}{'（' + r['tag'] + '）' if r['tag'] else ''}：{r['text']}")
        body.append("")
    ex = "\n".join(f"- {cid}：{d}" for cid, d in extras.items()) or "（没有）"
    res = vlm.ask_json(SCRIPT_POLISH_PROMPT.format(extras=ex, summary=ep["story"].get("summary") or "（未知）",
                                                   body="\n".join(body)), [], max_tokens=16000)
    if not isinstance(res, dict):
        return 0
    by_no = {b["no"]: b for b in blocks}
    for h in res.get("scenes") or []:
        b = by_no.get(str(h.get("no", "")))
        if not b:
            continue
        if h.get("time") in ("日", "夜", "晨", "昏"):
            b["time"] = h["time"]
        if h.get("place") in ("内", "外"):
            b["place"] = h["place"]
        loc = re.sub(r"[（(][^）)]*[）)]", "", str(h.get("location") or "")).strip()
        if 0 < len(loc) <= 16:
            b["where"] = loc
    n = 0
    for a in res.get("actions") or []:
        r = acts.get(str(a.get("id", "")))
        if r is None or not isinstance(a.get("text"), str) or len(a["text"]) > 120:
            continue
        r["text"], r["drop"] = a["text"].strip(), not a["text"].strip()
        n += 1
    fixed, voices = {}, {}  # subtitle key -> cleaned text / the extra who said it
    for x in res.get("lines") or []:
        r = lines.get(str(x.get("id", "")))
        if r is None:
            continue
        t = x.get("text")
        if isinstance(t, str) and t.strip() != r["text"] and _only_cut(r["text"], t.strip()):
            o, t = _bare(r["text"]), _bare(t)
            cut = [o[i:j] for op, i, j, _, _ in difflib.SequenceMatcher(None, o, t, autojunk=False).get_opcodes()
                   if op == "delete"]
            r["text"] = _strip(r["text"], cut)
            r["drop"] = not r["text"]
            fixed.update({k: cut for k in r["keys"]})
        w = x.get("who")
        if r.get("cid") in extras and isinstance(w, str) and 1 < len(w.strip()) <= 8:
            r["who"] = w.strip()
            voices.update({k: r["who"] for k in r["keys"]})
    for b in blocks:
        b["rows"] = [r for r in b["rows"] if not r.get("drop")]
    rename = {name(ep["ep"], cid): str(v).strip() for cid, v in (res.get("names") or {}).items()
              if cid in extras and isinstance(v, str) and 1 < len(v.strip()) <= 8}
    swap = lambda w: rename.get(w, w)
    for b in blocks:
        people = []
        for w in b["people"]:  # an extra id standing for several people: the names its lines got here
            people += [r["who"] for r in b["rows"] if r["kind"] == "line" and r.get("cid") in extras
                       and name(ep["ep"], r["cid"]) == w] or [w]
        b["people"] = list(dict.fromkeys(map(swap, people)))
        for r in b["rows"]:
            if r["kind"] == "line":
                r["who"] = swap(r["who"])
        for x in b["board"]:
            x["who"] = list(map(swap, x["who"]))
            for l in x["lines"]:
                l["who"] = swap(next((voices[k] for k in l.get("keys", ()) if k in voices), l["who"]))
                cut = [c for k in l.get("keys", ()) for c in fixed.get(k, ())]
                if cut:
                    l["text"] = _strip(l["text"], cut)
            x["lines"] = [l for l in x["lines"] if _bare(l["text"])]
    LOG.info("第 %d 集剧本润色：动作 %d/%d 段，场次 %d，龙套称呼 %s", ep["ep"], n, len(acts), len(res.get("scenes") or []),
             "、".join(f"{a}→{b}" for a, b in rename.items()) or "无")
    return n


def merge_scenes(blocks: list[dict], ep: int) -> list[dict]:
    """Join scene pieces a screenplay writes as one: back-to-back scenes with the same heading, and a scene of a
    shot or two whose last line runs on into the next (the voice over the cut). Renumbers them 1, 2, …"""
    out: list[dict] = []
    for b in blocks:
        p = out[-1] if out else None
        head = lambda x: (x["time"], x["place"], x["flashback"])
        same = p and head(p) == head(b) and similar_text(p["where"], b["where"]) >= 0.75  # 四合院门前 / 四合院门外
        last = next((r for r in reversed(p["rows"]) if r["kind"] == "line"), None) if p else None
        runs_on = (p and p["flashback"] == b["flashback"] and len(p["board"]) <= 2 and last
                   and not re.search(r"[。？！…”]$", last["text"]))
        if not (same or runs_on):
            out.append(b)
            continue
        if not same:  # the short piece takes the heading of the scene it belongs to
            p.update({k: b[k] for k in ("time", "place", "where", "raw_where", "raw_time")})
        rows = b["rows"]
        nxt = next((i for i, r in enumerate(rows) if r["kind"] != "action"), None)
        if runs_on and nxt is not None and rows[nxt]["kind"] == "line" and (rows[nxt]["who"], rows[nxt]["tag"]) == (
                last["who"], last["tag"]):
            last["text"] = _join(last["text"], rows[nxt]["text"])
            last["keys"] = last.get("keys", []) + rows[nxt].get("keys", [])
            rows = rows[:nxt] + rows[nxt + 1:]
        for r in rows:
            q = p["rows"][-1] if p["rows"] else {}
            if r["kind"] == "action" and q.get("kind") == "action":
                q["text"] = q["text"].rstrip("。") + "。" + r["text"]
            else:
                p["rows"].append(r)
        p["people"] = list(dict.fromkeys(p["people"] + b["people"]))
        p["board"] += b["board"]
        p["shots"] = [p["shots"][0], b["shots"][1]]
    for k, b in enumerate(out, 1):
        b["no"] = f"{ep}-{k}"
    return out


def _extras(ep: dict, chars: list[dict]) -> dict[str, str]:
    """Characters of an episode without a proper name in the cast (series extras, 龙套): id -> what they are."""
    named = {m for c in chars for m in c["members"] if c.get("name") and c.get("importance") != "extra"}
    return {c["id"]: f"{c.get('role', '')}（{c.get('appearance', '')[:40]}）" for c in ep["story"].get("characters") or []
            if "crowd" not in c["id"] and (ep["ep"], c["id"]) not in named}


def _sec(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _heading(b: dict) -> str:
    return "　".join(x for x in (b["no"], b["time"], b["place"], b["where"], "（回忆）" if b["flashback"] else "") if x)


def build(root: Path, punct: bool = True) -> dict:
    """punct: the two text calls per episode (punctuation, polish); False gives the screenplay as the breakdown has it."""
    eps, chars = _cast(root)
    if not eps:
        raise RuntimeError(f"{root} 还没有拆好的集：先 rea-toon analyze / series")
    name = _namer(chars, eps)
    out_dir = layout.characters(root)
    for c in chars:
        if "crowd" in c["id"]:
            continue
        try:
            c["pics"] = {k: layout.from_page(root, v) for k, v in pictures(eps, c, out_dir).items()}
        except Exception as exc:  # a character without a picture is still in the script
            LOG.warning("角色 %s 的图没取到：%s", c["id"], exc)
        try:
            v = voice(eps, c, out_dir)
            if v:
                c["voice"] = {**v, "src": layout.from_page(root, v["src"])}
        except Exception as exc:  # nor is one whose voice could not be cut
            LOG.warning("角色 %s 的配音样本没切出来：%s", c["id"], exc)
    title = ""
    if layout.series(root).is_file():
        from . import series
        title = series.load(root).get("title", "")
    title = title or (eps[0]["story"].get("source") or {}).get("title") or root.name
    out = {"title": title, "characters": chars, "episodes": []}
    from .vlm import NeedAgent
    waiting = None  # agent backend: ask every episode's question before stopping, not one episode per run
    for e in eps:
        marks, asked = {}, False
        if punct:
            try:
                marks = punctuate(e, name)
            except NeedAgent as exc:
                waiting, asked = exc, True
            except Exception as exc:  # no model configured / it failed: the lines as the subtitles have them
                LOG.warning("第 %d 集台词没加上标点：%s", e["ep"], exc)
        extras = _extras(e, chars)
        blocks = scenes(e, name, root, marks, extras)
        if punct and not asked:  # the polish reads the punctuated lines: asked once those are in
            try:
                polish(e, blocks, extras, name)
            except NeedAgent as exc:
                waiting = exc
            except Exception as exc:  # the screenplay as the shot breakdown has it
                LOG.warning("第 %d 集剧本没润色：%s", e["ep"], exc)
        blocks = merge_scenes(blocks, e["ep"])
        out["episodes"].append({"ep": e["ep"], "summary": e["story"].get("summary", ""),
                                "duration": e["story"].get("duration", 0), "scenes": blocks})
    if waiting:
        raise waiting
    _same_cards([b for e in out["episodes"] for b in e["scenes"]])
    return out


def _role_line(c: dict) -> str:
    return " · ".join(x for x in (IMPORTANCE_CN.get(c.get("importance"), c.get("importance") or ""),
                                  f"出场 {len(c.get('episodes') or [1])} 集", f"{c['shots']} 镜") if x)


def _line_text(r: dict) -> tuple[str, str]:
    """("容遇（OS）", "（生气）这死孩子……") for a screenplay line."""
    who = r["who"] + (f"（{r['tag']}）" if r["tag"] else "")
    return who, (f"（{r['mood']}）" if r.get("mood") else "") + r["text"]


# ---- the screenplay: script.md / script.html / script.pdf ----

def to_md(sc: dict) -> str:
    L = [f"# {sc['title']}", "", "## 人物", ""]
    L += [f"- **{c['label']}**：{c.get('role', '')}" + (f"（配音样本 {c['voice']['src']}）" if c.get("voice") else "") for c in sc["characters"]]
    L.append("")
    for e in sc["episodes"]:
        L += [f"## 第 {e['ep']} 集", "", f"梗概：{e['summary']}", ""]
        for b in e["scenes"]:
            L += [f"### {_heading(b)}", "", f"人物：{'、'.join(b['people']) or '无'}", ""]
            for r in b["rows"]:
                if r["kind"] == "action":
                    L.append(f"△ {r['text']}")
                elif r["kind"] == "card":
                    L.append(f"【字幕】{r['text']}")
                else:
                    who, text = _line_text(r)
                    L.append(f"{who}：{text}")
                L.append("")
    return "\n".join(L) + "\n"


PRINT = """.vo{display:block;margin-top:6px}.vo audio{display:block;width:100%;max-width:260px;height:30px}
.vo .said{display:block;margin-top:4px;font-size:12px;color:var(--muted)}.vo .said span{display:block}
@page{size:A4;margin:16mm 14mm}
@media print{:root{--bg:#fff;--card:#fff;--fg:#111;--muted:#555;--line:#ddd;--chip:#f2f2f2;--must:#c2410c;--series:#1d4ed8}
main{max-width:none;padding:0}.toc,#tip,.vo{display:none}.ep{break-before:page;margin-top:0}}"""

SCRIPT_CSS = """
@media screen{main>*{max-width:820px}}.toc{display:flex;flex-wrap:wrap;gap:6px 14px}.toc a{color:var(--series);text-decoration:none}
.title{margin:0 0 20px}
.people{columns:2 280px;column-gap:28px;margin:0}.people p{margin:0 0 8px;break-inside:avoid;display:flex;gap:10px;align-items:flex-start}
.people img{width:40px;height:40px;border-radius:50%;object-fit:cover;flex:none}.people b{display:block}
.ep{margin-top:48px}.ep>h2{border:0;font-size:20px}
@media print{.title{text-align:center;margin:12px 0 28px}.title h1{font-size:26px;letter-spacing:.08em}.ep>h2{text-align:center;letter-spacing:.1em}}
.syn{color:var(--muted);font-size:13px;margin:0 0 8px}
.sc{margin:30px 0 0}.sc h3{font-size:15.5px;margin:0 0 4px;font-weight:800;letter-spacing:.04em}
.sc .who{margin:0 0 12px;font-size:13.5px}
.act{margin:10px 0;line-height:1.75}
.ln{margin:10px 0;line-height:1.75;padding-left:2em;text-indent:-2em}.ln b{font-weight:700}.ln .md{color:var(--muted)}
.cd{margin:10px 0;color:var(--muted)}
body{font-size:15px}
@media print{body{font-size:12.5px}.sc{break-inside:auto}.sc h3{break-after:avoid}}
"""


def _voice_html(c: dict, full: bool = False) -> str:
    """The character's voice sample as something to play; full: with the lines it is cut from."""
    v = c.get("voice")
    if not v:
        return ""
    said = "".join(f"<span>第 {x['ep']} 集「{esc(x['text'])}」</span>" for x in v["lines"])
    tip = esc(" / ".join(x["text"] for x in v["lines"]))
    return (f"<span class=vo title='配音样本（原片人声轨）：{tip}'><audio controls preload=none src='{esc(v['src'])}'></audio>"
            + (f"<span class=said>配音样本 {v['seconds']:g}s：{said}</span>" if full else "") + "</span>")


def script_html(sc: dict) -> str:
    B = [f"<style>{SCRIPT_CSS}{PRINT}</style>", f"<div class=title><h1>{esc(sc['title'])}</h1>"
         f"<p class=muted>剧本 · 共 {len(sc['episodes'])} 集</p></div>",
         "<p class=toc>" + "".join(f"<a href='#ep{e['ep']}'>第 {e['ep']} 集</a>" for e in sc["episodes"])
         + "<a href='storyboard.html'>分镜脚本 →</a></p>", "<h2>人物</h2><div class=people>"]
    for c in sc["characters"]:
        face = (c.get("pics") or {}).get("face")
        B.append(f"<p>{f'<img src={esc(face)!r} alt=>' if face else ''}<span><b>{esc(c['label'])}</b>"
                 f"<span class=muted>{esc(c.get('role'))}</span>{_voice_html(c)}</span></p>")
    B.append("</div>")
    for e in sc["episodes"]:
        B += [f"<section class=ep id=ep{e['ep']}><h2>第 {e['ep']} 集</h2>",
              f"<p class=syn>梗概：{esc(e['summary'])}</p>"]
        for b in e["scenes"]:
            B.append(f"<div class=sc><h3>{esc(_heading(b))}</h3><p class=who>人物：{esc('、'.join(b['people']) or '无')}</p>")
            for r in b["rows"]:
                if r["kind"] == "action":
                    B.append(f"<p class=act>△ {esc(r['text'])}</p>")
                elif r["kind"] == "card":
                    B.append(f"<p class=cd>【字幕】{esc(r['text'])}</p>")
                else:
                    mood = f"<span class=md>（{esc(r['mood'])}）</span>" if r.get("mood") else ""
                    tag = f"（{esc(r['tag'])}）" if r["tag"] else ""
                    B.append(f"<p class=ln><b>{esc(r['who'])}{tag}：</b>{mood}{esc(r['text'])}</p>")
            B.append("</div>")
        B.append("</section>")
    return page(f"{sc['title']} · 剧本", B)


# ---- the shot script: storyboard.html / storyboard.pdf ----

BOARD_CSS = """
.toc{display:flex;flex-wrap:wrap;gap:6px 14px}.toc a{color:var(--series);text-decoration:none}
.cast{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(240px,1fr))}
.cast .card{break-inside:avoid}.pp{display:flex;gap:6px;margin-bottom:8px}
.pp img{width:50%;aspect-ratio:1;object-fit:cover;border-radius:8px;background:var(--chip)}.pp img.look{object-fit:contain}
.cast .nm{font-size:16px;font-weight:700;margin:0}.cast p{margin:0}
.ep{margin-top:44px}.syn{color:var(--muted);margin:0 0 4px}
.sh{margin:22px 0 6px;font-weight:800;font-size:15px}.sh span{font-weight:400;color:var(--muted);font-size:13px;margin-left:10px}
table.sb{table-layout:fixed}.sb th,.sb td{font-size:12.5px}.sb img{width:100%;border-radius:4px;display:block}.sb img[data-a]{cursor:pointer}
.sb .c-no{width:3.4em}.sb .c-pic{width:64px}.sb .c-size{width:4.6em}.sb .c-line{width:30%}.sb .c-sfx{width:4.5em}
.sb .c-mo{width:5.6em}.sb .c-t{width:3.6em}
.sb td.no{font-weight:700}.sb tr.kp td.no{box-shadow:inset 3px 0 var(--must)}
.sb .bt{display:inline-block;white-space:nowrap;font-size:10.5px;color:var(--must);font-weight:600;margin-right:4px}
.sb .wh{color:var(--muted);font-size:11.5px;display:block;margin-top:2px}.sb .l{margin:0 0 3px}.sb .l b{font-weight:600}
@media (max-width:700px){.sb .c-pic,.sb td.pic{display:none}}
@media print{.sb tr{break-inside:avoid}.sh{break-after:avoid}}
"""


def board_html(sc: dict) -> str:
    n_shots = sum(len(b["board"]) for e in sc["episodes"] for b in e["scenes"])
    B = [f"<style>{BOARD_CSS}{PRINT}</style>", f"<h1>{esc(sc['title'])} · 分镜脚本</h1>",
         f"<p class=muted>{len(sc['episodes'])} 集 · {n_shots} 镜。动效：全动 / 局部动 / 静图推拉，后面是运镜和特效；"
         "音效是从原片音轨里听出来的提示音，仅供参考；左边红线是必保镜头，镜号旁的小字是这一镜的戏剧作用。点缩略图播原片的这一镜。</p>",
         "<p class=toc><a href='#cast'>角色设定</a>" + "".join(f"<a href='#ep{e['ep']}'>第 {e['ep']} 集</a>" for e in sc["episodes"])
         + "<a href='script.html'>← 剧本</a></p>", "<h2 id=cast>角色设定</h2><div class=cast>"]
    for c in sc["characters"]:
        pics = c.get("pics") or {}
        imgs = "".join(f"<img class={k} src='{esc(v)}' alt='{esc(c['label'])}'>" for k, v in pics.items())
        B.append(f"<div class=card>{'<div class=pp>' + imgs + '</div>' if imgs else ''}"
                 f"<p class=nm>{esc(c['label'])}</p><p class=muted>{esc(c.get('role'))}</p>"
                 f"<p style='margin-top:6px'>{esc(c['appearance'])}</p>{_voice_html(c, True)}"
                 f"<p style='margin-top:6px'><span class=chip>{esc(_role_line(c))}</span></p></div>")
    B.append("</div>")
    head = ("<colgroup><col class=c-no><col class=c-pic><col class=c-size><col><col class=c-line><col class=c-sfx>"
            "<col class=c-mo><col class=c-t></colgroup><tr><th>镜号</th><th>画面</th><th>景别</th><th>画面描述</th>"
            "<th>台词 / 旁白 / OS</th><th>音效</th><th>动效</th><th>时长</th></tr>")
    for e in sc["episodes"]:
        B += [f"<section class=ep id=ep{e['ep']}><h2>第 {e['ep']} 集 <span class=muted>{_sec(e['duration'])}</span></h2>",
              f"<p class=syn>{esc(e['summary'])}</p>"]
        for b in e["scenes"]:
            B.append(f"<p class=sh>{esc(_heading(b))}<span>{_sec(b['start'])} 起 · {len(b['board'])} 镜</span></p>")
            rows = []
            for x in b["board"]:
                lines = "".join(f"<p class=l><b>{esc(l['who'])}{('（' + esc(l['tag']) + '）') if l['tag'] else ''}：</b>"
                                f"{esc(l['text'])}</p>" for l in x["lines"])
                beat = "".join(f"<span class=bt>{esc(t)}</span>" for t in x["beat"])
                who = f"<span class=wh>{esc('、'.join(x['who']))}</span>" if x["who"] else ""
                play = (f" data-v='{esc(x['clip'])}' data-a={x['t']:.2f} data-b={x['t'] + x['dur']:.2f} data-title='第 {e['ep']} 集 镜 {x['no']}' "
                        f"data-cap='{esc(x['picture'])}' title='点开播这一镜'" if x.get("clip") else "")
                pic = f"<img loading=lazy src='{esc(x['thumb'])}' alt=''{play}>" if x["thumb"] else ""
                size = esc(x["size"]) + (f"<br><span class=muted>{esc(x['angle'])}</span>" if x["angle"] else "")
                rows.append(f"<tr{' class=kp' if x['must'] else ''}><td class=no>{x['no']}<br>{beat}</td>"
                            f"<td class=pic>{pic}</td><td>{size}</td><td>{esc(x['picture'])}{who}</td><td>{lines}</td>"
                            f"<td>{esc('、'.join(x['sfx']))}</td><td>{esc(x['motion'])}</td>"
                            f"<td>{x['dur']:.1f}s</td></tr>")
            B.append(f"<div class=tw><table class=sb>{head}{''.join(rows)}</table></div>")
        B.append("</section>")
    return page(f"{sc['title']} · 分镜脚本", B, player(None))


# ---- files ----

def _chrome() -> str | None:
    for c in (os.environ.get("REA_TOON_CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser")):
        if c and Path(c).exists():
            return c
    return None


def to_pdf(html_path: Path) -> Path | None:
    """The page next to it as .pdf, printed by a headless Chrome (none found: no PDF)."""
    chrome = _chrome()
    if not chrome:
        LOG.warning("没找到 Chrome/Chromium，跳过 PDF（设 REA_TOON_CHROME 指定浏览器）")
        return None
    dst = html_path.with_suffix(".pdf")
    try:
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--allow-file-access-from-files",
                        "--run-all-compositor-stages-before-draw", "--virtual-time-budget=20000",
                        f"--print-to-pdf={dst}", html_path.resolve().as_uri()],
                       check=True, capture_output=True, timeout=600)
    except (subprocess.SubprocessError, OSError) as exc:
        LOG.warning("%s 没生成：%s", dst.name, exc)
        return None
    return dst if dst.is_file() else None


def write(root: Path, pdf: bool = True, punct: bool = True) -> dict:
    sc = build(root, punct=punct)
    layout.put(layout.page(root, "script.md"), to_md(sc))
    pages = {"script": layout.page(root, "script.html"), "storyboard": layout.page(root, "storyboard.html")}
    layout.put(pages["script"], script_html(sc))
    layout.put(pages["storyboard"], board_html(sc))
    sc["files"] = list(pages.values())
    if pdf:
        sc["files"] += [p for p in map(to_pdf, pages.values()) if p]
    for p in pages.values():
        stamp(p)  # after the PDFs: what ties the pages of a series together is for the screen
    site(root)  # the series' index.html now offers the script and the shot script
    return sc
