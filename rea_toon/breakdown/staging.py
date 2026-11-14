"""Camera angle, where each face points and who looks at whom; effects, manga marks and split panels (same call)."""

from __future__ import annotations

import logging
from pathlib import Path

from ..prompts import STAGING_PROMPT
from ..vlm import Pool, VLM

LOG = logging.getLogger(__name__)


ANGLES = ("eye", "high", "low", "dutch", "pov", "ots")
ANGLE_CN = {"eye": "平视", "high": "俯拍", "low": "仰拍", "dutch": "斜角", "pov": "主观视角", "ots": "过肩"}
FACING_CN = {"left": "画面左侧", "right": "画面右侧", "camera": "镜头", "away": "背对镜头", "down": "下方"}


def staging_from(r: dict, s: dict, story: dict) -> dict | None:
    """A model's staging answer for one shot, cleaned: only known ids, known angles / facings."""
    people = [c for c in (s.get("analysis") or {}).get("characters") or [] if c.get("id") and c.get("visibility") not in ("tiny",)]
    ids = {c["id"] for c in people} | {c["id"] for c in story["characters"]}
    ppl = {k: {"facing": v.get("facing") if v.get("facing") in FACING_CN else "",
               "looking_at": str(v.get("looking_at") or "")}
           for k, v in (r.get("people") or {}).items() if k in ids and isinstance(v, dict)}
    fx = [{"what": str(e.get("what") or ""), "color": str(e.get("color") or ""),
           "from": str(e.get("from") or "") if str(e.get("from") or "") in ids else ""}
          for e in r.get("effects") or [] if isinstance(e, dict) and e.get("what")]
    try:
        panels = max(1, min(9, int(r.get("panels") or 1)))
    except (TypeError, ValueError):
        panels = 1
    st = {"angle": r.get("angle") if r.get("angle") in ANGLES else "eye", "ots_of": r.get("ots_of") or "",
          "people": ppl, "power": str(r.get("power") or "")}
    st.update({k: v for k, v in (("effects", fx), ("marks", [str(x) for x in r.get("marks") or [] if x]),
                                 ("panels", panels if panels > 1 else 0)) if v})
    return st if people or len(st) > 4 else None


def staging(story: dict, work: Path, vlm: VLM, workers: int = 6) -> int:
    """Per shot: camera angle, which way each face points and who looks at whom; in the same call the things a comic
    drama draws on top of the people: effects (剑气、灵光、法阵 and their colour / source), manga marks, split panels."""

    def one(s: dict) -> dict | None:
        a = s.get("analysis") or {}
        people = [c for c in a.get("characters") or [] if c.get("id") and c.get("visibility") not in ("tiny",)]
        desc = "；".join(f"{c['id']}（{c.get('where', '')}）" for c in people) or "（没有看得清的角色）"
        try:
            r = vlm.ask_json(STAGING_PROMPT.format(index=s["index"], size=a.get("shot_size", ""), people=desc),
                             [work / s["frames"]["mid"]], max_tokens=1536, timeout=120)
        except Exception as exc:
            LOG.warning("第 %s 镜机位/视线拆解失败：%s", s["index"], exc)
            return None
        return staging_from(r, s, story)

    todo = [s for s in story["shots"] if not (s.get("analysis") or {}).get("batched")]  # fast mode answered these already
    with Pool(workers) as ex:
        for s, st in zip(todo, ex.map(one, todo)):
            if st:
                s["staging"] = st
            else:
                s.pop("staging", None)
    return sum(1 for s in story["shots"] if s.get("staging"))
