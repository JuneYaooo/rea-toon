"""ffmpeg / OpenCV helpers of the remake: clip checks (layout drift, frozen pictures, lip sync), reference clips with
blurred faces, subtitle erasing and Ken Burns renders. The analysis helpers (probe, frames, motion) are in rea_toon.media."""

from __future__ import annotations

import subprocess
from pathlib import Path

from rea_toon.media import font_path, frame_at, mean_luma, probe, run  # noqa: F401  (re-exported for assemble / qa)

STILL_DIFF = 0.3    # mean abs grey-level change (256x144) below which two frames count as the same picture


def cut_audio(src: Path, start: float, dur: float, out: Path, pad_to: float | None = None,
              mute: list[tuple[float, float]] | None = None) -> Path:
    """Cut [start, start+dur]; with pad_to, append silence so the file lasts exactly pad_to seconds.
    mute: (t0, t1) spans in clip time to silence, e.g. off-screen lines: an audio-driven workflow moves the visible
    mouth to every voice it hears, so a voice from off camera must not reach it."""
    out.parent.mkdir(parents=True, exist_ok=True)
    filters = [f"volume=enable='between(t,{a:.3f},{b:.3f})':volume=0" for a, b in mute or [] if b > a]
    if pad_to and pad_to > dur:
        filters.append(f"apad=whole_dur={pad_to:.3f}")
    af = ["-af", ",".join(filters)] if filters else []
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src), *af,
         "-ac", "1", "-ar", "24000", str(out)])
    return out


def layout_similarity(a: Path, b: Path) -> float:
    """Composition likeness of two images (normalised grey thumbnails, -1..1). Swapped faces or costumes
    barely move it (~0.95); a different framing, background or a cut drops it below ~0.5."""
    import cv2
    import numpy as np

    def thumb(p):
        im = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        im = cv2.resize(im, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)
        return (im - im.mean()) / (im.std() + 1e-6)

    return float((thumb(a) * thumb(b)).mean())


def clip_drift(source: Path, start: float, clip: Path, duration: float, tmp: Path, n: int = 5) -> dict:
    """Compare a clip with the source shot at the same moments (clip not retimed).
    layout = lowest composition likeness; light = lowest brightness ratio clip/source."""
    tmp.mkdir(parents=True, exist_ok=True)
    layout, light = 1.0, 9.9
    for k in range(n):
        t = duration * (k + 0.5) / n
        a, b = tmp / f"src_{k}.png", tmp / f"gen_{k}.png"
        frame_at(source, start + t, a)
        frame_at(clip, t, b)
        if a.is_file() and b.is_file():
            layout = min(layout, layout_similarity(a, b))
            light = min(light, (mean_luma(b) + 1) / (mean_luma(a) + 1))
    return {"layout": round(layout, 3), "light": round(light, 3)}


def keyframe_drift(clip: Path, keys: list[tuple[float, Path]], tmp: Path) -> dict:
    """Like clip_drift, but against the new keyframes (restyled remakes: the source looks different by design).
    keys = [(seconds into the clip, keyframe image)]."""
    tmp.mkdir(parents=True, exist_ok=True)
    layout, light = 1.0, 9.9
    for k, (t, key) in enumerate(keys):
        b = tmp / f"gen_key_{k}.png"
        frame_at(clip, t, b)
        if b.is_file() and Path(key).is_file():
            layout = min(layout, layout_similarity(Path(key), b))
            light = min(light, (mean_luma(b) + 1) / (mean_luma(Path(key)) + 1))
    return {"layout": round(layout, 3), "light": round(light, 3), "against": "keyframes"}


FROZEN_MIN = 0.45   # a clip with at least this share of still frames...
FROZEN_OVER = 0.3   # ...and this much more than the original shot is frozen (H3 FL2VA: 70-96% vs Ref2VA <= 33%)


