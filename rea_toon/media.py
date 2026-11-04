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


def cut_audio(src: Path, start: float, dur: float, out: Path, pad_to: float | None = None) -> Path:
    """Cut [start, start+dur]; with pad_to, append silence so the file lasts exactly pad_to seconds."""
    out.parent.mkdir(parents=True, exist_ok=True)
    af = ["-af", f"apad=whole_dur={pad_to:.3f}"] if pad_to and pad_to > dur else []
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


def ref_clip(source: Path, voice: Path, start: float, dur: float, subtitles: list[dict], out: Path,
             size: tuple[int, int] = (1024, 576), fps: int = 24, hide_faces: bool = True) -> Path:
    """The original shot as a motion reference: burned subtitles blurred away, 24 fps, the shot's voice as audio.
    hide_faces blurs the large (lead) faces: otherwise Ref2VA copies the original actors' identity."""
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
    vf = ",".join([f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},setsar=1", *blurs])
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(source),
         "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(voice), "-map", "0:v:0", "-map", "1:a:0",
         "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-shortest", str(out)])
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
    for p in (out, flag):
        if p.is_file() and p.stat().st_mtime >= video.stat().st_mtime:
            return out if p == out else video
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
    return out
