"""ffmpeg / OpenCV / PIL helpers of the analysis: probe, frames, contact sheets, sound onsets, held frames, how a picture moves."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import layout

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



def font_path() -> str | None:
    return next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)



def mean_luma(p: Path) -> float:
    import cv2
    return float(cv2.imread(str(p), cv2.IMREAD_GRAYSCALE).mean())



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


# ---- contact sheets: several shots in one picture for one model call ----

def contact_sheets(shots: list[dict], work: Path, per_sheet: int = 12, cols: int | None = None) -> list[Path]:
    """The middle frame of every shot, numbered, twelve to a picture. A tile keeps the shape of the video: a vertical
    drama squeezed into wide tiles spends the same pixels on faces three times too wide."""
    font = ImageFont.truetype(font_path(), 34) if font_path() else ImageFont.load_default()
    paths = []
    w0, h0 = Image.open(work / shots[0]["frames"]["mid"]).size if shots else (16, 9)
    tw, th = (270, 480) if h0 > w0 else (480, 270)
    cols = cols or (6 if h0 > w0 else 4)
    for k in range(0, len(shots), per_sheet):
        group = shots[k:k + per_sheet]
        rows = (len(group) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw, rows * th), "black")
        for i, s in enumerate(group):
            im = Image.open(work / s["frames"]["mid"]).convert("RGB").resize((tw, th))
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, 64, 42], fill="white")
            d.text((6, 2), str(s["index"]), fill="black", font=font)
            sheet.paste(im, ((i % cols) * tw, (i // cols) * th))
        p = layout.analysis(work, f"contact_{k // per_sheet + 1}.jpg")
        p.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(p, quality=88)
        paths.append(p)
    return paths


def face_sheet(faces: list[tuple[str, Path]], out: Path, cols: int = 4) -> Path | None:
    """One picture of who is who: a frame of each known character with the id written on it. Later episodes of a
    series get it next to their own frames, so a person is recognised by the face, not by a description."""
    faces = [(cid, p) for cid, p in faces if Path(p).is_file()]
    if not faces:
        return None
    font = ImageFont.truetype(font_path(), 26) if font_path() else ImageFont.load_default()
    tw, th = 360, 480
    sheet = Image.new("RGB", (min(cols, len(faces)) * tw, ((len(faces) + cols - 1) // cols) * th), "black")
    for i, (cid, p) in enumerate(faces):
        im = Image.open(p).convert("RGB")
        k = max(tw / im.width, th / im.height)
        im = im.resize((round(im.width * k), round(im.height * k)))
        im = im.crop(((im.width - tw) // 2, (im.height - th) // 2, (im.width - tw) // 2 + tw, (im.height - th) // 2 + th))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, tw, 38], fill="white")
        d.text((6, 3), cid, fill="black", font=font)
        sheet.paste(im, ((i % cols) * tw, (i // cols) * th))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    return out


def line_strip(work: Path, s: dict, line: dict, label: str, h: int = 360) -> Path:
    """Three frames of a shot taken while one subtitle line is up (its start | middle | end inside the shot), with a
    label in the corner: who is talking is seen when the line is said, not at the shot's first / middle / last frame.
    Falls back to the shot's own strip when the video is not there."""
    src = layout.source(work)
    a, b = max(s["start"], line["start"]), min(s["end"], line["end"])
    if not src.is_file() or b - a <= 0.05:
        return shot_strip(work, s)
    out = layout.analysis(work, "strips") / f"line_{s['id']}_{round(line['start'] * 100)}_{label.replace('/', '-')}.jpg"
    if out.is_file():
        return out
    ims = []
    if line["end"] <= s["end"]:
        b = max(a + 0.1, b - 0.2)  # the track is read every 0.2s: the last fifth of a second may be the next line already
    for k, f in enumerate((0.15, 0.5, 0.85)):
        p = out.with_name(f"{out.stem}_{k}.jpg")
        frame_at(src, a + (b - a) * f, p)
        ims.append(Image.open(p).convert("RGB"))
        p.unlink()
    w = round(ims[0].width * h / ims[0].height)
    strip = Image.new("RGB", (3 * w + 2 * 6, h), "white")
    for i, im in enumerate(ims):
        strip.paste(im.resize((w, h)), (i * (w + 6), 0))
    font = ImageFont.truetype(font_path(), 26) if font_path() else ImageFont.load_default()
    d = ImageDraw.Draw(strip)
    d.rectangle([0, 0, 14 + 15 * len(label), 36], fill="white")
    d.text((6, 3), label, fill="black", font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    strip.save(out, quality=85)
    return out


def shot_strip(work: Path, s: dict, h: int = 300) -> Path:
    """First | middle | last frame of a shot side by side, its number in the corner: one image per shot for fast mode."""
    p = layout.analysis(work, "strips", f"{s['id']}.jpg")
    if p.is_file():
        return p
    ims = [Image.open(work / s["frames"][k]).convert("RGB") for k in ("first", "mid", "last")]
    w = round(ims[0].width * h / ims[0].height)
    strip = Image.new("RGB", (3 * w + 2 * 6, h), "white")
    for i, im in enumerate(ims):
        strip.paste(im.resize((w, h)), (i * (w + 6), 0))
    font = ImageFont.truetype(font_path(), 30) if font_path() else ImageFont.load_default()
    d = ImageDraw.Draw(strip)
    d.rectangle([0, 0, 64, 38], fill="white")
    d.text((6, 2), str(s["index"]), fill="black", font=font)
    p.parent.mkdir(parents=True, exist_ok=True)
    strip.save(p, quality=85)
    return p


# ---- faces ----

YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"


def yunet() -> Path:
    """OpenCV's YuNet face detector (~230 KB), downloaded once."""
    p = Path.home() / ".cache" / "rea-toon" / "face_detection_yunet_2023mar.onnx"
    if not p.is_file():
        import requests
        p.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(YUNET_URL, timeout=120)
        r.raise_for_status()
        p.write_bytes(r.content)
    return p


def faces(image: Path, min_frac: float = 0.04) -> list[tuple[float, float, float, float, float]]:
    """Faces in a picture as (x, y, w, h, score) in pixels, biggest first; tiny background faces left out."""
    import cv2
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)  # YuNet warns about backends on every call
    except AttributeError:
        pass
    img = cv2.imread(str(image))
    if img is None:
        return []
    h, w = img.shape[:2]
    det = cv2.FaceDetectorYN.create(str(yunet()), "", (w, h), 0.6, 0.3, 20)
    _, found = det.detect(img)
    out = [tuple(float(v) for v in (*r[:4], r[-1])) for r in (found if found is not None else []) if r[2] >= w * min_frac]
    return sorted(out, key=lambda f: -f[2] * f[3])
