"""Timed burned-in subtitle track: OCR the subtitle band every `step` seconds, merge runs of the same line.

Output: list of {start, end, text, y_center, height, boxes:[[x0,y0,x1,y1] normalized]}.
The boxes are reused to mask the text out of keyframes before editing.
"""

from __future__ import annotations

import difflib
import logging
import re
import statistics
from collections import Counter
from pathlib import Path

LOG = logging.getLogger(__name__)
_OCR = None

# How a line is spoken (per-line speakers, analyze.attribute_lines). Only "on" moves a mouth on screen:
# an inner monologue is the character's voice with the mouth shut, even when they fill the frame.
MODES = ("on", "off", "inner", "narration")
MODE_CN = {"on": "", "off": "画外", "inner": "内心独白", "narration": "旁白"}
NARRATOR = "narrator"  # speaker of a narration line whose teller is not one of the characters


def line_mode(x: dict) -> str:
    """Speech mode of a subtitle line; older story.json files only carry on_screen."""
    m = x.get("mode")
    if m in MODES:
        return m
    return "on" if x.get("on_screen", True) else "off"


def _ocr():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        _OCR = RapidOCR()
    return _OCR


def read_text(frame, min_score: float = 0.7) -> list[dict]:
    """All text OCR finds in a picture (BGR array or image path): [{text, box: normalized [x0,y0,x1,y1]}]."""
    import cv2
    if not hasattr(frame, "shape"):
        frame = cv2.imread(str(frame))
        if frame is None:
            return []
    h, w = frame.shape[:2]
    scale = min(1.0, 1280 / max(h, w))
    img = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else frame
    res, _ = _ocr()(img)
    out = []
    for box, text, score in res or []:
        xs, ys = [q[0] / scale for q in box], [q[1] / scale for q in box]
        if float(score) >= min_score and text.strip():
            out.append({"text": text.strip(), "box": [min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h]})
    return out


