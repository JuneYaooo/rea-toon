"""Put the remake back on the original timeline: same cuts, same durations, original soundtrack."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .media import SYNC_SHIFT, count_frames, erase_text, font_path, probe, run

LOG = logging.getLogger(__name__)


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%").replace(",", "\\,")


def _sub_size(s: dict, h: int, w: int | None = None) -> int:
    """Font size of a reburned line. OCR box heights run ~25% short of the glyph size, so the width of the
    original line (one CJK glyph is ~1 em wide) is the better measure; the height is the fallback."""
    by_h = s["height"] * h * 0.78
    box, n = s.get("box"), len(s.get("text", "").strip())
    if w and box and n:
        by_w = (box[2] - box[0]) * w / n
        return max(18, int(min(max(by_w, by_h), 2.2 * by_h)))
    return max(18, int(by_h))


def _subtitle_filter(shot: dict, h: int, w: int | None = None) -> str:
    font = font_path()
    if not font or not shot.get("subtitles"):
        return ""
    parts = []
    for s in shot["subtitles"]:
        a = max(0.0, s["start"] - shot["start"])
        b = min(shot["duration"], s["end"] - shot["start"])
        if b - a < 0.1:
            continue
        size = _sub_size(s, h, w)
        y = int(s["y_center"] * h - size / 2)
        parts.append(f"drawtext=fontfile='{font}':text='{_esc(s['text'])}':fontsize={size}:fontcolor=white:"
                     f"borderw={max(2, size // 10)}:bordercolor=black@0.7:shadowx=2:shadowy=2:shadowcolor=black@0.45:x=(w-text_w)/2:y={y}:enable='between(t,{a:.3f},{b:.3f})'")
    return ",".join(parts)


def _overlay_filter(shot: dict, h: int, w: int) -> str:
    """花字 of the original (breakdown screen_text, kind overlay): the keyframes leave it out, so a regenerated shot
    gets it drawn back at the original place, time and colour (a plain font: the original lettering is not recovered)."""
    font = font_path()
    items = [t for t in shot.get("screen_text") or [] if t.get("kind") == "overlay" and t.get("box")]
    if not font or not items:
        return ""
    parts = []
    for t in items:
        a = max(0.0, t.get("t0", shot["start"]) - shot["start"])
        b = min(shot["duration"], t.get("t1", shot["end"]) - shot["start"])
        if b - a < 0.1:
            continue
        x0, y0, x1, y1 = t["box"]
        size = _sub_size({"height": y1 - y0, "box": t["box"], "text": t["text"]}, h, w)
        color = str(t.get("color") or "#FFFFFF").replace("#", "0x")
        parts.append(f"drawtext=fontfile='{font}':text='{_esc(t['text'])}':fontsize={size}:fontcolor={color}:"
                     f"borderw={max(2, size // 8)}:bordercolor=black@0.8:x={int((x0 + x1) / 2 * w)}-text_w/2:"
                     f"y={int(y0 * h)}:enable='between(t,{a:.3f},{b:.3f})'")
    return ",".join(parts)


def _freeze_filter(shot: dict, fps: float) -> str:
    """Freeze frames of the original (breakdown): a generated clip keeps moving, so hold its picture there."""
    parts = []
    for t0, t1 in shot.get("freeze") or []:
        a, b = round(t0 * fps), round(t1 * fps) - 1
        if b > a:
            parts.append(f"freezeframes=first={a}:last={b}:replace={a}")
    return ",".join(parts)


def aigc_mark(story: dict) -> dict | None:
    """Where the original carries its AI-content label (most shots agree): the remake's label goes to the same place."""
    from collections import Counter
    seen = Counter()
    boxes = {}
    for s in story["shots"]:
        for t in s.get("screen_text") or []:
            if t.get("kind") == "aigc":
                k = (round(t["box"][0], 1), round(t["box"][1], 1))
                seen[k] += 1
                boxes.setdefault(k, t)
    return boxes[seen.most_common(1)[0][0]] if seen else None


