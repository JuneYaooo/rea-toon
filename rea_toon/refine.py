"""Story-level passes over a perceived story (stage 2): fix OCR slips in the subtitles, say who speaks each line,
and merge one person the shot pass split into two ids (disguise, costume change)."""

from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path

from . import layout
from . import subtitles as subs_mod
from .prompts import IDENTITY_PROMPT, LINE_SPEAKER_PROMPT, SPEAKER_CHECK_PROMPT, SUBS_PROOFREAD_PROMPT
from .story import names_of, portraits
from .vlm import VLM, Pool

LOG = logging.getLogger(__name__)


# ---- subtitles ----

def proofread(subs: list[dict], vlm: VLM, summary: str = "") -> list[dict]:
    """Fix OCR look-alike errors (百先→首先, 目己→自己) once; the reburned subtitles and every prompt use this text.
    The OCR reading is kept in `ocr` for lines that changed."""
    if not subs or all(x.get("checked") for x in subs):
        return subs
    lines = "\n".join(f"{i}: {x['text']}" for i, x in enumerate(subs))
    try:
        res = vlm.ask_json(SUBS_PROOFREAD_PROMPT.format(summary=summary or "（未知）", lines=lines), [], max_tokens=4096)
    except Exception as exc:  # keep the OCR text; next run tries again
        LOG.warning("字幕校对失败：%s", exc)
        return subs
    for f in res.get("fixes") or []:
        i, text = int(f.get("i", -1)), str(f.get("text", "")).strip()
        if 0 <= i < len(subs) and text and text != subs[i]["text"]:
            LOG.info("字幕校对 #%d：%s → %s", i, subs[i]["text"], text)
            subs[i] = {**subs[i], "ocr": subs[i].get("ocr", subs[i]["text"]), "text": text}
    return [{**x, "checked": True} for x in subs]


def proofread_story(story: dict, work: Path, vlm: VLM) -> int:
    """Proofread subtitles.json of an analysed work dir and carry the fixes into the story (no re-analysis)."""
    subs_path = layout.data(work, "subtitles.json")
    subs = proofread(json.loads(subs_path.read_text(encoding="utf-8")), vlm, story.get("summary", ""))
    layout.put(subs_path, json.dumps(subs, ensure_ascii=False, indent=1))
    subs_mod.to_srt(subs, layout.data(work, "subtitles.srt"))
    fixed = {(x["start"], x["ocr"]): x["text"] for x in subs if "ocr" in x}
    n = 0
    for s in story["shots"]:
        for x in s.get("subtitles") or []:
            new = fixed.get((x["start"], x.get("ocr", x["text"])))
            if new and new != x["text"]:
                x.setdefault("ocr", x["text"])
                x["text"] = new
                n += 1
    return n