def still_fraction(video: Path, start: float = 0.0, dur: float | None = None) -> float:
    """Share of frames that repeat the previous picture. Generated clips have no sensor noise, so a clip that
    barely moves (H3 FL2VA holds the picture and jumps every 4 frames) scores far above any real footage."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(video))
    if start:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    n = int((dur or 1e6) * (cap.get(cv2.CAP_PROP_FPS) or 24))
    prev, still, total = None, 0, 0
    while total < n:
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(f, (256, 144)), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            total += 1
            still += float(np.abs(g - prev).mean()) < STILL_DIFF
        prev = g
    cap.release()
    return still / total if total else 0.0


def frozen(clip: Path, dur: float, source: Path, start: float) -> dict:
    """Is the clip frozen compared with the original shot? (originals often repeat 1 frame in 5: 24p shown at 30p)"""
    c, o = still_fraction(clip, 0, dur), still_fraction(source, start, dur)
    return {"still": round(c, 2), "orig_still": round(o, 2), "frozen": c >= FROZEN_MIN and c > o + FROZEN_OVER}


REF_MIN_S = 2.1     # H3 Ref2VA rejects reference videos under 2 s


def ref_clip(source: Path, voice: Path, start: float, dur: float, subtitles: list[dict], out: Path,
             size: tuple[int, int] = (1024, 576), fps: int = 24, hide_faces: bool = True,
             mute: list[tuple[float, float]] | None = None) -> Path:
    """The original shot as a motion reference: burned subtitles blurred away, 24 fps, the shot's voice as audio.
    hide_faces blurs the large (lead) faces: otherwise Ref2VA copies the original actors' identity.
    mute: off-screen lines, silenced: with the faces blurred, the model gives any voice it hears to a visible mouth."""
    W, H = size
    blurs = []
    for s in subtitles or []:
        a, b = max(0.0, s["start"] - start), min(dur, s["end"] - start)
        if b - a <= 0:
            continue
        x0, y0, x1, y1 = s["box"]
        x, y = max(2, int(x0 * W) - 8), max(2, int(y0 * H) - 8)
        w, h = min(W - 3, int(x1 * W) + 8) - x, min(H - 3, int(y1 * H) + 8) - y
        if w > 4 and h > 4:
            blurs.append(f"delogo=x={x}:y={y}:w={w}:h={h}:enable='between(t,{a - 0.05:.3f},{b + 0.05:.3f})'")
    # short shots: hold the last frame (and pad silence) up to the model's minimum; the clip is cut back to the shot
    pad = max(0.0, REF_MIN_S - dur)
    tail = [f"tpad=stop_mode=clone:stop_duration={pad:.3f}"] if pad else []
    vf = ",".join([f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},setsar=1", *blurs, *tail])
    afs = [f"volume=enable='between(t,{a:.3f},{b:.3f})':volume=0" for a, b in mute or [] if b > a]
    afs += [f"apad=pad_dur={pad:.3f}"] if pad else []
    af = ["-af", ",".join(afs)] if afs else []
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(source),
         "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(voice), "-map", "0:v:0", "-map", "1:a:0",
         "-vf", vf, *af, "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-ac", "2", *([] if pad else ["-shortest"]), str(out)])
    if hide_faces:
        blur_faces(out)
    return out


YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"


def _yunet() -> Path:
    """OpenCV's YuNet face detector (~230 KB), downloaded once."""
    p = Path.home() / ".cache" / "rea-toon" / "face_detection_yunet_2023mar.onnx"
    if not p.is_file():
        import requests
        p.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(YUNET_URL, timeout=120)
        r.raise_for_status()
        p.write_bytes(r.content)
    return p


