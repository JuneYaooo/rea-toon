"""Thin ffmpeg/ffprobe helpers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

FONT_CANDIDATES = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)


def run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"命令失败：{' '.join(cmd[:6])}…\n{p.stderr[-1500:]}")


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), {})
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    return {"duration": float(data["format"].get("duration") or 0), "width": int(v.get("width") or 0),
            "height": int(v.get("height") or 0), "fps": float(num) / float(den or 1),
            "has_audio": any(s["codec_type"] == "audio" for s in data["streams"])}


def aspect_of(w: int, h: int) -> str:
    r = w / max(h, 1)
    return "16:9" if r > 1.2 else "9:16" if r < 0.8 else "1:1"


def frame_at(video: Path, t: float, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(t, 0):.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)])
    return out


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


def font_path() -> str | None:
    return next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)


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


def mean_luma(p: Path) -> float:
    import cv2
    return float(cv2.imread(str(p), cv2.IMREAD_GRAYSCALE).mean())


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


STILL_DIFF = 0.3    # mean abs grey-level change (256x144) below which two frames count as the same picture
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
    from .subtitles import _band_lines
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


def transients(path: Path, z_min: float = 8.0, hop: float = 0.01) -> list[tuple[float, float]]:
    """Sharp sound onsets (hits, splashes, door slams, stings) in a track: spectral flux far above its local
    median, as (seconds, strength). The non-vocal stem also carries the music, so beats show up too: callers
    only use these to snap a moment they already know from the picture."""
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view
    sr, win = 16000, 1024
    step = int(sr * hop)
    a = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                                     capture_output=True).stdout, np.int16).astype(np.float32) / 32768
    n = (len(a) - win) // step
    if n < 50:
        return []
    frames = np.lib.stride_tricks.as_strided(a, (n, win), (a.strides[0] * step, a.strides[0]))
    logs = np.log1p(100 * np.abs(np.fft.rfft(frames * np.hanning(win), axis=1)))
    flux = np.concatenate([[0.0], np.maximum(0, np.diff(logs, axis=0)).sum(1)])
    k = int(2 / hop)  # 2 s context
    med = np.median(sliding_window_view(np.pad(flux, (k, k), mode="edge"), 2 * k + 1), axis=1)[:len(flux)]
    mad = np.median(sliding_window_view(np.pad(np.abs(flux - med), (k, k), mode="edge"), 2 * k + 1), axis=1)[:len(flux)] + 1e-6
    z = (flux - med) / mad
    peaks = [i for i in range(1, len(z) - 1) if z[i] > z[i - 1] and z[i] >= z[i + 1] and z[i] >= z_min]
    peaks.sort(key=lambda i: -z[i])
    kept: list[int] = []
    for i in peaks:  # one onset per 0.2 s
        if all(abs(i - j) * hop > 0.2 for j in kept):
            kept.append(i)
    # frame i starts at i*hop; an onset first shows when the window's far edge reaches it
    return sorted((round(i * hop + win / sr, 3), float(z[i])) for i in kept)


def still_runs(video: Path, start: float, dur: float, min_len: float = 0.4) -> list[tuple[float, float]]:
    """Stretches inside a shot where the picture does not change at all (freeze frames, 定格), in shot time.
    A shot that never moves is not a freeze; a run only counts after the picture has moved."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    prev, diffs = None, []
    for _ in range(int(dur * fps)):
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(f, (192, 108)), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            diffs.append(float(np.abs(g - prev).mean()))
        prev = g
    cap.release()
    runs, i = [], 0
    still = [d < 0.08 for d in diffs]  # identical frames; near-still AI footage (only a mouth moving) is ~0.2-0.3
    while i < len(still):
        if still[i]:
            j = i
            while j < len(still) and still[j]:
                j += 1
            moved = any(not x for x in still[:i])
            if moved and (j - i) / fps >= min_len:
                runs.append((round(i / fps, 3), round((j + 1) / fps, 3)))
            i = j
        else:
            i += 1
    if runs and sum(b - a for a, b in runs) > 0.85 * dur:
        return []
    return runs


