"""Reference video -> story.json (the editable "source" of a short drama).

1. rea-video decompose: shot cuts, stems (vocals / music), production forensics
2. timed subtitle OCR
3. full-res first / mid / last frame per shot
4. VLM cast pass over contact sheets: who is who across shots
5. VLM shot pass: per shot characters, emotion, camera, motion prompt
6. VLM performance pass: time-coded face / mouth / body beats for shots with main characters
7. story-level passes: who says each line, one id per person across disguises

The deep breakdown (scenes and looks, beats, risks, lint) is breakdown.py; the remake only reads the locked result.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import subtitles as subs_mod
from .vlm import VLM
from .media import aspect_of, font_path, frame_at, probe
from .prompts import CAST_PROMPT, IDENTITY_PROMPT, LINE_SPEAKER_PROMPT, PERFORMANCE_PROMPT, SHOT_PROMPT, SUBS_PROOFREAD_PROMPT

LOG = logging.getLogger(__name__)


def _rea_video_bin() -> str:
    for cand in (os.getenv("REA_VIDEO_BIN"), shutil.which("rea-video"),
                 str(Path.home() / "code/github/capsule-cinema/.venv/bin/rea-video")):
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError("找不到 rea-video：pip install -e ~/code/github/rea-video 或设置 REA_VIDEO_BIN")


def decompose(work: Path) -> dict:
    out = work / "decomp"
    bd = out / "breakdown.json"
    if not bd.is_file():
        LOG.info("rea-video decompose …")
        subprocess.run([_rea_video_bin(), "decompose", str(work / "source.mp4"), "-o", str(out)], check=True)
    return json.loads(bd.read_text(encoding="utf-8"))


def _subs_in(subs: list[dict], start: float, end: float) -> list[dict]:
    return [s for s in subs if s["end"] > start + 0.05 and s["start"] < end - 0.05]


def contact_sheets(shots: list[dict], work: Path, per_sheet: int = 12, cols: int = 4) -> list[Path]:
    font = ImageFont.truetype(font_path(), 34) if font_path() else ImageFont.load_default()
    paths = []
    for k in range(0, len(shots), per_sheet):
        group = shots[k:k + per_sheet]
        tw, th = 480, 270
        rows = (len(group) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw, rows * th), "black")
        for i, s in enumerate(group):
            im = Image.open(work / s["frames"]["mid"]).convert("RGB").resize((tw, th))
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, 64, 42], fill="white")
            d.text((6, 2), str(s["index"]), fill="black", font=font)
            sheet.paste(im, ((i % cols) * tw, (i // cols) * th))
        p = work / "analysis" / f"contact_{k // per_sheet + 1}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(p, quality=88)
        paths.append(p)
    return paths


def split_shots(shots: list[dict], cuts: list[float]) -> list[dict]:
    """Force extra cuts the detector missed (dissolves, walk-through-door transitions, occluding wipes).
    Long takes have to be cut to <=15s for Ref2VA; cut where the frame is fully covered or most chaotic."""
    out = []
    for s in shots:
        start, end = float(s["start"]), float(s["end"])
        inner = sorted(c for c in cuts if start + 0.3 < c < end - 0.3)
        for a, b in zip([start, *inner], [*inner, end]):
            out.append({**s, "start": a, "end": b})
    for i, s in enumerate(out, 1):
        s["index"] = i
    return out


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


def proofread_story(work: Path, vlm: VLM | None = None) -> int:
    """Proofread subtitles.json of an analysed work dir and carry the fixes into story.json (no re-analysis)."""
    vlm = vlm or VLM(cache_dir=work / "analysis" / "vlm_cache")
    subs_path, story_path = work / "subtitles.json", work / "story.json"
    story = json.loads(story_path.read_text(encoding="utf-8"))
    subs = proofread(json.loads(subs_path.read_text(encoding="utf-8")), vlm, story.get("summary", ""))
    subs_path.write_text(json.dumps(subs, ensure_ascii=False, indent=1), encoding="utf-8")
    subs_mod.to_srt(subs, work / "subtitles.srt")
    fixed = {(x["start"], x["ocr"]): x["text"] for x in subs if "ocr" in x}
    n = 0
    for s in story["shots"]:
        for x in s.get("subtitles") or []:
            new = fixed.get((x["start"], x.get("ocr", x["text"])))
            if new and new != x["text"]:
                x.setdefault("ocr", x["text"])
                x["text"] = new
                n += 1
    story_path.write_text(json.dumps(story, ensure_ascii=False, indent=1), encoding="utf-8")
    write_cast_sheet(story, work)
    return n


def _visible(s: dict) -> str:
    return ";".join(f"{c.get('id')}:{c.get('visibility', '')}" for c in (s.get("analysis") or {}).get("characters") or []) or "无人"


def attribute_lines(story: dict, vlm: VLM) -> int:
    """Who says each subtitle line, from the whole script (shot analysis only sees stills and allows one speaker
    per shot). Writes speaker / on_screen into every shot's subtitles; returns how many lines got a speaker."""
    mark_cards(story)
    rows, refs = [], []
    for s in story["shots"]:
        for x in s.get("subtitles") or []:
            if x.get("card"):
                continue
            rows.append(f"{len(rows)}|镜{s['index']}|{x['start']:.1f}s|{_visible(s)}|{x['text']}")
            refs.append(x)
    if not rows:
        return 0
    cast = "\n".join(f"- {c['id']}：{c.get('role')}，{str(c.get('appearance', ''))[:80]}" for c in story["characters"])
    res = vlm.ask_json(LINE_SPEAKER_PROMPT.format(summary=story.get("summary", ""), cast=cast, lines="\n".join(rows)),
                       [], max_tokens=16000, timeout=300)
    ids = {c["id"] for c in story["characters"]} | {"title", subs_mod.NARRATOR}
    n = 0
    for r in res.get("lines") or []:
        i, sp = r.get("i"), r.get("speaker")
        if isinstance(i, str) and i.isdigit():
            i = int(i)
        if not isinstance(i, int) or not 0 <= i < len(refs):
            continue
        mode = r.get("mode") if r.get("mode") in subs_mod.MODES else ("on" if r.get("on_screen") else "off")
        if sp not in ids and mode == "narration":
            sp = subs_mod.NARRATOR  # a narrator the model named its own way ("旁白", "解说")
        if sp == subs_mod.NARRATOR:
            mode = "narration"
        if sp in ids:
            # on_screen stays the switch every later stage reads: only a mouth that really talks on camera moves
            refs[i].update(speaker=sp, mode=mode, on_screen=mode == "on")
            n += 1
    return n


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


