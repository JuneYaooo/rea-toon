"""Acting: the per-second acting beats of each shot, physical contacts and when they land, each character's
expressions. The acting and contact passes are what `deepen` adds to a fast analysis."""

from __future__ import annotations

import logging
from pathlib import Path

from .. import layout, media
from ..prompts import CONTACT_PROMPT, EXPRESSIONS_PROMPT, PERFORMANCE_PROMPT
from ..vlm import for_work, Pool, VLM

LOG = logging.getLogger(__name__)


def add_performance(story: dict, work: Path, vlm: VLM, workers: int = 6) -> int:
    """The per-second acting pass for every shot with a main character that does not have it yet."""
    main = {c["id"] for c in story["characters"] if c.get("importance") != "extra"}
    acting = [s for s in story["shots"] if main & {c.get("id") for c in (s.get("analysis") or {}).get("characters") or []}
              and (not s.get("performance") or s["performance"].get("error"))]
    with Pool(workers) as ex:
        for s, res in zip(acting, ex.map(lambda s: performance(work, story, s, vlm), acting)):
            s["performance"] = res
    return len(acting)


def performance(work: Path, story: dict, shot: dict, vlm: VLM | None = None, n: int = 6) -> dict:
    """Time-coded acting beats of one shot (what the face, mouth and body do, second by second)."""
    vlm = vlm or for_work(work)
    dur = shot["duration"]
    n = max(3, min(n, int(dur / 0.4) + 1))
    times = [round(dur * (k + 0.5) / n, 2) for k in range(n)]
    frames = []
    for k, t in enumerate(times):
        p = layout.frames(work, "perf", f"{shot['id']}_{k}.jpg")
        if not p.is_file():
            media.frame_at(layout.source(work), shot["start"] + t, p)
        frames.append(p)
    subs = " / ".join(f"[{max(0, x['start'] - shot['start']):.1f}-{min(dur, x['end'] - shot['start']):.1f}s] {x['text']}"
                      for x in shot.get("subtitles", [])) or "（无）"
    chars = {c["id"]: c for c in story.get("characters", [])}
    cast = "\n".join(f"- {c.get('id')}：{chars.get(c.get('id'), {}).get('role', '')}，{c.get('where', '')}"
                      for c in (shot.get("analysis") or {}).get("characters") or [] if c.get("id") != "crowd")
    prompt = PERFORMANCE_PROMPT.format(n=n, duration=dur, times="、".join(f"{t}s" for t in times),
                                       subtitles=subs, cast=cast or "（无）")
    try:
        return vlm.ask_json(prompt, frames, max_tokens=4096)
    except Exception as exc:
        LOG.warning("shot %s 表演拆解失败：%s", shot["index"], exc)
        return {"error": str(exc)}


CONTACT_WORDS = ("扇", "打了", "打向", "拍桌", "拍了", "摔", "推", "踢", "抱", "递", "砸", "泼", "撞", "亲吻", "吻",
                 "抓住", "抓着", "拽", "扯", "掐", "握住", "碰杯", "关门", "甩", "扔", "掀", "撕")


def contacts(story: dict, work: Path, vlm: VLM, workers: int = 6) -> int:
    """Physical contacts (slap, splash, grab, push, hand-over) and the moment they land, per shot. The picture says
    which shots have one and roughly when (8 frames); a sharp onset in the non-vocal stem within 0.3 s pins it
    to the hit the original mix plays there: the action lands on that moment."""
    music = (story.get("audio") or {}).get("music")
    hits = media.transients(work / music) if music and (work / music).is_file() else []

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
            f = layout.frames(work, f"{s['id']}_c{j}.jpg")
            if not f.is_file():
                media.frame_at(layout.source(work), s["start"] + t, f)
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

    with Pool(workers) as ex:
        for s, cs in zip(story["shots"], ex.map(one, story["shots"])):
            if cs is None:
                s["contacts_error"] = True
            else:
                s.pop("contacts_error", None)
                s["contacts"] = cs
    return sum(len(s.get("contacts") or []) for s in story["shots"])


def expressions(story: dict, vlm: VLM) -> int:
    """The expressions each main character plays in this episode (from the per-shot analysis), merged into a short
    list with the shots of each: how many moods carry the episode."""
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
