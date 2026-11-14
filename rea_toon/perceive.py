"""Perception of one video -> story.json (stage 1 of the pipeline; pipeline.perceive runs it).

1. rea-video decompose: shot cuts, stems (vocals / music), production forensics
2. timed subtitle OCR, proofread once by the model
3. full-res first / mid / last frame per shot
4. VLM cast pass over contact sheets: who is who across shots
5. VLM shot pass: per shot characters, emotion, camera, motion prompt
   (fast: 5 shots per call with staging included; full: one call per shot plus the per-second acting pass)

The story-level checks (who says each line, one id per person) are refine.py; the deep breakdown is breakdown/.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from . import layout
from . import subtitles as subs_mod
from .breakdown.acting import add_performance
from .breakdown.staging import staging_from
from .media import aspect_of, contact_sheets, face_sheet, frame_at, probe, shot_strip
from .prompts import CAST_PROMPT, SERIES_CONTEXT, SERIES_FACES, SHOT_BATCH_PROMPT, SHOT_PROMPT
from .refine import proofread
from .vlm import for_work, Pool, VLM

LOG = logging.getLogger(__name__)


def _rea_video_bin() -> str:
    for cand in (os.getenv("REA_VIDEO_BIN"), shutil.which("rea-video")):
        if cand and Path(cand).expanduser().exists():
            return str(Path(cand).expanduser())
    raise RuntimeError("找不到 rea-video：pip install git+https://github.com/JuneYaooo/rea-video ，或在 .env 里设置 REA_VIDEO_BIN")


def decompose(work: Path) -> dict:
    out = layout.decomp(work)
    bd = out / "breakdown.json"
    if not bd.is_file():
        LOG.info("rea-video decompose …")
        subprocess.run([_rea_video_bin(), "decompose", str(layout.source(work)), "-o", str(out)], check=True)
    return json.loads(bd.read_text(encoding="utf-8"))


_subs_in = subs_mod.in_span


def shot_batches(story: dict, work: Path, vlm: VLM, cast_text: str, one, batch: int = 5, workers: int = 6,
                 ref: Path | None = None) -> None:
    """Fast mode: `batch` shots per call, staging included. A shot the model skips gets its own call (`one`).
    ref: the faces of the cast known from earlier episodes (media.face_sheet), shown with every batch."""
    shots = story["shots"]
    groups = [shots[k:k + batch] for k in range(0, len(shots), batch)]

    def ask(group: list[dict]) -> dict:
        rows = "\n".join(f"镜{s['index']}（{s['start']:.1f}–{s['end']:.1f}s，{s['duration']:.1f}s）字幕："
                         + (" / ".join(x["text"] for x in s["subtitles"]) or "（无）") for s in group)
        prompt = SHOT_BATCH_PROMPT.format(summary=story["summary"], cast=cast_text, n=len(group), shots=rows)
        if ref:
            prompt += SERIES_FACES
        try:
            r = vlm.ask_json(prompt, [shot_strip(work, s) for s in group] + ([ref] if ref else []),
                             max_tokens=2500 * len(group), timeout=400)
        except Exception as exc:
            LOG.warning("镜 %s–%s 批量拆解失败，逐镜重试：%s", group[0]["index"], group[-1]["index"], exc)
            return {}
        out = {}
        for x in r.get("shots") or []:
            try:
                out[int(x.get("index"))] = x
            except (TypeError, ValueError):
                continue
        return out

    with Pool(workers) as ex:
        answers = {k: v for got in ex.map(ask, groups) for k, v in got.items()}
    missing = [s for s in shots if s["index"] not in answers]
    for s in shots:
        if s["index"] in answers:
            res = dict(answers[s["index"]])
            st = res.pop("staging", None)
            res.pop("index", None)
            s["analysis"] = {**res, "batched": True}
            s.pop("staging", None)
            if isinstance(st, dict) and (got := staging_from(st, s, story)):
                s["staging"] = got
    if missing:
        LOG.info("%d 个镜头批量里没答，逐镜补拆", len(missing))
        with Pool(workers) as ex:
            for s, res in zip(missing, ex.map(one, missing)):
                s["analysis"] = res


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


def perceive(work: Path, use_vlm: bool = True, workers: int = 6, cuts: list[float] | None = None,
             prior: dict | None = None, full: bool = False) -> dict:
    """The story of work/source.mp4, not yet saved. use_vlm=False: shots, frames and subtitles only.
    prior: what earlier episodes of a series settled, {"ep", "recap", "cast"} (series.prior_of); None for one video.
    full=False (fast): several shots per model call, no per-second acting pass (`deepen` adds it);
    full=True: one call per shot plus the acting pass from the start (about five times the cost)."""
    src = layout.source(work)
    info = probe(src)
    bd = decompose(work)
    if cuts:
        bd = {**bd, "shots": split_shots(bd["shots"], cuts)}

    subs_path = layout.data(work, "subtitles.json")
    if subs_path.is_file():
        subs = json.loads(subs_path.read_text(encoding="utf-8"))
    else:
        LOG.info("OCR 字幕轨 …")
        subs = subs_mod.extract(src)
        layout.put(subs_path, json.dumps(subs, ensure_ascii=False, indent=1))
        subs_mod.to_srt(subs, layout.data(work, "subtitles.srt"))
    vlm = for_work(work) if use_vlm else None
    if vlm and not all(x.get("checked") for x in subs):
        subs = proofread(subs, vlm)
        layout.put(subs_path, json.dumps(subs, ensure_ascii=False, indent=1))
        subs_mod.to_srt(subs, layout.data(work, "subtitles.srt"))

    # frames are named by shot number: once the cuts change, every frame (and the acting / contact frames) is another shot's
    marker = layout.frames(work, "cuts.json")
    want = sorted(cuts or [])
    refresh = marker.is_file() and json.loads(marker.read_text(encoding="utf-8")) != want or (not marker.is_file() and bool(want))
    if refresh and layout.frames(work).is_dir():
        LOG.info("切点变了：重抽所有镜头帧")
        shutil.rmtree(layout.frames(work))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(want), encoding="utf-8")

    shots = []
    for s in bd["shots"]:
        sid = f"shot_{s['index']:03d}"
        start, end = float(s["start"]), float(s["end"])
        frames = {}
        for name, t in (("first", start + 0.04), ("mid", (start + end) / 2), ("last", max(start, end - 0.07))):
            p = layout.frames(work, f"{sid}_{name}.jpg")
            if not p.is_file():
                frame_at(src, t, p)
            frames[name] = str(p.relative_to(work))
        shots.append({"id": sid, "index": s["index"], "start": round(start, 3), "end": round(end, 3),
                      "duration": round(end - start, 3), "frames": frames,
                      "production": (s.get("base") or {}).get("label"),
                      "subtitles": _subs_in(subs, start, end)})

    story = {"schema": "rea_toon.story.v1", "source": json.loads(layout.data(work, "source.json").read_text(encoding="utf-8"))
             if layout.data(work, "source.json").is_file() else {}, "aspect": aspect_of(info["width"], info["height"]),
             "width": info["width"], "height": info["height"], "fps": info["fps"], "duration": info["duration"],
             "audio": {k: layout.rel(layout.decomp(work, "stems", f"{k}.wav"), work) for k in ("original", "vocals", "music")},
             "summary": "", "style": "", "characters": [], "shots": shots, "cuts": sorted(cuts or [])}

    if use_vlm:
        LOG.info("VLM: %s", vlm.describe())
        sheets = contact_sheets(shots, work)
        sub_lines = "\n".join(f"{s['index']}: " + (" / ".join(x["text"] for x in s["subtitles"]) or "（无）") for s in shots)
        prompt = CAST_PROMPT.format(subtitles=sub_lines)
        ref = Path(prior["sheet"]) if prior and prior.get("sheet") and Path(prior["sheet"]).is_file() else None
        if prior and prior.get("cast"):
            prompt += SERIES_CONTEXT.format(ep=prior["ep"], recap=prior["recap"], cast=prior["cast"], faces=SERIES_FACES if ref else "")
            story["prior"] = {k: v for k, v in prior.items() if k != "faces"}
        cast = vlm.ask_json(prompt, sheets + ([ref] if ref else []))
        story.update(summary=cast.get("summary", ""), style=cast.get("style", ""), palette=cast.get("palette", ""),
                     characters=cast.get("characters", []))
        for c in story["characters"]:  # a returning character keeps the role (and the spelling of the name) already settled
            if (prior or {}).get("roles", {}).get(c.get("id")):
                c["role"] = prior["roles"][c["id"]]
        cast_text = "\n".join(f"- {c['id']}（{c.get('role')}）：{c.get('appearance')}" for c in story["characters"])
        if ref and (prior or {}).get("faces"):
            # The cast call saw everyone known so far and said who is in this episode. The per-shot calls (a dozen or
            # more an episode) are shown only those faces: in a long series the full sheet is several times the frames.
            here = [(c["id"], prior["faces"][c["id"]]) for c in story["characters"] if c.get("id") in prior["faces"]]
            ref = face_sheet(here, layout.analysis(work, "cast_known.jpg")) if here else None

        def one(s: dict) -> dict:
            prompt = SHOT_PROMPT.format(summary=story["summary"], cast=cast_text, index=s["index"], start=s["start"],
                                        end=s["end"], duration=s["duration"],
                                        subtitle=" / ".join(x["text"] for x in s["subtitles"]) or "（无）")
            try:
                return vlm.ask_json(prompt, [work / s["frames"][k] for k in ("first", "mid", "last")], max_tokens=4096)
            except Exception as exc:
                LOG.warning("shot %s VLM 失败：%s", s["index"], exc)
                return {"error": str(exc)}

        if full:
            with Pool(workers) as ex:
                for s, res in zip(shots, ex.map(one, shots)):
                    s["analysis"] = res
        else:
            shot_batches(story, work, vlm, cast_text, one, workers=workers, ref=ref)
        story["depth"] = "full" if full else "fast"
        # back-fill appearances from the shot pass (the cast pass sometimes misses back views)
        by_id = {c["id"]: c for c in story["characters"]}
        for s in shots:
            for ch in (s.get("analysis") or {}).get("characters") or []:
                c = by_id.get(ch.get("id"))
                if c is not None and s["index"] not in c.setdefault("shots", []):
                    c["shots"].append(s["index"])
        for c in story["characters"]:
            c["shots"] = sorted(set(int(x) for x in c.get("shots", [])))
        if full:
            add_performance(story, work, vlm, workers)
    return story