def resync_subtitles(story: dict, work: Path) -> dict:
    """Read the subtitle track again (text off the subtitle line is no longer taken for dialogue) and put it into an
    analysed story. Lines that are still there keep their speaker and proofread wording; returns
    {"dropped": [texts gone], "added": [lines new to the story that still need a speaker], "lines": count}."""
    subs_path = layout.data(work, "subtitles.json")
    old = json.loads(subs_path.read_text(encoding="utf-8")) if subs_path.is_file() else []
    subs, _ = subs_mod.carry(subs_mod.extract(layout.source(work)), old, ("checked",))
    if old:
        layout.put(layout.prev(work, "subtitles.json"), json.dumps(old, ensure_ascii=False, indent=1))
    layout.put(subs_path, json.dumps(subs, ensure_ascii=False, indent=1))
    subs_mod.to_srt(subs, layout.data(work, "subtitles.srt"))
    dropped, added = {}, {}
    for s in story["shots"]:
        new, gone = subs_mod.carry(subs_mod.in_span(subs, s["start"], s["end"]), s.get("subtitles") or [],
                                   ("speaker", "mode", "on_screen"))
        for o in gone:
            dropped[(o.get("start"), o["text"])] = o["text"]
        for x in new:
            if "speaker" not in x:
                added[(x["start"], x["text"])] = {"shot": s["index"], "start": x["start"], "text": x["text"]}
        s["subtitles"] = new
    mark_cards(story)
    flat = subs_mod.unique_lines(story)
    for i, x in enumerate(flat):  # a recovered line between two lines of one speaker is that speaker's
        a, b = (flat[i - 1] if i else {}).get("speaker"), (flat[i + 1] if i + 1 < len(flat) else {}).get("speaker")
        if "speaker" not in x and not x.get("card") and a and a == b and a != "title":
            for sh in story["shots"]:
                seen = {c.get("id") for c in (sh.get("analysis") or {}).get("characters") or []}
                for y in sh["subtitles"]:
                    if (y["start"], y["text"]) == (x["start"], x["text"]):
                        mode = "narration" if a == subs_mod.NARRATOR else "on" if a in seen else "off"
                        y.update(speaker=a, mode=mode, on_screen=mode == "on")
            added.pop((x["start"], x["text"]), None)
    for k in [k for k in added if any(x.get("card") and (x["start"], x["text"]) == k
                                      for s in story["shots"] for x in s["subtitles"])]:
        del added[k]
    kept = {(x["start"], x["text"]) for s in story["shots"] for x in s["subtitles"]}
    return {"dropped": [t for k, t in sorted(dropped.items(), key=lambda kv: kv[0][0] or 0)
                        if not any(t == x[1] or t in x[1] for x in kept)] ,
            "added": sorted(added.values(), key=lambda x: x["start"]), "lines": len(subs)}


def refine(story: dict, work: Path, vlm: VLM) -> dict:
    """Who says each line, then one id per person across costume changes (speakers first: the merge pass reads
    them, and renames the merged id inside them too)."""
    n = attribute_lines(story, vlm, work)
    merges = merge_identities(story, work, vlm)
    r = story.setdefault("refined", {})
    r.update(lines=n, merges=r.get("merges", []) + merges)
    return {"merges": merges, "lines": n}


# ---- who says each line ----

def _visible(s: dict) -> str:
    return ";".join(f"{c.get('id')}:{c.get('visibility', '')}" for c in (s.get("analysis") or {}).get("characters") or []) or "无人"


def mark_cards(story: dict) -> int:
    """Name cards / titles drawn far bigger than the dialogue (e.g. a character's name as she walks in) are not
    lines: nobody says them, so no mouth may move on them, and they say who that person is."""
    subs = [x for s in story["shots"] for x in s.get("subtitles") or [] if x.get("box") and x["text"].strip()]
    if len(subs) < 5:
        return 0
    cw = lambda x: (x["box"][2] - x["box"][0]) / max(1, len(x["text"].strip()))  # width per character
    tall = lambda x: x["box"][3] - x["box"][1]
    mw, mh = statistics.median(map(cw, subs)), statistics.median(map(tall, subs))
    n = 0
    for x in subs:
        if cw(x) > 1.8 * mw and tall(x) > 1.8 * mh:
            x["card"], x["speaker"], x["on_screen"] = True, "title", False
            n += 1
    return n


_MOUTH = ("face", "profile", "full")  # how a character must show for their mouth to be seen talking


def _shot_speaker(s: dict, ids: set[str]) -> str | None:
    """Who the shot pass (it looked at the frames) saw talking in this shot, when it named a character."""
    v = (s.get("analysis") or {}).get("speaker")
    return v if v in ids else None


def _mode(sp: str, kind: str, s: dict) -> str:
    """How one copy of a line is said in its shot: only a mouth seen talking is "on" (code, not a second guess)."""
    if kind in ("inner", "narration"):
        return kind
    a = s.get("analysis") or {}
    vis = {c.get("id"): c.get("visibility") for c in a.get("characters") or []}.get(sp)
    if vis is None or vis in ("back", "tiny"):
        return "off"
    if a.get("speaker") == sp:
        return "on" if a.get("lip_visible", True) else "off"
    return "on" if vis in _MOUTH else "off"


