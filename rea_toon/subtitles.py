"""Timed burned-in subtitle track: OCR the subtitle band every `step` seconds, merge runs of the same line.

Output: list of {start, end, text, y_center, height, boxes:[[x0,y0,x1,y1] normalized]}.
The boxes are reused to mask the text out of keyframes before editing.
"""

from __future__ import annotations

import difflib
import logging
from collections import Counter
from pathlib import Path

LOG = logging.getLogger(__name__)
_OCR = None


def _ocr():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        _OCR = RapidOCR()
    return _OCR


def _band_lines(frame, band: tuple[float, float]):
    h, w = frame.shape[:2]
    y0, y1 = int(h * band[0]), int(h * band[1])
    crop = frame[y0:y1]
    scale = min(1.0, 960 / w)
    if scale < 1.0:
        import cv2
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    res, _ = _ocr()(crop)
    lines = []
    for box, text, score in res or []:
        xs = [p[0] / scale for p in box]
        ys = [p[1] / scale + y0 for p in box]
        cx = (min(xs) + max(xs)) / 2 / w
        if float(score) < 0.6 or not text.strip() or abs(cx - 0.5) > 0.2:
            continue  # subtitles are centred; skip props / signs at the sides
        lines.append({"text": text.strip(), "box": [min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h]})
    if not lines:
        return None
    lines.sort(key=lambda l: l["box"][1])
    return {"text": "".join(l["text"] for l in lines), "boxes": [l["box"] for l in lines]}


def _similar(a: str, b: str) -> bool:
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def extract(video: Path, step: float = 0.2, band: tuple[float, float] = (0.84, 0.985)) -> list[dict]:
    import cv2
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    every = max(1, round(fps * step))
    samples = []
    prev_band, prev_hit = None, None
    idx = 0
    while idx < total:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % every == 0:
            h = frame.shape[0]
            small = cv2.cvtColor(cv2.resize(frame[int(h * band[0]):int(h * band[1])], (320, 40)), cv2.COLOR_BGR2GRAY)
            if prev_band is not None and float(cv2.absdiff(small, prev_band).mean()) < 2.0:
                hit = prev_hit  # band unchanged: same subtitle (or none)
            else:
                hit = _band_lines(frame, band)
            prev_band, prev_hit = small, hit
            samples.append((idx / fps, hit))
        idx += 1
    cap.release()

    segs: list[dict] = []
    cur = None
    for t, hit in samples:
        if hit and cur and _similar(hit["text"], cur["variants"][-1]):
            cur["variants"].append(hit["text"])
            cur["end"] = t + step
            cur["boxes"].extend(hit["boxes"])
            continue
        if cur:
            segs.append(cur)
        cur = {"start": t, "end": t + step, "variants": [hit["text"]], "boxes": list(hit["boxes"])} if hit else None
    if cur:
        segs.append(cur)

    out = []
    for s in segs:
        if s["end"] - s["start"] < 0.3:
            continue  # flicker / misread
        text = Counter(s["variants"]).most_common(1)[0][0]
        b = s["boxes"]
        union = [min(x[0] for x in b), min(x[1] for x in b), max(x[2] for x in b), max(x[3] for x in b)]
        heights = sorted(x[3] - x[1] for x in b)
        out.append({"start": round(s["start"], 2), "end": round(s["end"], 2), "text": text,
                    "y_center": round((union[1] + union[3]) / 2, 4), "height": round(heights[len(heights) // 2], 4),
                    "box": [round(v, 4) for v in union]})
    LOG.info("字幕 %d 条", len(out))
    return out


def to_srt(subs: list[dict], path: Path) -> None:
    def ts(x: float) -> str:
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    path.write_text("\n".join(f"{i}\n{ts(s['start'])} --> {ts(s['end'])}\n{s['text']}\n"
                              for i, s in enumerate(subs, 1)), encoding="utf-8")
