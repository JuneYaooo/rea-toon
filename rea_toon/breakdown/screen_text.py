"""Text in the picture besides the subtitles: scene text, speech bubbles, 花字, AIGC labels, watermarks."""

from __future__ import annotations

import logging
from pathlib import Path

from .. import layout, subtitles
from ..memo import Memo, frame_stamp
from ..prompts import SCREEN_TEXT_BATCH_PROMPT, SCREEN_TEXT_PROMPT
from ..vlm import Pool, VLM

LOG = logging.getLogger(__name__)


TEXT_KINDS = ("scene", "bubble", "overlay", "aigc", "watermark")
TEXT_CN = {"scene": "场景里的字", "bubble": "对话气泡", "overlay": "花字", "aigc": "AI 标识", "watermark": "水印"}


def _overlaps(a: list, b: list, pad: float = 0.01) -> bool:
    return not (a[2] < b[0] - pad or b[2] < a[0] - pad or a[3] < b[1] - pad or b[3] < a[1] - pad)


def where(box: list) -> str:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return ("上" if cy < 0.33 else "下" if cy > 0.67 else "中") + ("左" if cx < 0.33 else "右" if cx > 0.67 else "")


def _ocr_shot(work: Path, shot: dict, memo: Memo | None = None) -> list[dict]:
    """Text in the shot's first / mid / last frames that is not one of its subtitle lines (or name cards)."""
    subs = [x["box"] for x in shot.get("subtitles") or [] if x.get("box")]
    subs += [b for x in shot.get("subtitles") or [] for b in x.get("boxes") or []]
    if memo is not None:
        fr = shot.get("frames") or {}
        key = ["ocr", [frame_stamp(work, fr.get(k)) for k in ("first", "mid", "last")], subs]
        return memo.get(key, lambda: _ocr_shot(work, shot))
    found: list[dict] = []
    for name in ("first", "mid", "last"):
        f = (shot.get("frames") or {}).get(name)
        if not f or not (work / f).is_file():
            continue
        for t in subtitles.read_text(work / f):
            b = t["box"]
            # small text counts too: AIGC labels and corner marks are ~1% of the frame tall
            if len(t["text"]) < 2 or b[3] - b[1] < 0.01 or any(_overlaps(b, sb) for sb in subs):
                continue
            same = next((x for x in found if subtitles.similar_text(x["text"], t["text"]) >= 0.6 and _overlaps(x["box"], b, 0.05)), None)
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


def _overlay_span(work: Path, shot: dict, item: dict, step: float = 0.2, memo: Memo | None = None) -> dict:
    """When a 花字 is on screen (OCR of its box every `step` s) and its colour: assembly redraws it there."""
    if memo is not None:
        return memo.get(["overlay", shot["start"], shot["end"], item["text"], item["box"], step],
                        lambda: _overlay_span(work, shot, item, step))
    import cv2
    cap = cv2.VideoCapture(str(layout.source(work)))
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
        if crop.size and any(subtitles.similar_text(r["text"], item["text"]) >= 0.6 for r in subtitles.read_text(crop, 0.6)):
            seen.append(t)
            color = color or _text_color(img, item["box"])
        t += step
    cap.release()
    if not seen:
        return {"t0": shot["start"], "t1": shot["end"], "color": "#FFFFFF"}
    return {"t0": round(max(shot["start"], seen[0] - step / 2), 2), "t1": round(min(shot["end"], seen[-1] + step / 2), 2),
            "color": color or "#FFFFFF"}


def screen_text(story: dict, work: Path, vlm: VLM, workers: int = 6, batch: int = 6) -> int:
    """Text in the picture besides the subtitles, per shot: scene text (signs, phone screens, documents) the keyframe
    edit must keep legible, post-production 花字 the edit drops and assembly redraws, watermarks that go.
    OCR is local (kept between runs, memo.py); one VLM call per shot that has any text says which is which."""
    with Memo(work, "screen_text") as memo:
        return _screen_text(story, work, vlm, memo, workers, batch)


def _screen_text(story: dict, work: Path, vlm: VLM, memo: Memo, workers: int, batch: int) -> int:

    def keep(s: dict, found: list[dict], kinds: dict[int, str]) -> list[dict]:
        out = []
        for k, x in enumerate(found):
            kind = kinds.get(k) if kinds.get(k) in (*TEXT_KINDS, "noise") else "unknown"
            if kind == "noise":
                continue
            x["kind"] = kind
            if kind == "overlay":
                x.update(_overlay_span(work, s, x, memo=memo))
            out.append(x)
        return out

    def one(s: dict) -> list[dict]:
        found = _ocr_shot(work, s, memo)
        if not found:
            return []
        rows = "\n".join(f"{k}|{x['text']}|{where(x['box'])}" for k, x in enumerate(found))
        frame = work / s["frames"][found[0]["frames"][0]]
        try:
            r = vlm.ask_json(SCREEN_TEXT_PROMPT.format(index=s["index"], texts=rows), [frame], max_tokens=1024, timeout=120)
        except Exception as exc:  # unknown kind: lint shows it
            LOG.warning("第 %s 镜画面文字分类失败：%s", s["index"], exc)
            return [{**x, "kind": "unknown"} for x in found]
        return keep(s, found, {int(k["i"]): k.get("kind") for k in r.get("texts") or [] if str(k.get("i", "")).isdigit()})

    if story.get("depth") != "fast":
        with Pool(workers) as ex:
            for s, texts in zip(story["shots"], ex.map(one, story["shots"])):
                s["screen_text"] = texts
        return sum(len(s["screen_text"]) for s in story["shots"])

    # fast: OCR every shot locally, then one model call per `batch` shots that have text
    with Pool(workers) as ex:
        found = dict(zip((s["index"] for s in story["shots"]), ex.map(lambda s: _ocr_shot(work, s, memo), story["shots"])))
    memo.save()  # before the questions below stop the run
    # a shot full of text (a system panel, a page) gets its own call: batched with others, the model stops sorting out noise
    todo = [s for s in story["shots"] if 0 < len(found[s["index"]]) <= 4]
    heavy = [s for s in story["shots"] if len(found[s["index"]]) > 4]
    groups = [todo[k:k + batch] for k in range(0, len(todo), batch)] + [[s] for s in heavy]

    def ask(group: list[dict]) -> dict[int, dict[int, str]]:
        rows = "\n".join(f"{s['index']}|{k}|{x['text']}|{where(x['box'])}" for s in group for k, x in enumerate(found[s["index"]]))
        frames = [work / s["frames"][found[s["index"]][0]["frames"][0]] for s in group]
        try:
            r = vlm.ask_json(SCREEN_TEXT_BATCH_PROMPT.format(n=len(group), order="、".join(f"镜{s['index']}" for s in group),
                                                             texts=rows), frames, max_tokens=4096, timeout=240)
        except Exception as exc:
            LOG.warning("画面文字分类失败（镜 %s–%s）：%s", group[0]["index"], group[-1]["index"], exc)
            return {}
        out: dict[int, dict[int, str]] = {}
        for k in r.get("texts") or []:
            if str(k.get("shot", "")).isdigit() and str(k.get("i", "")).isdigit():
                out.setdefault(int(k["shot"]), {})[int(k["i"])] = k.get("kind")
        return out

    with Pool(workers) as ex:
        kinds = {k: v for got in ex.map(ask, groups) for k, v in got.items()}
    for s in story["shots"]:
        s["screen_text"] = keep(s, found[s["index"]], kinds.get(s["index"], {})) if found[s["index"]] else []
    return sum(len(s["screen_text"]) for s in story["shots"])
