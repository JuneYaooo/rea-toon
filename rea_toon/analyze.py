"""Reference video -> story.json (the editable "source" of a short drama).

1. rea-video decompose: shot cuts, stems (vocals / music), production forensics
2. timed subtitle OCR
3. full-res first / mid / last frame per shot
4. VLM cast pass over contact sheets: who is who across shots
5. VLM shot pass: per shot characters, emotion, camera, motion prompt
6. VLM performance pass: time-coded face / mouth / body beats for shots with main characters
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
from .engines.vlm import VLM
from .media import aspect_of, font_path, frame_at, probe
from .prompts import CAST_PROMPT, PERFORMANCE_PROMPT, SHOT_PROMPT, SUBS_PROOFREAD_PROMPT

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

    shots = []
    for s in bd["shots"]:
        sid = f"shot_{s['index']:03d}"
        start, end = float(s["start"]), float(s["end"])
        frames = {}
        for name, t in (("first", start + 0.04), ("mid", (start + end) / 2), ("last", max(start, end - 0.07))):
            p = work / "frames" / f"{sid}_{name}.jpg"
            if cuts or not p.is_file():  # forced cuts renumber the shots: old frames belong to other shots
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