def _line_groups(story: dict) -> list[list[tuple[dict, dict]]]:
    """Every spoken line once, with each (shot, copy) it sits in: a line over a cut is in both shots."""
    groups: dict[tuple, list] = {}
    for s in story["shots"]:
        for x in s.get("subtitles") or []:
            if not x.get("card"):
                groups.setdefault((x["start"], x["text"]), []).append((s, x))
    return [groups[k] for k in sorted(groups, key=lambda k: k[0])]


def _set_speaker(group: list[tuple[dict, dict]], sp: str, kind: str) -> None:
    for s, x in group:
        mode = "narration" if sp == subs_mod.NARRATOR else "off" if sp == "title" else _mode(sp, kind, s)
        # on_screen stays the switch every later stage reads: only a mouth that really talks on camera moves
        x.update(speaker=sp, mode=mode, on_screen=mode == "on")


def attribute_lines(story: dict, vlm: VLM, work: Path | None = None) -> int:
    """Who says each subtitle line. One pass reads the whole script (each line once, with what the shot pass saw in
    its shots); the lines where that disagrees with the shot pass are then settled looking at the frames (needs
    `work`). Writes speaker / mode / on_screen into every shot's copy; returns how many lines got a speaker."""
    mark_cards(story)
    groups = _line_groups(story)
    if not groups:
        return 0
    ids = {c["id"] for c in story["characters"]}
    # people the shot pass saw and named itself (not in the cast table): they may be the ones talking
    extra: dict[str, list[int]] = {}
    for s in story["shots"]:
        for ch in (s.get("analysis") or {}).get("characters") or []:
            cid = ch.get("id")
            if isinstance(cid, str) and cid and cid not in ids and s["index"] not in extra.setdefault(cid, []):
                extra[cid].append(s["index"])
    lip = lambda s: "" if (s.get("analysis") or {}).get("lip_visible", True) else "(看不到嘴)"
    where = lambda s: (f"镜{s['index']}[画面里:{_visible(s)}；看图像是在说话:"
                       + (f"{_shot_speaker(s, ids)}{lip(s)}" if _shot_speaker(s, ids) else "无") + "]")
    rows = [f"{i}|{g[0][1]['start']:.1f}–{g[0][1]['end']:.1f}s|{' '.join(where(s) for s, _ in g)}|{g[0][1]['text']}"
            for i, g in enumerate(groups)]
    cast = "\n".join(f"- {c['id']}：{c.get('role')}，{str(c.get('appearance', ''))[:80]}" for c in story["characters"])
    cast += "".join(f"\n- {e}：不在角色表里、逐镜看图时画面里出现的人（镜{'、'.join(map(str, at))}）" for e, at in extra.items())
    res = vlm.ask_json(LINE_SPEAKER_PROMPT.format(summary=story.get("summary", ""), cast=cast, lines="\n".join(rows)),
                       [], max_tokens=16000, timeout=300)
    known = ids | set(extra) | {"title", subs_mod.NARRATOR}
    kinds: dict[int, str] = {}
    for r in res.get("lines") or []:
        i, sp = r.get("i"), r.get("speaker")
        if isinstance(i, str) and i.isdigit():
            i = int(i)
        if not isinstance(i, int) or not 0 <= i < len(groups):
            continue
        kind = r.get("kind") or r.get("mode")
        kind = kind if kind in ("inner", "narration") else "say"
        if sp not in known and kind == "narration":
            sp = subs_mod.NARRATOR  # a narrator the model named its own way ("旁白", "解说")
        if sp in known:
            _set_speaker(groups[i], sp, kind)
            kinds[i] = kind
            for _, x in groups[i]:
                x.pop("unsure", None)
                if r.get("sure") is False and sp in known - {"title", subs_mod.NARRATOR}:
                    x["unsure"] = True  # stays on the line unless the look at the frames settles it
    for e, at in extra.items():  # someone who talks has a part: into the cast table, as a bit part
        if any(x.get("speaker") == e for g in groups for _, x in g):
            story["characters"].append({"id": e, "role": "路人", "importance": "extra", "appearance": "", "shots": at})
            LOG.info("角色表补上 %s（逐镜里出现、有台词）：镜 %s", e, at)
    if work is not None:
        r = story.setdefault("refined", {})
        r["speaker_checks"] = settle_speakers(story, work, vlm, groups, kinds)
    return len(kinds)