def mark_cards(story: dict) -> int:
    """Name cards / titles drawn far bigger than the dialogue (e.g. a character's name as she walks in) are not
    lines: nobody says them, so no mouth may move on them, and they say who that person is."""
    import statistics
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


def _names(c: dict) -> set[str]:
    """Names a character goes by: the id and anything in brackets of the role, e.g. 姐姐（温月） -> 温月."""
    import re
    return {c["id"], *re.findall(r"[（(]([^）)]+)[）)]", str(c.get("role", "")))} - {""}


def identity_pairs(story: dict, chars: list[dict]) -> dict[tuple[str, str], list[str]]:
    """Suspect pairs with the hard facts behind them: a name shown while its owner is 'absent' (the person on screen
    may be the owner in disguise), and pairs that (almost) never share a shot."""
    shots_of = {c["id"]: set(c.get("shots", [])) for c in chars}
    pairs: dict[tuple[str, str], list[str]] = {}
    for s in story["shots"]:
        here = {x.get("id") for x in (s.get("analysis") or {}).get("characters") or []} & set(shots_of)
        for x in s.get("subtitles") or []:
            for c in chars:
                if x["text"].strip() in _names(c) and c["id"] not in here:
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


def _portraits(story: dict, work: Path, cid: str, k: int = 2) -> list[Path]:
    """The clearest shots of one character: face visible, closest framing first."""
    order = ("特写", "近景", "中景")
    best = [s for s in story["shots"] if any(x.get("id") == cid and x.get("visibility") in ("face", "profile")
                                             for x in (s.get("analysis") or {}).get("characters") or [])]
    best.sort(key=lambda s: next((i for i, w in enumerate(order) if w in str((s.get("analysis") or {}).get("shot_size", ""))), 9))
    return [work / s["frames"]["mid"] for s in best[:k]]