def _label_filter(text: str, h: int, w: int, mark: dict | None) -> str:
    """The AI-content label: at the original's spot and size if it had one, else small in the top-left corner."""
    font = font_path()
    if not font or not text:
        return ""
    if mark:
        x0, y0, x1, y1 = mark["box"]
        size = max(14, int((y1 - y0) * h))
        x, y = int(x0 * w), int(y0 * h)
    else:
        size, x, y = max(14, h // 40), max(8, w // 40), max(8, h // 40)
    return (f"drawtext=fontfile='{font}':text='{_esc(text)}':fontsize={size}:fontcolor=white@0.85:"
            f"borderw=1:bordercolor=black@0.5:x={x}:y={y}")


def segment(work: Path, story: dict, shot: dict, clip: Path | None, out: Path, reburn: bool,
            mode: str | None = None, av_lag: float | None = None, fps: float | None = None,
            label: str = "") -> Path:
    W, H, dur = story["width"], story["height"], shot["duration"]
    fps = fps or story["fps"]
    # cut on the original frame grid so 36 rounded segments still add up to the source length
    n_frames = max(1, round(shot["end"] * fps) - round(shot["start"] * fps))
    norm = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},setsar=1"
    mark = aigc_mark(story) if label else None
    if clip is None:  # reuse original footage
        # an original that already carries the label shows it in its reused shots; otherwise every shot gets ours
        lab = _label_filter(label, H, W, None) if label and not mark else ""
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{round(shot['start'] * fps) / fps:.4f}", "-i", str(work / "source.mp4"),
               "-vf", norm + ("," + lab if lab else "")]
    else:
        if mode != "kenburns":  # rendered from a keyframe that has no subtitles
            clip = erase_text(clip)  # H3's own burned-in subtitles would double up with the reburned ones
        gen = probe(clip)["duration"] or dur
        ratio = dur / gen
        # stretch to fit when close (keeps the last-frame target on the cut); otherwise keep the start
        # lip-synced / motion-referenced clips run on the source clock: never retime them, just cut the padded tail
        # ref_video clips follow the original shot's clock too
        synced = av_lag is not None  # aligned to the original voice: keep the source clock
        timing = f"setpts={ratio:.5f}*PTS," if ratio >= 0.74 and mode not in ("lipsync", "ref_video") and not synced else ""
        av_lag = av_lag or 0.0
        # H3 voiced the line `av_lag` seconds late (or early): shift the picture so the mouths hit the original voice
        if av_lag > 0.02:
            timing = f"trim=start={av_lag:.3f},setpts=PTS-STARTPTS," + timing
        elif av_lag < -0.02:
            timing = f"tpad=start_mode=clone:start_duration={-av_lag:.3f}," + timing
        # a clip shorter than the shot (or trimmed by av_lag) holds its last frame instead of running out;
        # tpad only pads reliably after the fps filter, so it goes after norm
        vf = timing + norm + ",tpad=stop_mode=clone:stop_duration=2"
        frz = _freeze_filter(shot, fps)
        vf += ("," + frz) if frz else ""
        over = _overlay_filter(shot, H, W)
        vf += ("," + over) if over else ""
        if reburn:
            sub = _subtitle_filter(shot, H, W)
            vf += ("," + sub) if sub else ""
        lab = _label_filter(label, H, W, mark)  # the keyframes dropped the original's label with the other marks
        vf += ("," + lab) if lab else ""
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vf", vf]
    cmd += ["-frames:v", str(n_frames), "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    run(cmd)
    return out


def shot_source(shot: dict, plan: dict | None, out_dir: Path) -> dict:
    """Where a shot's picture comes from, read from disk only (no run state that a partial re-run could corrupt):
    a regenerated shot uses clips/<id>.mp4 and the mode / voice lag recorded in its .meta.json; a shot whose clip
    is missing falls back to the original footage and is reported."""
    clip = out_dir / "clips" / f"{shot['id']}.mp4"
    if not plan or plan.get("route") != "regen":
        return {"clip": None, "status": "reuse"}
    meta_p = clip.with_name(clip.name + ".meta.json")
    if not clip.is_file() or not meta_p.is_file():
        return {"clip": None, "status": "missing"}
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    sync = meta.get("sync") or {}
    lag = float(sync.get("lag", 0.0)) if sync.get("corr", 0) >= SYNC_SHIFT else None
    return {"clip": clip, "status": "regen", "mode": meta.get("mode", plan.get("mode")), "av_lag": lag}


def output_fps(story: dict, clips: list[Path]) -> float:
    """The cut runs at the generated clips' rate when they share one (H3: 24 fps). Putting 24 fps clips on a 30 fps
    timeline repeats every 4th frame, which reads as a steady stutter; 30 fps sources of short dramas are often
    24p content anyway (1 repeated frame in 5)."""
    rates = {round(probe(c).get("fps") or 0, 3) for c in clips}
    return rates.pop() if len(rates) == 1 and 0 < min(rates | {1e9}) <= story["fps"] else story["fps"]


def assemble(work: Path, out_dir: Path, plan: list[dict], reburn: bool = True, compare: bool = True,
             label: str = "") -> dict:
    """plan: the full-film shot plan (Remake.plan()["shots"])."""
    story = json.loads((work / "story.json").read_text(encoding="utf-8"))
    by_index = {p["index"]: p for p in plan}
    segs, used = [], {"regen": [], "reuse": [], "missing": []}
    sources = [(shot, shot_source(shot, by_index.get(shot["index"]), out_dir)) for shot in story["shots"]]
    # local push / pan renders take any rate: only the generated clips decide it
    fps = output_fps(story, [s["clip"] for _, s in sources if s["clip"] and s.get("mode") != "kenburns"])
    for shot, src in sources:
        used[src["status"]].append(shot["index"])
        seg = segment(work, story, shot, src["clip"], out_dir / "segments" / f"{shot['id']}.mp4", reburn,
                      src.get("mode"), av_lag=src.get("av_lag"), fps=fps, label=label)
        want = max(1, round(shot["end"] * fps) - round(shot["start"] * fps))
        got = count_frames(seg)
        if got != want:  # a short segment shifts every later shot off the original voice
            raise RuntimeError(f"镜头 {shot['index']} 拼接后 {got} 帧，应为 {want} 帧")
        segs.append(seg)
    lst = out_dir / "segments" / "list.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segs), encoding="utf-8")
    silent = out_dir / "video_only.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent)])
    final = out_dir / "final.mp4"
    # the picture sets the length: pad the audio (never drop the last frames to the audio's length)
    run(["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-i", str(work / "source.mp4"), "-map", "0:v", "-map", "1:a?",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-af", "apad", "-shortest", str(final)])
    out = {"final": str(final), "fps": fps, **used}
    if compare:
        cmp_path = out_dir / "compare.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(work / "source.mp4"), "-i", str(final), "-filter_complex",
             "[0:v]scale=960:-2,drawtext=fontfile='%s':text='原片':fontsize=28:fontcolor=white:x=16:y=12:box=1:boxcolor=black@0.4[a];"
             "[1:v]scale=960:-2,drawtext=fontfile='%s':text='复刻':fontsize=28:fontcolor=white:x=16:y=12:box=1:boxcolor=black@0.4[b];"
             "[a][b]hstack" % (font_path(), font_path()), "-map", "1:a?", "-c:v", "libx264", "-crf", "22", "-c:a", "aac",
             str(cmp_path)])
        out["compare"] = str(cmp_path)
    review_sheet(work, story, out_dir, [i for i in used["regen"]])
    (out_dir / "assemble.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def review_sheet(work: Path, story: dict, out_dir: Path, regen: list[int]) -> Path | None:
    """Rows of original first/last vs new first/last for every regenerated shot."""
    rows = [s for s in story["shots"] if s["index"] in set(regen)]
    if not rows:
        return None
    tw, th = 384, int(384 * story["height"] / story["width"])
    font = ImageFont.truetype(font_path(), 22) if font_path() else ImageFont.load_default()
    sheet = Image.new("RGB", (tw * 4, th * len(rows)), "black")
    for r, s in enumerate(rows):
        new = {k: out_dir / "frames" / f"{s['id']}_{k}.png" for k in ("first", "last")}
        cells = [work / s["frames"]["first"], new["first"], work / s["frames"]["last"], new["last"]]
        for c, p in enumerate(cells):
            if p and Path(p).is_file():
                sheet.paste(Image.open(p).convert("RGB").resize((tw, th)), (c * tw, r * th))
        ImageDraw.Draw(sheet).text((6, r * th + 4), f"#{s['index']}", fill="yellow", font=font)
    p = out_dir / "review.jpg"
    sheet.save(p, quality=85)
    return p
