"""Style bible and scenes: the visual rules of the whole film, continuous scenes, and each person's look per scene."""

from __future__ import annotations

import logging
from pathlib import Path

from ..prompts import SCENE_LOOKS_PROMPT, SCENES_PROMPT
from ..story import LOOK_PARTS, cast_lines, look_split, shot_table
from ..vlm import Pool, VLM

LOG = logging.getLogger(__name__)


def _fix_scenes(scenes: list[dict], n: int) -> list[dict]:
    """Scenes as given by the model, made into ordered, gap-free, non-overlapping ranges over shots 1..n."""
    out, nxt = [], 1
    for sc in sorted((s for s in scenes if isinstance(s.get("shots"), list) and len(s["shots"]) == 2),
                     key=lambda s: int(s["shots"][0])):
        a, b = max(int(sc["shots"][0]), nxt), min(int(sc["shots"][1]), n)
        if b < a:
            continue
        if a > nxt and out:  # a gap: the previous scene runs on
            out[-1]["shots"][1] = a - 1
        elif a > nxt:
            a = 1
        out.append({**sc, "shots": [a, b]})
        nxt = b + 1
    if not out:
        return [{"id": "S1", "shots": [1, n], "location": "", "time": "", "present": []}]
    out[-1]["shots"][1] = n
    for k, sc in enumerate(out, 1):
        sc["id"] = f"S{k}"
    return out


def _scene_frames(story: dict, work: Path, sc: dict, k: int = 6) -> list[tuple[int, Path]]:
    """Frames that show this scene's people best: closest framing, spread over the scene."""
    order = ("特写", "近景", "中景", "全景")
    shots = [s for s in story["shots"] if sc["shots"][0] <= s["index"] <= sc["shots"][1]]
    peopled = [s for s in shots if (s.get("analysis") or {}).get("characters")] or shots
    if len(peopled) <= k:
        return [(s["index"], work / s["frames"]["mid"]) for s in peopled]
    rank = lambda s: next((i for i, w in enumerate(order) if w in str((s.get("analysis") or {}).get("shot_size", ""))), 9)
    step = len(peopled) / k
    picks = []
    for j in range(k):  # one per slice of the scene, the closest shot of that slice
        part = peopled[int(j * step):int((j + 1) * step)] or peopled[-1:]
        picks.append(min(part, key=rank))
    return [(s["index"], work / s["frames"]["mid"]) for s in picks]


def scene_looks(story: dict, work: Path, sc: dict, vlm: VLM) -> dict:
    by_id = {c["id"]: c for c in story["characters"]}
    shots = [s for s in story["shots"] if sc["shots"][0] <= s["index"] <= sc["shots"][1]]
    here = list(dict.fromkeys(c.get("id") for s in shots for c in (s.get("analysis") or {}).get("characters") or []))
    here = [c for c in dict.fromkeys([*(sc.get("present") or []), *here]) if c in by_id]
    if not here:
        return {"looks": {}, "changes": []}
    frames = _scene_frames(story, work, sc)
    pics = "，".join(f"图{n} 是第 {i} 镜" for n, (i, _) in enumerate(frames, 1))
    cast = "\n".join(f"- {c}：{by_id[c].get('role', '')}（{str(by_id[c].get('appearance', ''))[:80]}）" for c in here)
    prompt = SCENE_LOOKS_PROMPT.format(sid=sc["id"], a=sc["shots"][0], b=sc["shots"][1], location=sc.get("location", ""),
                                       time=sc.get("time", ""), pics=pics, cast=cast, table=shot_table(story, shots))
    try:
        r = vlm.ask_json(prompt, [p for _, p in frames], max_tokens=4096, timeout=300)
    except Exception as exc:  # leave the scene without looks: lint reports it
        LOG.warning("第 %s 场造型拆解失败：%s", sc["id"], exc)
        return {"looks": {}, "changes": [], "error": str(exc)}
    looks = {k: look_split(v) for k, v in (r.get("looks") or {}).items() if k in by_id and v}
    changes = [{"shot": int(c["shot"]), "who": c["who"], **look_split(c if any(c.get(k) for k in LOOK_PARTS) else c.get("look"))}
               for c in r.get("changes") or [] if c.get("who") in by_id and str(c.get("shot", "")).isdigit()
               and sc["shots"][0] <= int(c["shot"]) <= sc["shots"][1]]
    return {"looks": looks, "changes": changes}


def scenes(story: dict, work: Path, vlm: VLM, sheets: list[Path], workers: int = 6) -> int:
    """Style bible + scenes over the contact sheets, then each scene's looks. Starts story["breakdown"] afresh."""
    r = vlm.ask_json(SCENES_PROMPT.format(summary=story.get("summary", ""), cast=cast_lines(story), table=shot_table(story)),
                     sheets, max_tokens=12000, timeout=400)
    found = _fix_scenes(r.get("scenes") or [], len(story["shots"]))
    story["breakdown"] = {"version": 1, "style_bible": r.get("style_bible") or {}, "scenes": found}
    LOG.info("深度拆解：%d 场的造型 …", len(found))
    with Pool(workers) as ex:
        for sc, res in zip(found, ex.map(lambda sc: scene_looks(story, work, sc, vlm), found)):
            sc.update(res)
    return len(found)