def _run(groups: list, kinds: dict[int, str], i: int, gap: float = 0.4) -> list[int]:
    """The lines one person says in one breath around line i (a sentence the subtitles cut in pieces): decided together."""
    same = lambda a, b: (kinds.get(a) == kinds.get(b) == "say" and groups[a][0][1].get("speaker") == groups[b][0][1].get("speaker")
                         and groups[b][0][1]["start"] - groups[a][0][1]["end"] <= gap)
    lo = hi = i
    while lo > 0 and same(lo - 1, lo):
        lo -= 1
    while hi + 1 < len(groups) and same(hi, hi + 1):
        hi += 1
    return list(range(lo, hi + 1))


def speaker_disputes(story: dict, groups: list, kinds: dict[int, str]) -> list[tuple[int, str, str]]:
    """Lines to look at again with the frames: (line, speaker by context, speaker by picture or "").
    - a shot where the picture shows X talking but none of its lines went to X: the line that sits most in it;
    - a line the first pass itself was not sure of."""
    ids = {c["id"] for c in story["characters"]}
    at = {id(x): i for i, g in enumerate(groups) for _, x in g}
    found: dict[int, tuple[str, str]] = {}
    for s in story["shots"]:
        seen = _shot_speaker(s, ids)
        lines = [x for x in s.get("subtitles") or [] if id(x) in at and kinds.get(at[id(x)]) == "say" and x.get("speaker") in ids]
        if not seen or not lines or any(x["speaker"] == seen for x in lines):
            continue
        x = max(lines, key=lambda x: min(x["end"], s["end"]) - max(x["start"], s["start"]))
        found.setdefault(at[id(x)], (x["speaker"], seen))
    for i, g in enumerate(groups):
        if g[0][1].get("unsure") and kinds.get(i) == "say":
            found.setdefault(i, (g[0][1]["speaker"], next((v for s, _ in g if (v := _shot_speaker(s, ids))), "")))
    return [(i, *found[i]) for i in sorted(found)]


