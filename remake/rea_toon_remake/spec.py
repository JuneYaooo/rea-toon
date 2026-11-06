"""remake.yaml: what to change. Everything not mentioned stays as in the reference."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CastSwap:
    id: str                    # character id in story.json
    name: str                  # new character name, e.g. 孙悟空
    look: str = ""             # face / body / hair of the new character
    costume: str = "keep"      # keep = wear the original role's outfit; or free text
    image: str | None = None   # optional reference picture of the new character
    voice: str = "keep"        # keep the original voice track (only option for now)
    kind: str = "person"       # person | object (a product swap: no face, never "too small to recognise")
    original: str = ""         # kind=object only: what the object looks like in the reference (it is added to every shot)
    fx_color: str = ""         # the colour of this role's effects (剑气、灵光) in the remake, e.g. "金色"; "" = as in the original


@dataclass
class RemakeSpec:
    name: str
    style: str | None = None           # global restyle, e.g. "3D 皮克斯动画电影风格"; None = keep look
    cast: dict[str, CastSwap] = field(default_factory=dict)
    image_channel: str = "cyber"       # gpt-image-2 channel: cyber | krill | juling | openai
    video_engine: str = "h3"           # h3 (autodl.art API) | h3local (self-hosted H3 ComfyUI) | seedance
    frames: str = "first_last"         # first_last | first
    lipsync: bool = True               # H3: dialogue of swapped roles is driven by the original voice (mouth on the words)
    motion_ref: bool = True            # h3local: shots of 2-15s use the original shot as Ref2VA motion reference
    no_motion_ref: str = ""            # shots that skip the motion reference, e.g. "19,21-23" (original people leaked back in)
    anchors: dict = field(default_factory=dict)  # extra keyframes inside long ref_video shots: {shot: [seconds into shot]}
    frame_notes: dict = field(default_factory=dict)  # per-keyframe instructions: {shot: {first|last|a1..: "穿第二套"}}
    scene_ref: bool = True             # first frames also see the scene's anchor shot (same faces / looks within a scene)
    aigc_label: str = "AI生成"          # explicit AI-content label burned into the final cut (标识办法, 2025-09); "" = none
    kenburns: bool = True              # still pictures held / pushed / panned in the original (动态漫画) are rendered from
                                       # the new keyframe locally, no video model (the breakdown's per-shot `motion`)
    expression_sheet: bool = False     # draw an expression sheet per swapped character and pass it with the keyframes
    subtitles: str = "reburn"          # reburn | none
    shots: str = "all"                 # "all" or "1-5,28,30"
    regen: str = "affected"            # affected = only shots with swapped roles (or all when style is set); all
    workers: int = 4                   # shots in flight; on one self-hosted GPU 3 keeps its queue full
    frame_workers: int = 4             # parallel keyframe edits (image API)
    notes: str = ""
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "RemakeSpec":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cast = {}
        for cid, c in (raw.pop("cast", None) or {}).items():
            c = c if isinstance(c, dict) else {"name": str(c)}
            cast[cid] = CastSwap(id=cid, **{k: v for k, v in c.items() if k in CastSwap.__dataclass_fields__})
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        if known.get("anchors"):
            known["anchors"] = {int(k): sorted(float(t) for t in (v if isinstance(v, list) else [v]))
                                for k, v in known["anchors"].items()}
        if known.get("frame_notes"):
            known["frame_notes"] = {int(k): dict(v) for k, v in known["frame_notes"].items()}
        spec = cls(cast=cast, path=path, **known)
        if not spec.name:
            spec.name = path.parent.name
        return spec


def parse_shots(expr: str | None, total: int) -> list[int]:
    if not expr or str(expr).strip() == "all":
        return list(range(1, total + 1))
    out: set[int] = set()
    for part in str(expr).replace("，", ",").split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return sorted(i for i in out if 1 <= i <= total)
