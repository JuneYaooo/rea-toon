"""Bring a reference video into a work dir. Only a video the user already has on disk: nothing is downloaded.
Anything that is not an .mp4 is rewrapped (or, when its streams do not fit the container, re-encoded) into one: the
pages play source.mp4 in a browser.

A work dir holds one video for good: running the same command again (the agent does, after every round of answers)
does not copy or re-encode it again, and another video is refused, because the cuts, subtitles and frames cached next
to it belong to the first one."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import layout
from .media import probe, run


def _to_mp4(src: Path, dst: Path) -> None:
    head = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?"]
    try:
        run(head + ["-c", "copy", "-movflags", "+faststart", str(dst)])
    except RuntimeError:  # e.g. VP9 / Opus from a .webm
        run(head + ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(dst)])


def _same_video(meta: dict, local: Path) -> bool:
    """The video `meta` (source.json) was made from is this file: same path, and the same size when it was noted."""
    was = Path(meta.get("path") or meta.get("input") or "").expanduser()
    try:
        same = was.resolve() == local.resolve()
    except OSError:
        return False
    return same and meta.get("bytes") in (None, local.stat().st_size)


LONG = 20 * 60  # s: one short-drama episode is 1-10 minutes; a file this long is several episodes in one


def ingest(source: str, work: Path, title: str | None = None, long: bool = False) -> dict:
    """title: what to call the video; the file's name when not given (an existing title stays unless a new one is given).
    A file longer than LONG is refused unless `long`: a compilation broken down as one episode has no episodes,
    no context from one to the next and no series pages, and takes thousands of shots in one go."""
    local = Path(source).expanduser()
    if not local.is_file():
        raise RuntimeError(f"只支持已经准备好的本地视频文件，不支持链接：{source}")
    dst, info = layout.source(work), layout.data(work, "source.json")
    if dst.is_file() and info.is_file():
        meta = json.loads(info.read_text(encoding="utf-8"))
        if local.resolve() == dst.resolve() or _same_video(meta, local):
            if title and title != meta.get("title"):
                meta["title"] = title
                layout.put(info, json.dumps(meta, ensure_ascii=False, indent=2))
            return meta
        raise RuntimeError(f"{work} 里已经导入了另一条视频（{meta.get('input')}）：一个目录只拆一条视频，换一个目录，"
                           "或者先删掉这个目录再导入")
    if not long and (minutes := probe(local)["duration"] / 60) > LONG / 60:
        raise RuntimeError(f"{local.name} 有 {minutes:.0f} 分钟，像是好几集的合集：先切成一集一个文件，再用 rea-toon series 按整部剧拆"
                           "（怎么切见 skills/rea-toon/SKILL.md「合集」）；确实只是一集，加 --long")
    work.mkdir(parents=True, exist_ok=True)
    if local.resolve() != dst.resolve():
        part = dst.with_name("source.part.mp4")  # a copy cut short never passes for the video
        if local.suffix.lower() == ".mp4":
            shutil.copy2(local, part)
        else:
            _to_mp4(local, part)
        part.replace(dst)
    meta: dict = {"input": source, "path": str(local.resolve()), "bytes": local.stat().st_size,
                  "title": title or local.stem, **probe(dst)}
    layout.put(info, json.dumps(meta, ensure_ascii=False, indent=2))
    return meta