def settle_speakers(story: dict, work: Path, vlm: VLM, groups: list, kinds: dict[int, str], batch: int = 6,
                    workers: int = 6) -> list[dict]:
    """Second look at the lines in doubt, with the frames of every shot they run over; the other pieces of a sentence
    the subtitles cut up are looked at with them. What the look cannot settle stays `unsure` for a person (lint lists it).
    Returns what was asked and decided."""
    from .media import line_strip
    ids = {c["id"] for c in story["characters"]}
    cast = "\n".join(f"- {c['id']}：{c.get('role')}，{str(c.get('appearance', ''))[:80]}" for c in story["characters"])
    said = lambda j: f"{groups[j][0][1].get('speaker', '?')}：{groups[j][0][1]['text']}" if 0 <= j < len(groups) else "（无）"
    todo, taken = [], set()
    for i, _, _ in speaker_disputes(story, groups, kinds):
        for j in _run(groups, kinds, i):  # the lines said in the same breath are looked at with it, each on its own row
            if j not in taken:
                taken.add(j)
                shots = [s for s, _ in groups[j]][:2]
                todo.append((j, shots, groups[j][0][1]["speaker"], next((v for s in shots if (v := _shot_speaker(s, ids))), "")))
    todo.sort(key=lambda d: d[0])
    parts = [todo[n:n + batch] for n in range(0, len(todo), batch)]

    def ask(part: list) -> dict | None:
        rows = [f"{k}|{'、'.join('镜' + str(s['index']) for s in shots)}|{groups[j][0][1]['text']}|{said(j - 1)}|{said(j + 1)}|{sp}|{seen or '无'}"
                for k, (j, shots, sp, seen) in enumerate(part)]
        try:
            return vlm.ask_json(SPEAKER_CHECK_PROMPT.format(summary=story.get("summary", ""), cast=cast, lines="\n".join(rows)),
                                [line_strip(work, s, groups[j][0][1], f"{k} 镜{s['index']}")
                                 for k, (j, shots, _, _) in enumerate(part) for s in shots], max_tokens=4096)
        except Exception as exc:  # the first pass stands
            LOG.warning("说话人复核失败：%s", exc)
            return None

    with Pool(workers) as ex:  # every batch in one round (agent backend), all asked before any answer is applied
        answers = ex.map(ask, parts)
    out = []
    for part, res in zip(parts, answers):
        if res is None:
            continue
        for r in res.get("lines") or []:
            k, sp = r.get("k"), r.get("speaker")
            if isinstance(k, str) and k.isdigit():
                k = int(k)
            if not isinstance(k, int) or not 0 <= k < len(part) or sp not in ids:
                continue
            j, shots, was, seen = part[k]
            text = groups[j][0][1]["text"]
            _set_speaker(groups[j], sp, "say")
            for _, x in groups[j]:
                x.pop("unsure", None)
                if r.get("sure") is False:
                    x["unsure"] = True
            if sp != was:
                LOG.info("说话人复核 镜%d「%s」：%s → %s（%s）", shots[0]["index"], text, was, sp, r.get("why", ""))
            out.append({"shot": shots[0]["index"], "text": text, "context": was, "picture": seen, "speaker": sp,
                        "sure": r.get("sure") is not False, "why": str(r.get("why", ""))})
    return out


# ---- one id per person ----

def identity_pairs(story: dict, chars: list[dict]) -> dict[tuple[str, str], list[str]]:
    """Suspect pairs with the hard facts behind them: a name shown while its owner is 'absent' (the person on screen
    may be the owner in disguise), and pairs that (almost) never share a shot."""
    shots_of = {c["id"]: set(c.get("shots", [])) for c in chars}
    pairs: dict[tuple[str, str], list[str]] = {}
    for s in story["shots"]:
        here = {x.get("id") for x in (s.get("analysis") or {}).get("characters") or []} & set(shots_of)
        for x in s.get("subtitles") or []:
            for c in chars:
                if x["text"].strip() in names_of(c) and c["id"] not in here:
                    kind = ("大字人名卡（字号是台词的好几倍，是给刚出场的角色打的名字，不是谁喊出来的台词）"
                            if x.get("card") else "字幕")
                    for o in here:
                        if len(shots_of[o] & shots_of[c["id"]]) <= 1:
                            pairs.setdefault((c["id"], o), []).append(
                                f"镜{s['index']} 画面上出现{kind}「{x['text'].strip()}」，这是 {c['id']} 的名字，"
                                f"但这一镜里没有标出 {c['id']}，画面里的是 {'、'.join(sorted(here))}")
    ids = sorted(shots_of)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            both = shots_of[a] & shots_of[b]
            # a shared transition shot is allowed; tiny parts (1-2 shots) are not suspects on this ground alone
            if len(shots_of[a]) >= 3 and len(shots_of[b]) >= 3 and len(both) <= 1:
                key = (a, b) if (a, b) in pairs or (b, a) not in pairs else (b, a)
                pairs.setdefault(key, []).append(f"{a} 和 {b} 几乎没有同时出现过（同框镜头：{sorted(both) or '无'}）")
    return pairs