def merge_identities(story: dict, work: Path, vlm: VLM) -> list[dict]:
    """One person split into two ids (disguise, costume change): merge them, keeping the other look as an outfit
    of the same character for its shots. Otherwise the remake gives one person two different looks."""
    chars = [c for c in story["characters"] if c.get("importance") != "extra" and "crowd" not in c["id"]]
    by_id = {c["id"]: c for c in story["characters"]}
    done = []
    for (a, b), clues in identity_pairs(story, chars).items():
        if a not in by_id or b not in by_id:
            continue
        pa, pb = _portraits(story, work, a), _portraits(story, work, b)
        if not pa or not pb:
            continue
        near = set(by_id[a].get("shots", [])) | set(by_id[b].get("shots", []))
        lines = "\n".join(f"镜{s['index']} {'【人名字卡】' if x.get('card') else x.get('speaker', '?') + '：'}{x['text']}"
                          for s in story["shots"] if s["index"] in near for x in s.get("subtitles") or [])
        info = lambda c: f"{c.get('role')}；{c.get('appearance')}；出现在镜头 {c.get('shots')}"
        nums = lambda lo, n: "、".join(str(lo + i) for i in range(n))
        try:
            r = vlm.ask_json(IDENTITY_PROMPT.format(a=a, b=b, a_info=info(by_id[a]), b_info=info(by_id[b]),
                                                    a_pics=nums(1, len(pa)), b_pics=nums(1 + len(pa), len(pb)),
                                                    summary=story.get("summary", ""), clues="\n".join(f"- {c}" for c in clues),
                                                    lines=lines), pa + pb, max_tokens=4096, timeout=300)
        except Exception as exc:  # a safety net, never a blocker
            LOG.warning("角色合并检查 %s/%s 失败：%s", a, b, exc)
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


def refine(work: Path, vlm: VLM | None = None) -> dict:
    """Story-level passes over an analysed work dir: who says each line, and one person split into two ids.
    analyze runs them; this re-runs them on an older story.json without re-analysing the shots."""
    vlm = vlm or VLM(cache_dir=work / "analysis" / "vlm_cache")
    story_path = work / "story.json"
    story = json.loads(story_path.read_text(encoding="utf-8"))
    n = attribute_lines(story, vlm)
    merges = merge_identities(story, work, vlm)
    story.setdefault("refined", {}).update(merges=story.get("refined", {}).get("merges", []) + merges, lines=n)
    story_path.write_text(json.dumps(story, ensure_ascii=False, indent=1), encoding="utf-8")
    write_cast_sheet(story, work)
    return {"merges": merges, "lines": n}


def analyze(work: Path, use_vlm: bool = True, workers: int = 6, cuts: list[float] | None = None) -> dict:
    src = work / "source.mp4"
    info = probe(src)
    bd = decompose(work)
    if cuts:
        bd = {**bd, "shots": split_shots(bd["shots"], cuts)}

    subs_path = work / "subtitles.json"
    if subs_path.is_file():
        subs = json.loads(subs_path.read_text(encoding="utf-8"))
    else:
        LOG.info("OCR 字幕轨 …")
        subs = subs_mod.extract(src)
        subs_path.write_text(json.dumps(subs, ensure_ascii=False, indent=1), encoding="utf-8")
        subs_mod.to_srt(subs, work / "subtitles.srt")
    vlm = VLM(cache_dir=work / "analysis" / "vlm_cache") if use_vlm else None
    if vlm and not all(x.get("checked") for x in subs):
        subs = proofread(subs, vlm)
        subs_path.write_text(json.dumps(subs, ensure_ascii=False, indent=1), encoding="utf-8")
        subs_mod.to_srt(subs, work / "subtitles.srt")

    # frames are named by shot number: once the cuts change, every frame (and the acting / contact frames) is another shot's
    marker = work / "frames" / "cuts.json"
    want = sorted(cuts or [])
    refresh = marker.is_file() and json.loads(marker.read_text(encoding="utf-8")) != want or (not marker.is_file() and bool(want))
    if refresh and (work / "frames").is_dir():
        LOG.info("切点变了：重抽所有镜头帧")
        shutil.rmtree(work / "frames")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(want), encoding="utf-8")

    shots = []
    for s in bd["shots"]:
        sid = f"shot_{s['index']:03d}"
        start, end = float(s["start"]), float(s["end"])
        frames = {}
        for name, t in (("first", start + 0.04), ("mid", (start + end) / 2), ("last", max(start, end - 0.07))):
            p = work / "frames" / f"{sid}_{name}.jpg"
            if not p.is_file():
                frame_at(src, t, p)
            frames[name] = str(p.relative_to(work))
        shots.append({"id": sid, "index": s["index"], "start": round(start, 3), "end": round(end, 3),
                      "duration": round(end - start, 3), "frames": frames,
                      "production": (s.get("base") or {}).get("label"),
                      "subtitles": _subs_in(subs, start, end)})

    story = {"schema": "rea_toon.story.v1", "source": json.loads((work / "source.json").read_text(encoding="utf-8"))
             if (work / "source.json").is_file() else {}, "aspect": aspect_of(info["width"], info["height"]),
             "width": info["width"], "height": info["height"], "fps": info["fps"], "duration": info["duration"],
             "audio": {k: str(Path("decomp/stems") / f"{k}.wav") for k in ("original", "vocals", "music")},
             "summary": "", "style": "", "characters": [], "shots": shots, "cuts": sorted(cuts or [])}

    if use_vlm:
        LOG.info("VLM: %s", vlm.describe())
        sheets = contact_sheets(shots, work)
        sub_lines = "\n".join(f"{s['index']}: " + (" / ".join(x["text"] for x in s["subtitles"]) or "（无）") for s in shots)
        cast = vlm.ask_json(CAST_PROMPT.format(subtitles=sub_lines), sheets)
        story.update(summary=cast.get("summary", ""), style=cast.get("style", ""), palette=cast.get("palette", ""),
                     characters=cast.get("characters", []))
        cast_text = "\n".join(f"- {c['id']}（{c.get('role')}）：{c.get('appearance')}" for c in story["characters"])

        def one(s: dict) -> dict:
            prompt = SHOT_PROMPT.format(summary=story["summary"], cast=cast_text, index=s["index"], start=s["start"],
                                        end=s["end"], duration=s["duration"],
                                        subtitle=" / ".join(x["text"] for x in s["subtitles"]) or "（无）")
            try:
                return vlm.ask_json(prompt, [work / s["frames"][k] for k in ("first", "mid", "last")], max_tokens=4096)
            except Exception as exc:
                LOG.warning("shot %s VLM 失败：%s", s["index"], exc)
                return {"error": str(exc)}

        with ThreadPoolExecutor(workers) as ex:
            for s, res in zip(shots, ex.map(one, shots)):
                s["analysis"] = res
        # back-fill appearances from the shot pass (the cast pass sometimes misses back views)
        by_id = {c["id"]: c for c in story["characters"]}
        for s in shots:
            for ch in (s.get("analysis") or {}).get("characters") or []:
                c = by_id.get(ch.get("id"))
                if c is not None and s["index"] not in c.setdefault("shots", []):
                    c["shots"].append(s["index"])
        for c in story["characters"]:
            c["shots"] = sorted(set(int(x) for x in c.get("shots", [])))
        main = {c["id"] for c in story["characters"] if c.get("importance") != "extra"}
        acting = [s for s in shots if main & {c.get("id") for c in (s.get("analysis") or {}).get("characters") or []}]
        with ThreadPoolExecutor(workers) as ex:
            for s, res in zip(acting, ex.map(lambda s: performance(work, story, s, vlm), acting)):
                s["performance"] = res
        # story-level passes: one speaker per line (not per shot), one id per person across costume changes
        # (speakers first: the merge pass reads them; it renames the merged id inside them too)
        n = attribute_lines(story, vlm)
        merges = merge_identities(story, work, vlm)
        story.setdefault("refined", {}).update(lines=n, merges=merges)
    (work / "story.json").write_text(json.dumps(story, ensure_ascii=False, indent=1), encoding="utf-8")
    write_cast_sheet(story, work)
    return story