def similar_text(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _band_lines(frame, band: tuple[float, float]) -> list[dict]:
    """Centred text in the subtitle band of one frame: [{text, box}], top to bottom."""
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
    lines.sort(key=lambda l: l["box"][1])
    return lines


_CJK = re.compile(r"[\u3400-\u9fff]")
_WORD = re.compile(r"[\w\u3400-\u9fff]")


def _yc(l: dict) -> float:
    return (l["box"][1] + l["box"][3]) / 2


def _tall(l: dict) -> float:
    return l["box"][3] - l["box"][1]


def _sized(l: dict, row: dict) -> bool:
    return 0.7 <= _tall(l) / row["h"] <= 1.4


def _stacks(lines: list[dict], row: dict) -> list[list[dict]]:
    """Runs of lines set right under each other in the subtitle size (a subtitle wrapped onto two lines)."""
    lines = sorted((l for l in lines if _sized(l, row)), key=_yc)
    out = []
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            if not 0.8 <= (_yc(lines[j]) - _yc(lines[j - 1])) / row["h"] <= 1.5:
                break
            out.append(lines[i:j + 1])
    return out


def _lean(group: list[dict], row: dict) -> float:
    """How far the middle of a group of lines sits from the subtitle line, in line heights."""
    return (statistics.mean(map(_yc, group)) - row["y"]) / row["h"]


def find_row(samples: list[list[dict]]) -> dict | None:
    """The line the subtitles are set on: {y, h, cjk, lean}. Subtitles come back at the same height in the same
    size all video long; a logo on a shirt, a shop sign or a phone number on a van passes through the band once.
    lean: where a subtitle of two lines sits (0 = centred on the line, ±0.6 = the second line goes under / over it).
    None when the text has no steady line (free-floating captions): nothing is filtered."""
    lines = [l for hit in samples for l in hit if _WORD.search(l["text"])]
    if len(lines) < 20:
        return None
    bins = Counter(round(_yc(l) / 0.01) for l in lines)
    b = max(bins, key=lambda k: bins[k - 1] + bins[k] + bins[k + 1])
    near = [l for l in lines if abs(_yc(l) / 0.01 - b) <= 1.5]
    row = {"y": statistics.median(map(_yc, near)), "h": statistics.median(map(_tall, near)), "cjk": False, "lean": [0]}
    main = [l for l in lines if _sized(l, row) and abs(_lean([l], row)) <= 0.3]
    if len(main) < 0.5 * len(lines):
        return None
    row["cjk"] = sum(bool(_CJK.search(l["text"])) for l in main) >= 0.7 * len(main)
    pairs = [g for hit in samples for g in _stacks(hit, row) if len(g) == 2 and _script(g, row)]
    for k in (-0.6, 0.6):
        n = sum(abs(_lean(g, row) - k) <= 0.3 for g in pairs)
        if n >= 10 and n >= 0.6 * len(pairs):
            row["lean"].append(k)
    return row


def _script(group: list[dict], row: dict) -> bool:
    return not row["cjk"] or all(_CJK.search(l["text"]) for l in group)


def subtitle_lines(lines: list[dict], row: dict | None) -> list[dict]:
    """The lines of one frame that are the subtitle (on the subtitle line, in its size and script), not text in the
    picture. A name card or caption drawn much bigger across the line is kept: refine.mark_cards tells it apart."""
    lines = [l for l in lines if _WORD.search(l["text"])]  # ® + … read off a logo
    if row is None:
        return lines
    groups = [g for g in _stacks(lines, row) if _script(g, row) and any(abs(_lean(g, row) - k) <= 0.3 for k in row["lean"])]
    if groups:
        return max(groups, key=len)
    for l in lines:
        if _sized(l, row) and abs(_lean([l], row)) <= 0.3:
            if _script([l], row) or (abs(_lean([l], row)) <= 0.15 and 0.8 <= _tall(l) / row["h"] <= 1.25):
                return [l]  # a subtitle in another script ("OK") sits exactly on the line; a logo does not
    big = [l for l in lines if _tall(l) >= 1.8 * row["h"] and l["box"][1] < row["y"] < l["box"][3] and _script([l], row)]
    return big[:1]


def _hit(lines: list[dict], row: dict | None) -> dict | None:
    lines = subtitle_lines(lines, row)
    if not lines:
        return None
    return {"text": "".join(l["text"] for l in lines), "boxes": [l["box"] for l in lines]}


def _similar(a: str, b: str) -> bool:
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


BAND = (0.84, 0.985)  # where most dramas put the subtitles


def find_band(video: Path, samples: int = 16) -> tuple[float, float]:
    """Where this video's subtitles sit: the height at which centred one-line text shows up in the most of a few
    frames spread over the video. Live-action dramas often put them at 70-80% of the height, not at the bottom."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    seen: dict[int, set[int]] = {}
    heights: dict[int, list[float]] = {}
    for k in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (0.05 + 0.9 * k / max(1, samples - 1))))
        ok, frame = cap.read()
        if not ok:
            continue
        for x in read_text(frame):
            x0, y0, x1, y1 = x["box"]
            if abs((x0 + x1) / 2 - 0.5) < 0.1 and 0.012 < y1 - y0 < 0.08 and x1 - x0 < 0.9:
                b = round((y0 + y1) / 2 / 0.02)
                seen.setdefault(b, set()).add(k)
                heights.setdefault(b, []).append(y1 - y0)
    cap.release()
    if not seen:
        return BAND
    b = max(seen, key=lambda b: (len(seen[b]) + len(seen.get(b - 1, ())) + len(seen.get(b + 1, ())), len(seen[b])))
    yc = b * 0.02
    if len(seen[b]) < 3 or BAND[0] <= yc <= BAND[1]:
        return BAND
    hs = sorted(heights[b])
    half = max(0.05, 2.5 * hs[len(hs) // 2])
    band = (round(max(0.0, yc - half), 3), round(min(1.0, yc + half), 3))
    LOG.info("字幕不在底部：按 %.0f%%–%.0f%% 高度读", band[0] * 100, band[1] * 100)
    return band


def read_samples(video: Path, step: float = 0.2, band: tuple[float, float] | None = None) -> list[tuple[float, list[dict]]]:
    """OCR of the subtitle band every `step` seconds: [(time, lines of that frame)]."""
    import cv2
    band = band or find_band(video)
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
    return samples


def track(samples: list[tuple[float, list[dict]]], step: float = 0.2) -> list[dict]:
    """The subtitle track from the per-frame OCR. Text that is not on the subtitle line (find_row) is part of the
    picture and left out."""
    row = find_row([lines for _, lines in samples])
    hits = [(t, _hit(lines, row)) for t, lines in samples]
    kept = {id(b) for _, hit in hits if hit for b in hit["boxes"]}
    dropped = Counter(l["text"] for _, lines in samples for l in lines if id(l["box"]) not in kept)
    if dropped:
        LOG.info("不在字幕行上的字不算字幕：%s", "、".join(t for t, _ in dropped.most_common(12)))

    segs: list[dict] = []
    cur = None
    for t, hit in hits:
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
    text_of = lambda s: Counter(s["variants"]).most_common(1)[0][0]
    joined: list[dict] = []
    for s in segs:  # one line read in two halves (something in the picture crossed it for a moment)
        p = joined[-1] if joined else None
        if p and s["start"] - p["end"] <= 0.6 and _similar(text_of(s), text_of(p)):
            p.update(end=s["end"], variants=p["variants"] + s["variants"], boxes=p["boxes"] + s["boxes"])
        else:
            joined.append(s)

    out = []
    for s in joined:
        if s["end"] - s["start"] < 0.3:
            continue  # flicker / misread
        b = s["boxes"]
        union = [min(x[0] for x in b), min(x[1] for x in b), max(x[2] for x in b), max(x[3] for x in b)]
        heights = sorted(x[3] - x[1] for x in b)
        out.append({"start": round(s["start"], 2), "end": round(s["end"], 2), "text": text_of(s),
                    "y_center": round((union[1] + union[3]) / 2, 4), "height": round(heights[len(heights) // 2], 4),
                    "box": [round(v, 4) for v in union]})
    LOG.info("字幕 %d 条", len(out))
    return out


def extract(video: Path, step: float = 0.2, band: tuple[float, float] | None = None) -> list[dict]:
    return track(read_samples(video, step, band), step)


def in_span(subs: list[dict], start: float, end: float) -> list[dict]:
    """The subtitle lines on screen between two times (a line over a cut is in both shots)."""
    def inside(s: dict) -> bool:
        over = min(s["end"], end) - max(s["start"], start)
        # the track is read every 0.2s, so a line's end is a little late: a sliver over the cut is not a second shot
        return over > 0.05 and (over >= 0.3 or over >= 0.3 * (s["end"] - s["start"]) or over >= 0.8 * (end - start))
    return [s for s in subs if inside(s)]


def carry(new: list[dict], old: list[dict], keys: tuple[str, ...]) -> tuple[list[dict], list[dict]]:
    """A re-read track with what was settled on the old one: each new line takes `keys` (and the proofread text)
    from the old line it overlaps and reads like. Returns (new lines, old lines nothing matches any more)."""
    used, out = set(), []
    for x in new:
        best, score = None, 0.0
        for i, o in enumerate(old):
            over = min(x["end"], o["end"]) - max(x["start"], o["start"])
            if over <= 0 or o.get("card"):
                continue
            read = o.get("ocr", o["text"])
            if x["text"] != read and x["text"] not in read and similar_text(x["text"], read) < 0.5:
                continue
            if over > score:
                best, score = i, over
        x = dict(x)
        if best is not None:
            o = old[best]
            used.add(best)
            x.update({k: o[k] for k in keys if k in o})
            if x["text"] == o.get("ocr", o["text"]) and o["text"] != x["text"]:
                x.update(ocr=x["text"], text=o["text"])  # the proofread wording
        out.append(x)
    return out, [o for i, o in enumerate(old) if i not in used]


def to_srt(subs: list[dict], path: Path) -> None:
    def ts(x: float) -> str:
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{i}\n{ts(s['start'])} --> {ts(s['end'])}\n{s['text']}\n"
                              for i, s in enumerate(subs, 1)), encoding="utf-8")


def unique_lines(story: dict, cards: bool = False) -> list[dict]:
    """Every subtitle line once. A line that runs across a cut sits in both shots (each with its own mode: said on
    camera in one, heard over the listener in the other); counted once, the copy said on camera speaks for it."""
    out: dict[tuple, dict] = {}
    for s in story.get("shots") or []:
        for x in s.get("subtitles") or []:
            if x.get("card") and not cards:
                continue
            k = (x.get("start"), x.get("end"), x.get("text"))
            if k not in out or (line_mode(x) == "on" and line_mode(out[k]) != "on"):
                out[k] = x
    return sorted(out.values(), key=lambda x: (x.get("start") or 0))
