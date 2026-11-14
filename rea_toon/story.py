"""story.json: the reviewed "source" of one episode, and everything that reads it (where it sits: layout.py).

Every stage reads and writes it through load / save. The text views (cast, shot table) feed the prompts; the look
queries (who wears what in a shot) feed the checks; the lock state says whether a reviewer approved it.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path

from . import layout
from .subtitles import MODE_CN, line_mode

ROLES = ("hook", "setup", "conflict", "reversal", "reveal", "climax", "payoff", "cliffhanger", "reaction",
         "transition", "normal")
ROLE_CN = {"hook": "钩子", "setup": "铺垫", "conflict": "冲突", "reversal": "反转", "reveal": "揭露", "climax": "高潮",
           "payoff": "爽点", "cliffhanger": "结尾钩子", "reaction": "反应", "transition": "转场", "normal": "普通"}
PRIORITY_CN = {"must": "必保", "high": "重要", "normal": "普通"}
# What the audience feels at a shot or beat (the emotion ledger, rea_toon/emotion.py). 憋 and 悬 build pressure,
# the rest are the hits people stay for; "" = the shot only tells or moves the plot on.
FEELS = ("爽", "憋", "虐", "甜", "笑", "悬", "惊", "燃")
TRANSITIONS = ("cut", "white_flash", "fade", "dissolve", "whip", "match")


# ---- read and write ----

def load(work: Path) -> dict:
    return json.loads(layout.story(work).read_text(encoding="utf-8"))


def save(work: Path, story: dict) -> None:
    layout.put(layout.story(work), json.dumps(story, ensure_ascii=False, indent=1))


# ---- text views of the story, shared by the prompts ----

def prior_text(story: dict) -> str:
    """What earlier episodes left for this one (series.prior_of), for the prompts that read the drama."""
    p = story.get("prior") or {}
    if not p.get("recap"):
        return "（单集，或这是第一集：没有前情）"
    return f"这是第 {p.get('ep')} 集。\n{p['recap']}" + (f"\n上一集结尾留的钩子：{p['hook']}" if p.get("hook") else "")


def lines_of(shot: dict) -> list[dict]:
    return [x for x in shot.get("subtitles") or [] if not x.get("card") and x.get("speaker") != "title"]


def said_tag(x: dict) -> str:
    """How a line is spoken, as a short tag: (画外) / (内心独白) / (旁白); nothing for a line said on camera."""
    tag = MODE_CN[line_mode(x)]
    return f"({tag})" if tag else ""


def gap_line(g: dict) -> str:
    """One information gap as a sentence: who knows, who does not, whether this episode opens it."""
    who = lambda xs: "、".join(str(x) for x in xs) if isinstance(xs, list) else str(xs or "")
    return (f"{g.get('what')}：{who(g.get('knows'))} 知道，{who(g.get('blind'))} 不知道；"
            f"{'镜 ' + str(g['to']) + ' 揭开' if g.get('to') else '这一集没揭开'}。{g.get('play') or ''}")


def cast_lines(story: dict) -> str:
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
    lines = " / ".join(f"{x.get('speaker', '?')}{said_tag(x)}：{x['text']}" for x in lines_of(s)) or "无台词"
    cols = [str(s["index"]), f"{s['start']:.1f}-{s['end']:.1f}s", a.get("shot_size", ""),
            a.get("camera_move", "") if emotion else a.get("scene", ""), who, lines]
    if emotion:
        cols.append(a.get("emotion", ""))
    return "|".join(str(c) for c in cols)


def shot_table(story: dict, shots: list[dict] | None = None, emotion: bool = False) -> str:
    return "\n".join(_shot_row(s, emotion) for s in shots or story["shots"])


# ---- people ----

def names_of(c: dict) -> set[str]:
    """Names a character goes by: the id and anything in brackets of the role, e.g. 姐姐（温月） -> 温月."""
    return {c["id"], *re.findall(r"[（(]([^）)]+)[）)]", str(c.get("role", "")))} - {""}


def name_in(c: dict, said: str, cards: str = "") -> str:
    """What to call a character in the script. The role should hold the name in brackets (姐姐（温月）), but models
    also write it the other way round (温月（女主的姐姐，大小姐））. Short words of the role, in this order: one on a
    name card; brackets that hold nothing but a short word; one the episode says (a line), in the brackets first;
    the short word before the brackets; one in the brackets; else whatever is in the brackets (the old rule)."""
    role = str(c.get("role", ""))
    short = lambda w: 2 <= len(w) <= 4 and re.fullmatch(r"[\u4e00-\u9fff·]+", w)
    words = lambda p: [w for w in re.split(r"[，,、/；;：:\s]+", p) if short(w)]
    brackets = re.findall(r"[（(]([^）)]+)[）)]", role)
    inside, head = [w for p in brackets for w in words(p)], words(re.split(r"[（(]", role)[0])
    return (next((w for w in head + inside if w in cards), None) or next((p for p in brackets if short(p)), None)
            or next((w for w in inside + head if w in said), None) or next(iter(head), None) or next(iter(inside), None)
            or next(iter(sorted(names_of(c) - {c["id"]})), ""))


def portraits(story: dict, work: Path, cid: str, k: int = 2) -> list[Path]:
    """The clearest shots of one character: face visible, closest framing first."""
    order = ("特写", "近景", "中景")
    best = [s for s in story["shots"] if any(x.get("id") == cid and x.get("visibility") in ("face", "profile")
                                             for x in (s.get("analysis") or {}).get("characters") or [])]
    best.sort(key=lambda s: next((i for i, w in enumerate(order) if w in str((s.get("analysis") or {}).get("shot_size", ""))), 9))
    return [work / s["frames"]["mid"] for s in best[:k]]


# ---- looks: what each person wears in a shot, from the scene breakdown ----

def scene_of(story: dict, index: int) -> dict | None:
    for sc in (story.get("breakdown") or {}).get("scenes") or []:
        if sc["shots"][0] <= index <= sc["shots"][1]:
            return sc
    return None


LOOK_PARTS = ("wear", "hair", "face", "props")


def day_tag(sc: dict) -> str:
    return (f" · 剧情日 {sc['day']}" if sc.get("day") else "") + (" · 回忆" if sc.get("flashback") else "")


def look_split(v) -> dict:
    if isinstance(v, dict):
        return {k: str(v.get(k) or "").strip() for k in LOOK_PARTS}
    return {"wear": str(v or "").strip(), "hair": "", "face": "", "props": ""}  # older / hand-written plain text


def story_day(sc: dict) -> str:
    """Story day of a scene (D1, N1, D2 …); flashbacks get their own key, they never share a look with the present.
    A flashback labelled only "回忆" has no day: two such scenes can be years apart."""
    d = str(sc.get("day") or "").strip()
    if sc.get("flashback"):
        return f"{d}*" if re.search(r"\d", d) else ""
    return d


def differs(a: str, b: str) -> bool:
    """Two descriptions of an outfit that do not look like the same clothes (the model words one outfit many ways)."""
    return difflib.SequenceMatcher(None, a, b).ratio() < 0.45


def has_look(sc: dict, cid: str) -> bool:
    return cid in (sc.get("looks") or {}) or any(c.get("who") == cid for c in sc.get("changes") or [])


def look_parts(story: dict, cid: str, index: int) -> dict:
    """A character's look in one shot from the scene breakdown, as wear / hair / face / props ({} if unknown).
    Not described in this scene (only a hand or a back in frame, say): the look they ended the latest earlier
    scene of the same story day with, since nobody changes clothes off screen within a day without the story saying so."""
    sc = scene_of(story, index)
    if not sc:
        return {}
    if not has_look(sc, cid):
        scenes = (story.get("breakdown") or {}).get("scenes") or []
        same = [x for x in scenes if x["shots"][1] < sc["shots"][0] and story_day(x) and story_day(x) == story_day(sc) and has_look(x, cid)]
        return look_parts(story, cid, same[-1]["shots"][1]) if same else {}
    look = look_split((sc.get("looks") or {}).get(cid))
    for ch in sorted(sc.get("changes") or [], key=lambda c: int(c.get("shot", 0))):
        if ch.get("who") == cid and int(ch.get("shot", 0)) <= index:
            new = look_split(ch if any(ch.get(k) for k in LOOK_PARTS) else ch.get("look"))
            look = {k: new[k] or (look[k] if k != "face" and k != "props" else new[k]) for k in LOOK_PARTS}
    return look


def look_in(story: dict, cid: str, index: int, dress: bool = False) -> str:
    """The look as one line. dress=True is what is worn rather than who wears it: clothes, face extras
    (face mask, wine) and props; hair only when it is a wig (the new character brings its own hair or fur)."""
    p = look_parts(story, cid, index)
    if not p:
        return ""
    keep = [p["wear"], p["hair"] if not dress or "假发" in p["hair"] else "", p["face"], p["props"]]
    return "；".join(x for x in keep if x)


def look_line(v) -> str:
    p = look_split(v)
    return "，".join(f"{n}{p[k]}" for k, n in (("wear", ""), ("hair", "发型："), ("face", "脸上："), ("props", "道具："))
                    if p[k])


# ---- lock: approval of a reviewed breakdown ----

def digest(story: dict) -> str:
    body = {k: v for k, v in story.items() if k != "lock"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def lock_state(story: dict) -> str:
    """locked | stale (edited after lock) | unlocked"""
    lk = story.get("lock") or {}
    if not lk:
        return "unlocked"
    return "locked" if lk.get("hash") == digest(story) else "stale"


def require_locked(story: dict) -> None:
    state = lock_state(story)
    if state == "unlocked":
        raise RuntimeError("剧本拆解还没确认：先 `rea-toon analyze WORK`，审 WORK/pages/breakdown.html，"
                           "改好后 `rea-toon lock WORK`")
    if state == "stale":
        raise RuntimeError("story.json 在 lock 之后被改过：审一下改动（WORK/pages/breakdown.html），再 `rea-toon lock WORK`")