def performance(work: Path, story: dict, shot: dict, vlm: VLM | None = None, n: int = 6) -> dict:
    """Time-coded acting beats of one shot (what the face, mouth and body do, second by second)."""
    vlm = vlm or VLM(cache_dir=work / "analysis" / "vlm_cache")
    dur = shot["duration"]
    n = max(3, min(n, int(dur / 0.4) + 1))
    times = [round(dur * (k + 0.5) / n, 2) for k in range(n)]
    frames = []
    for k, t in enumerate(times):
        p = work / "frames" / "perf" / f"{shot['id']}_{k}.jpg"
        if not p.is_file():
            frame_at(work / "source.mp4", shot["start"] + t, p)
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


def write_cast_sheet(story: dict, work: Path) -> None:
    """Human-readable cast + shot table, for picking whom to replace."""
    lines = [f"# 剧本拆解：{story['source'].get('title') or 'source'}", "", f"**剧情**：{story['summary']}", "",
             f"**画风**：{story['style']}", "", "## 角色", "", "| id | 身份 | 重要度 | 出现镜头 | 外貌 |", "|---|---|---|---|---|"]
    for c in story["characters"]:
        lines.append(f"| {c['id']} | {c.get('role','')} | {c.get('importance','')} | {','.join(map(str, c.get('shots', [])))} | {c.get('appearance','')} |")
    lines += ["", "## 镜头", "", "| # | 时间 | 角色 | 情绪 | 运镜 | 字幕 | 动作 |", "|---|---|---|---|---|---|---|"]
    for s in story["shots"]:
        a = s.get("analysis") or {}
        chars = ",".join(c.get("id", "?") for c in a.get("characters") or [])
        lines.append(f"| {s['index']} | {s['start']:.1f}-{s['end']:.1f} | {chars} | {a.get('emotion','')} | {a.get('camera_move','')} | "
                     f"{' / '.join(x['text'] for x in s['subtitles'])} | {a.get('action_summary','')} |")
    (work / "story.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
