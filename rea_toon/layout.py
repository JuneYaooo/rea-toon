"""Where everything in a work directory lives. The only module that names a path inside one.

One video, or one episode of a series:

    index.html     for people: the one page to open                                              page.site
    VIDEO.md       for models and downstream tools: the entry of the breakdown package           videomd.py
    story.json     the source: the only file edited by hand, and what `lock` approves            story.py
    source.mp4     the reference video                                                            ingest.py
    video/         the package: timeline.md, scenes.md, content.md, cast.md, x-report.md, shots/NN.md, transcript.vtt
    pages/         what index.html shows: report.html, script.html, storyboard.html, breakdown.html; script.md, *.pdf
    frames/        first / mid / last frame of each shot (story.json names them relative to the work dir)
    characters/    face and look of each character, voice samples, thumbnails of the shot script
    data/          for programs: source.json, report.json, subtitles.json / .srt, *.prev.json (kept before a redo)
    analysis/      working files: model answers (vlm_cache/), questions for the agent (agent/), contact sheets, strips,
                   slow local measurements kept between runs (local/, memo.py)
    decomp/        what rea-video measured: cuts, stems

A series: index.html, SERIES.md (for models: the conclusions across episodes and where each episode's package is),
series.json (the source across episodes), pages/ (series.html / .md, script, storyboard), characters/, analysis/, and
one directory like the above per episode: ep01/ ep02/ … ep100/

Three things sit at the top because three readers start there: a person (index.html), a model (VIDEO.md), an editor
(story.json). Everything else is in a folder named after who uses it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SITE = "index.html"
ENTRY = "VIDEO.md"
PACKAGE = "video"
PAGES = "pages"


# ---- the top: what a reader starts from ----

def story(work: Path) -> Path:
    return work / "story.json"


def series(root: Path) -> Path:
    return root / "series.json"


def source(work: Path) -> Path:
    return work / "source.mp4"


def site(root: Path) -> Path:
    return root / SITE


def entry(work: Path) -> Path:
    return work / ENTRY


def series_entry(root: Path) -> Path:
    return root / "SERIES.md"


# ---- folders ----

def package(work: Path, *parts: str) -> Path:
    return work.joinpath(PACKAGE, *parts)


def page(root: Path, name: str) -> Path:
    """A page index.html shows, or a file it offers: report.html, script.pdf …"""
    return root / PAGES / name


def data(work: Path, name: str) -> Path:
    """Data a program reads back: source.json, report.json, subtitles.json …"""
    return work / "data" / name


def prev(work: Path, name: str) -> Path:
    """What `name` (story.json, subtitles.json) was before a redo replaced it."""
    stem, ext = os.path.splitext(name)
    return work / "data" / f"{stem}.prev{ext}"


def frames(work: Path, *parts: str) -> Path:
    return work.joinpath("frames", *parts)


def characters(root: Path, *parts: str) -> Path:
    return root.joinpath("characters", *parts)


def analysis(work: Path, *parts: str) -> Path:
    return work.joinpath("analysis", *parts)


def measured(work: Path, name: str) -> Path:
    """Slow local measurements (OCR of frames, pixel motion) kept between runs: memo.py."""
    return analysis(work, "local", f"{name}.json")


def decomp(work: Path, *parts: str) -> Path:
    return work.joinpath("decomp", *parts)


# ---- a series and its episodes ----

def is_episode_name(name: str) -> bool:
    return bool(re.fullmatch(r"ep\d+", name))


def episode_no(work: Path) -> int:
    """The episode number of a directory named epNN, else 0."""
    return int(work.name[2:]) if is_episode_name(work.name) else 0


def episodes(root: Path) -> list[Path]:
    """The episode directories of a series, in episode order (ep100 after ep99, not after ep10)."""
    return sorted((d for d in root.glob("ep*") if is_episode_name(d.name) and d.is_dir()), key=episode_no)


def series_of(work: Path) -> Path | None:
    """The series an episode directory belongs to: named epNN, next to a series.json."""
    return work.parent if is_episode_name(work.name) and series(work.parent).is_file() else None


# ---- writing, and pointing from one file to another ----

def put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def rel(target: Path, start: Path) -> str:
    """`target` as a file in the folder `start` reaches it (a URL path: forward slashes)."""
    return os.path.relpath(target, start).replace(os.sep, "/")


def from_page(root: Path, target: Path | str) -> str:
    """How a page of `root` (in pages/) reaches a file given relative to root, or by its path."""
    return rel(root / target, root / PAGES)
