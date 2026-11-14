"""The dramatic spine and per-shot key: hook, reversals, reveals, payoff, cliffhanger; per shot its role, what the
audience feels (the emotion ledger, emotion.py), priority, function, the 1-3 things the shot cannot lose, effects
and how the shot is entered; and the information gaps the episode plays on (who knows what the others do not)."""

from __future__ import annotations

from pathlib import Path

from ..prompts import BEATS_PROMPT
from ..story import FEELS, PRIORITY_CN, ROLES, TRANSITIONS, cast_lines, look_line, prior_text, shot_table
from ..vlm import VLM
from .motion import transition_seen


def beats(story: dict, work: Path, vlm: VLM, sheets: list[Path]) -> int:
    """One call over the whole episode (scenes and looks included): the beats, and every shot's `key`."""
    bd = story["breakdown"]
    scene_text = "\n".join(f"{sc['id']} 镜{sc['shots'][0]}-{sc['shots'][1]} {sc.get('location', '')} {sc.get('time', '')}："
                           + "；".join(f"{k}={look_line(v)}" for k, v in (sc.get("looks") or {}).items()) for sc in bd["scenes"])
    r = vlm.ask_json(BEATS_PROMPT.format(summary=story.get("summary", ""), prior=prior_text(story), cast=cast_lines(story),
                                         scenes=scene_text, table=shot_table(story, emotion=True)),
                     sheets, max_tokens=28000, timeout=400)
    feel = lambda x: x.get("feel") if x.get("feel") in FEELS else ""
    bd["beats"] = [{**b, "feel": feel(b)} for b in r.get("beats") or [] if b.get("type")]
    bd["info_gaps"] = [g for g in r.get("info_gaps") or [] if isinstance(g, dict) and g.get("what")]
    given = {int(x["i"]): x for x in r.get("shots") or [] if str(x.get("i", "")).isdigit()}
    for s in story["shots"]:
        g = given.get(s["index"], {})
        key = {"role": g.get("role") if g.get("role") in ROLES else "normal", "feel": feel(g),
               "priority": g.get("priority") if g.get("priority") in PRIORITY_CN else "normal",
               "function": str(g.get("function") or "").strip(),
               "keep": [str(x) for x in g.get("keep") or [] if x][:3],
               "fx": [str(x) for x in g.get("fx") or [] if x],
               "transition_in": g.get("transition_in") if g.get("transition_in") in TRANSITIONS else "cut"}
        seen = transition_seen(work, s)  # the pixels win over the model on flash / fade
        if seen and key["transition_in"] == "cut":
            key["transition_in"] = seen
        if not g:
            key["missing"] = True  # lint: the model skipped this shot
        s["key"] = key
    return len(bd["beats"])
