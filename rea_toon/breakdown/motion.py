"""How the picture moves, measured locally (no model): freeze frames, still / pushed / partly moving pictures,
flash and fade transitions."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .. import layout, media
from ..memo import Memo


MOTION_CN = {"still": "静图", "pan": "静图推拉", "partial": "局部动", "full": ""}


def freezes(story: dict, work: Path) -> int:
    """Freeze frames (定格) inside shots: a generated clip never holds still, so assembly freezes it there."""
    n = 0
    with Memo(work, "freezes") as memo:  # kept between runs: the agent backend restarts the breakdown every round
        found = [memo.get([s["start"], s["duration"]], lambda s=s: media.still_runs(layout.source(work), s["start"], s["duration"]))
                 for s in story["shots"]]
    for s, runs in zip(story["shots"], found):
        if runs:
            s["freeze"] = [list(r) for r in runs]
            n += 1
        else:
            s.pop("freeze", None)
    return n


def motions(story: dict, work: Path) -> dict:
    """How each shot's picture moves (local, no model): a still picture held or pushed / panned (动态漫画 Ken Burns)
    is rendered from the new keyframe directly; a picture where only a small part moves gets told so."""
    with Memo(work, "motion") as memo:  # kept between runs, like freezes
        for s in story["shots"]:
            masks = [x["box"] for x in s.get("subtitles") or [] if x.get("box")]
            masks += [t["box"] for t in s.get("screen_text") or [] if t.get("kind") in ("overlay", "aigc", "watermark")]
            s["motion"] = memo.get([s["start"], s["duration"], masks],
                                   lambda: media.image_motion(layout.source(work), s["start"], s["duration"], masks))
    return dict(Counter(s["motion"]["kind"] for s in story["shots"]))


def transition_seen(work: Path, shot: dict) -> str | None:
    """Flash / fade-in seen in the pixels: the first frame far brighter (white flash) or darker than the middle."""
    try:
        first, mid = (media.mean_luma(work / shot["frames"][k]) for k in ("first", "mid"))
    except Exception:
        return None
    if first > 200 and first > mid + 50:
        return "white_flash"
    if first < 0.5 * mid:
        return "fade"
    return None