def blur_faces(video: Path, min_frac: float = 0.02, hold: int = 8) -> Path:
    """Heavily blur every face (and hair), crowd included, in place, keeping head position and motion readable."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    if not frames:
        return video
    H, W = frames[0].shape[:2]
    det = cv2.FaceDetectorYN.create(str(_yunet()), "", (W, H), 0.5, 0.3, 50)
    tmp = video.with_name(video.stem + ".faces.mp4")
    wr = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    found = []
    for f in frames:
        _, faces = det.detect(f)
        found.append([tuple(map(float, r[:4])) for r in (faces if faces is not None else []) if r[2] >= W * min_frac])
    for i, f in enumerate(frames):
        # boxes seen within +-hold frames cover detector misses (turned heads, motion blur, the first frames)
        for x, y, w, h in (b for j in range(max(0, i - hold), min(len(frames), i + hold + 1)) for b in found[j]):
            x0, x1 = max(0, int(x - 0.4 * w)), min(W, int(x + 1.4 * w))
            y0, y1 = max(0, int(y - 0.7 * h)), min(H, int(y + 1.35 * h))
            roi = f[y0:y1, x0:x1]
            if roi.size:
                small = cv2.resize(roi, (max(1, roi.shape[1] // 10), max(1, roi.shape[0] // 10)), interpolation=cv2.INTER_AREA)
                f[y0:y1, x0:x1] = cv2.GaussianBlur(cv2.resize(small, roi.shape[1::-1], interpolation=cv2.INTER_LINEAR), (0, 0), 6)
        wr.write(f)
    wr.release()
    final = video.with_name(video.stem + ".tmp.mp4")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-i", str(video), "-map", "0:v:0", "-map", "1:a:0?",
         "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", str(final)])
    tmp.unlink(missing_ok=True)
    final.replace(video)
    return video


def _envelope(path: Path, start: float = 0.0, dur: float | None = None, hop: float = 0.02):
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", *(["-t", f"{dur:.3f}"] if dur else []), "-i", str(path),
           "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    a = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.int16).astype(float)
    n = int(8000 * hop)
    if len(a) < n * 10:
        return None
    a = a[: len(a) // n * n].reshape(-1, n)
    return np.log1p(np.sqrt((a ** 2).mean(1)))


SYNC_MIN = 0.6     # loudness-envelope correlation of the model's own speech with the original voice: below = retry
SYNC_SHIFT = 0.5   # below this the measured lag is noise: no picture shift


def count_frames(p: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout
    return int(out.strip() or 0)


def frames_between(video: Path, fractions: tuple[float, ...], out_dir: Path, tag: str) -> list[Path]:
    """Frames at fractions of the video's length (what the keyframes don't show: the middle of the clip)."""
    dur = probe(video).get("duration") or 0
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in fractions:
        p = out_dir / f"{tag}_{int(f * 100):02d}.png"
        frame_at(video, dur * f, p)
        if p.is_file():
            paths.append(p)
    return paths


def audio_sync(clip: Path, voice: Path, start: float, dur: float, max_lag: float = 0.8, hop: float = 0.02) -> dict:
    """How well the speech H3 generated inside the clip (the mouths follow it) matches the original voice.
    corr = loudness-envelope correlation at the best lag; lag > 0: the clip speaks `lag` seconds late."""
    import numpy as np
    c, v = _envelope(clip, 0, dur, hop), _envelope(voice, start, dur, hop)
    if c is None or v is None or v.std() < 1e-6 or c.std() < 1e-6:
        return {"corr": 0.0, "lag": 0.0}
    best = (-2.0, 0)
    for lag in range(-int(max_lag / hop), int(max_lag / hop) + 1):
        a, b = (c[lag:], v) if lag >= 0 else (c, v[-lag:])
        m = min(len(a), len(b))
        if m < max(10, 0.6 * len(v)):
            continue
        a, b = a[:m], b[:m]
        if a.std() > 1e-6 and b.std() > 1e-6:
            best = max(best, (float(np.corrcoef(a, b)[0, 1]), lag))
    return {"corr": round(best[0], 3), "lag": round(best[1] * hop, 3)}


def erase_text(video: Path, band: tuple[float, float] = (0.6, 0.99), every: int = 2, hold: int = 3) -> Path:
    """H3 sometimes burns its own subtitles for the spoken line into the clip (it learned that from subtitled
    training videos). Find centred subtitle-like text in the lower band with OCR and inpaint it, so the reburned
    subtitles don't double up. Returns <stem>.notext.mp4 (cached while newer than the clip); the clip itself
    when no text is found."""
    import cv2
    import numpy as np
    from rea_toon.subtitles import _band_lines
    out = video.with_name(video.stem + ".notext.mp4")
    flag = video.with_name(video.stem + ".notext.none")
    # cached per exact clip (size + mtime in ns): a clip replaced within the same second must not reuse the old result
    stamp = video.with_name(video.stem + ".notext.src")
    if not video.is_file():
        return video
    st = video.stat()
    sig = f"{st.st_size}:{st.st_mtime_ns}"
    if stamp.is_file() and stamp.read_text().strip() == sig:
        for p in (out, flag):
            if p.is_file():
                return out if p == out else video
    out.unlink(missing_ok=True)
    flag.unlink(missing_ok=True)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    if not frames:
        return video
    H, W = frames[0].shape[:2]
    boxes: list[list] = [[] for _ in frames]
    for i in range(0, len(frames), every):
        hit = _band_lines(frames[i], band)
        for b in (hit or {}).get("boxes", []):
            if 0.025 <= b[3] - b[1] <= 0.12:  # subtitle-sized lines only
                boxes[i].append(b)
    if not any(boxes):
        flag.touch()
        stamp.write_text(sig)
        return video
    tmp = video.with_name(video.stem + ".notext.tmp.mp4")
    wr = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    for i, f in enumerate(frames):
        near = [b for j in range(max(0, i - hold), min(len(frames), i + hold + 1)) for b in boxes[j]]
        if near:
            mask = np.zeros((H, W), np.uint8)
            for x0, y0, x1, y1 in near:
                px, py = 0.01 * W, 0.012 * H
                cv2.rectangle(mask, (int(x0 * W - px), int(y0 * H - py)), (int(x1 * W + px), int(y1 * H + py)), 255, -1)
            f = cv2.inpaint(f, mask, 5, cv2.INPAINT_TELEA)
        wr.write(f)
    wr.release()
    run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-i", str(video), "-map", "0:v", "-map", "1:a?",
         "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)])
    tmp.unlink(missing_ok=True)
    stamp.write_text(sig)
    return out


def kenburns(image: Path, path: list, size: tuple[int, int], fps: float, dur: float, out: Path) -> Path:
    """Push / pan over one picture: `path` = [t, x0, y0, x1, y1] windows (normalised) of the picture over time."""
    import subprocess
    import cv2
    import numpy as np
    img = cv2.imread(str(image))
    ih, iw = img.shape[:2]
    W, H = size
    ts = [p[0] for p in path]
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                             "-r", f"{fps:g}", "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                             "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    for i in range(max(1, round(dur * fps))):
        t = i / fps
        x0, y0, x1, y1 = (float(np.interp(t, ts, [p[j] for p in path])) for j in (1, 2, 3, 4))
        sx, sy = (x1 - x0) * iw / W, (y1 - y0) * ih / H
        M = np.array([[1 / sx, 0, -x0 * iw / sx], [0, 1 / sy, -y0 * ih / sy]], np.float64)
        flags = cv2.INTER_AREA if sx > 1.3 else cv2.INTER_CUBIC
        frame = cv2.warpAffine(img, M, (W, H), flags=flags | cv2.WARP_FILL_OUTLIERS, borderMode=cv2.BORDER_REPLICATE)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"推拉视频渲染失败：{out}")
    return out