def _rename(story: dict, old: str, new: str) -> None:
    for s in story["shots"]:
        a = s.get("analysis") or {}
        for c in a.get("characters") or []:
            if c.get("id") == old:
                c["id"] = new
        for k in ("in_first", "in_last"):
            if isinstance(a.get(k), list):
                a[k] = list(dict.fromkeys(new if x == old else x for x in a[k]))
        if a.get("speaker") == old:
            a["speaker"] = new
        for b in (s.get("performance") or {}).get("beats") or []:
            if b.get("who") == old:
                b["who"] = new
        for x in s.get("subtitles") or []:
            if x.get("speaker") == old:
                x["speaker"] = new


def merge_identities(story: dict, work: Path, vlm: VLM, workers: int = 6) -> list[dict]:
    """One person split into two ids (disguise, costume change): merge them, keeping the other look as an outfit
    of the same character for its shots. Otherwise one person is counted as two.
    Every suspect pair is asked at once (one round for the agent backend, not one per pair), then merged in order."""
    chars = [c for c in story["characters"] if c.get("importance") != "extra" and "crowd" not in c["id"]]
    by_id = {c["id"]: c for c in story["characters"]}
    info = lambda c: f"{c.get('role')}；{c.get('appearance')}；出现在镜头 {c.get('shots')}"
    nums = lambda lo, n: "、".join(str(lo + i) for i in range(n))
    todo = []
    for (a, b), clues in identity_pairs(story, chars).items():
        if a not in by_id or b not in by_id:
            continue
        pa, pb = portraits(story, work, a), portraits(story, work, b)
        if pa and pb:
            todo.append((a, b, clues, pa, pb))

    def ask(item) -> dict | None:
        a, b, clues, pa, pb = item
        near = set(by_id[a].get("shots", [])) | set(by_id[b].get("shots", []))
        lines = "\n".join(f"镜{s['index']} {'【人名字卡】' if x.get('card') else x.get('speaker', '?') + '：'}{x['text']}"
                          for s in story["shots"] if s["index"] in near for x in s.get("subtitles") or [])
        try:
            return vlm.ask_json(IDENTITY_PROMPT.format(a=a, b=b, a_info=info(by_id[a]), b_info=info(by_id[b]),
                                                       a_pics=nums(1, len(pa)), b_pics=nums(1 + len(pa), len(pb)),
                                                       summary=story.get("summary", ""), clues="\n".join(f"- {c}" for c in clues),
                                                       lines=lines), pa + pb, max_tokens=4096, timeout=300)
        except Exception as exc:  # a safety net, never a blocker
            LOG.warning("角色合并检查 %s/%s 失败：%s", a, b, exc)
            return None

    with Pool(workers) as ex:
        answers = ex.map(ask, todo)
    done = []
    for (a, b, *_), r in zip(todo, answers):
        if r is None or a not in by_id or b not in by_id:  # failed, or one of them was merged into a third already
            continue
        if not r.get("same"):  # remembered so the breakdown check does not raise the pair again
            pair = sorted((a, b))
            seen = story.setdefault("refined", {}).setdefault("distinct", [])
            if pair not in seen:
                seen.append(pair)
            continue
        # keep the one that is introduced first / has more shots; the other becomes one of its outfits
        keep, drop = sorted((by_id[a], by_id[b]), key=lambda c: (min(c.get("shots") or [999]), -len(c.get("shots", []))))
        LOG.info("角色合并：%s 就是 %s（%s）", drop["id"], keep["id"], r.get("reason", ""))
        keep.setdefault("outfits", []).append({"shots": drop.get("shots", []), "appearance": drop.get("appearance", ""),
                                               "role": drop.get("role", ""), "was": drop["id"]})
        keep["shots"] = sorted(set(keep.get("shots", [])) | set(drop.get("shots", [])))
        _rename(story, drop["id"], keep["id"])
        story["characters"].remove(drop)
        by_id.pop(drop["id"])
        done.append({"keep": keep["id"], "drop": drop["id"], "reason": r.get("reason", "")})
    return done