def _grey(f, w: int = 320):
    import cv2
    h = round(f.shape[0] * w / f.shape[1])
    return cv2.GaussianBlur(cv2.cvtColor(cv2.resize(f, (w, h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY), (5, 5), 0)


MOVED = 24        # grey-level change (blurred, 320 px wide) that counts as a pixel that moved
STILL_SHARE = 0.002   # share of moved pixels a held / pushed still picture stays under: a talking mouth or a falling
#                       drop in a near-still AI shot is 0.3-1.1 % (cat/dog shots 45, 74, 75), a real Ken Burns ~0
PARTIAL_SHARE = 0.12  # ...and a picture where only a small part animates (blink, hair, mouth)


def image_motion(video: Path, start: float, dur: float, masks: list | None = None, n: int = 6) -> dict:
    """How the picture of a shot moves (动态漫画 vs animation):
    - still: one picture held; pan: one picture pushed / panned (Ken Burns) -> `path` = the visible window over time,
      as [t, x0, y0, x1, y1] in the normalised coordinates of the `base` frame (first or last, the widest one);
    - partial: the picture holds, only a small region moves (`where` = its box); full: anything else.
    Burned subtitles / 花字 (`masks`, normalised boxes) stay put on a moving picture and are ignored."""
    import cv2
    import numpy as np
    if dur < 0.5:
        return {"kind": "full"}
    cap = cv2.VideoCapture(str(video))
    times = [round(0.06 + (dur - 0.12) * k / (n - 1), 3) for k in range(n)]
    frames, sharp = [], []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, (start + t) * 1000)
        ok, f = cap.read()
        if not ok:
            break
        frames.append(_grey(f))
        sharp.append(cv2.cvtColor(cv2.resize(f, (480, round(f.shape[0] * 480 / f.shape[1])), interpolation=cv2.INTER_AREA),
                                  cv2.COLOR_BGR2GRAY))
    cap.release()
    if len(frames) < n:
        return {"kind": "full"}
    h, w = frames[0].shape
    mask = np.full((h, w), 255, np.uint8)
    for x0, y0, x1, y1 in masks or []:
        cv2.rectangle(mask, (int((x0 - 0.02) * w), int((y0 - 0.02) * h)), (int((x1 + 0.02) * w), int((y1 + 0.02) * h)), 0, -1)
    # features on the sharp frames (480 px), the pixel comparison on the blurred ones (320 px)
    sift = cv2.SIFT_create(2000, contrastThreshold=0.015)
    f = 480 / w
    big = cv2.resize(mask, (480, round(h * f)), interpolation=cv2.INTER_NEAREST)
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    feats = [sift.detectAndCompute(sg, big) for sg in sharp]

    def pair(fa, fb):
        """Similarity taking picture a onto picture b (pixel units of the 320 px frames); None if they do not match."""
        (ka, da), (kb, db) = fa, fb
        if da is None or db is None or len(ka) < 8 or len(kb) < 8:
            return None
        m = [x for x, y in (p for p in matcher.knnMatch(da, db, k=2) if len(p) == 2) if x.distance < 0.75 * y.distance]
        if len(m) < 8:
            return None
        pa = np.float32([ka[x.queryIdx].pt for x in m]) / f
        pb = np.float32([kb[x.trainIdx].pt for x in m]) / f
        est, inl = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=1.5)
        return est if est is not None and inl is not None and inl.sum() >= 6 else None

    def residual(M, g):
        warped = cv2.warpAffine(frames[0], M, (w, h), flags=cv2.INTER_LINEAR)
        valid = cv2.warpAffine(mask, M, (w, h), flags=cv2.INTER_NEAREST) & mask
        valid = cv2.erode(valid, np.ones((5, 5), np.uint8)) > 0
        if valid.sum() < 0.3 * w * h:
            return 1.0, 255.0, None
        d = cv2.absdiff(warped, g)
        moved = ((d > MOVED) & valid).astype(np.uint8)
        moved = cv2.morphologyEx(moved, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        return float(moved.sum()) / float(valid.sum()), float(d[valid].mean()), moved

    def refined(M, i):
        """Frame 0 moved by M, matched again against frame i: the leftover similarity, composed in."""
        for _ in range(2):
            S = np.diag([f, f, 1.0])
            Mb = (S @ np.vstack([M, [0, 0, 1]]) @ np.linalg.inv(S))[:2]
            moved0 = cv2.warpAffine(sharp[0], Mb, sharp[i].shape[::-1], flags=cv2.INTER_LINEAR)
            st = pair(sift.detectAndCompute(moved0, cv2.warpAffine(big, Mb, big.shape[::-1], flags=cv2.INTER_NEAREST)), feats[i])
            if st is None:
                break
            M = (np.vstack([st, [0, 0, 1]]) @ np.vstack([M, [0, 0, 1]]))[:2]
        return M

    def ecc(M, g):
        try:
            crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5)
            _, R = cv2.findTransformECC(frames[0], g, M.astype(np.float32), cv2.MOTION_AFFINE, crit, mask, 5)
        except cv2.error:
            return None
        R = R.astype(np.float64)
        sx, sy = np.hypot(R[0, 0], R[1, 0]), np.hypot(R[0, 1], R[1, 1])
        return R if abs(sx - sy) < 0.01 * sx and abs(R[0, 1]) < 0.01 * sx else None

    boxes, shares, moved_box, ident = [[0.0, 0.0, 1.0, 1.0]], [], None, True
    M = np.array([[1, 0, 0], [0, 1, 0]], np.float64)
    for i, g in enumerate(frames[1:], 1):
        st = pair(feats[i - 1], feats[i])  # neighbours differ little, so they match even on flat pictures
        if st is not None:  # frame 0 -> frame i = (i-1 -> i) after (0 -> i-1)
            M = (np.vstack([st, [0, 0, 1]]) @ np.vstack([M, [0, 0, 1]]))[:2]
        # the chained estimate drifts a little per step: refine it two ways and keep whichever lines up best
        cands = [c for c in (M, refined(M, i), ecc(M, g)) if c is not None]
        share, _, moved, M = min(((*residual(c, g), c) for c in cands), key=lambda x: (x[0] > STILL_SHARE, x[1]))
        s = float(np.hypot(M[0, 0], M[1, 0]))
        if abs(np.degrees(np.arctan2(M[1, 0], M[0, 0]))) > 0.5 or not 0.5 < s < 2.0 or moved is None:
            return {"kind": "full"}
        if abs(s - 1) > 0.004 or abs(M[0, 2]) > 0.8 or abs(M[1, 2]) > 0.8:
            ident = False
        shares.append(share)
        ys, xs = np.nonzero(moved)
        if len(xs):
            b = [xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h]
            moved_box = b if moved_box is None else [min(moved_box[0], b[0]), min(moved_box[1], b[1]),
                                                       max(moved_box[2], b[2]), max(moved_box[3], b[3])]
        # where frame i's view lies in frame 0: the inverse similarity applied to frame i's corners
        Mi = cv2.invertAffineTransform(M)
        (ax, ay), (bx, by) = (Mi @ np.array([0, 0, 1.0]), Mi @ np.array([w, h, 1.0]))
        boxes.append([ax / w, ay / h, bx / w, by / h])
    top = max(shares)
    if top >= PARTIAL_SHARE:
        return {"kind": "full", "moved": round(top, 3)}
    if top >= STILL_SHARE:
        if not ident:  # a part moves while the camera pushes: needs the video model
            return {"kind": "full", "moved": round(top, 3)}
        return {"kind": "partial", "moved": round(top, 3), "where": [round(v, 3) for v in moved_box or [0, 0, 1, 1]]}
    if ident:
        return {"kind": "still", "moved": round(top, 3)}
    inside = lambda bs: all(-0.02 <= b[0] and -0.02 <= b[1] and b[2] <= 1.02 and b[3] <= 1.02 for b in bs)
    base = "first"
    if not inside(boxes):  # zooming out / panning back: express everything in the last frame
        L = boxes[-1]
        lw, lh = L[2] - L[0], L[3] - L[1]
        boxes = [[(b[0] - L[0]) / lw, (b[1] - L[1]) / lh, (b[2] - L[0]) / lw, (b[3] - L[1]) / lh] for b in boxes]
        base = "last"
        if not inside(boxes):  # neither end shows all of it: a pan across a picture wider than any frame
            return {"kind": "full", "moved": round(top, 3)}
    clip01 = lambda v: min(max(v, 0.0), 1.0)
    path = [[t, *[round(float(clip01(v)), 4) for v in b]] for t, b in zip(times, boxes)]
    path = [[0.0, *path[0][1:]], *path, [round(dur, 3), *path[-1][1:]]]
    return {"kind": "pan", "base": base, "path": path, "moved": round(top, 3)}


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
