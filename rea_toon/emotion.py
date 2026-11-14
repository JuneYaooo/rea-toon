"""The emotion ledger of one episode, counted from the per-shot breakdown (no model call).

A short drama holds people by emotion, not by act structure: it presses the audience (憋: the lead is humiliated,
misread, held down; 悬: a secret about to slip) and then lets go (爽, 甜, 笑 ...). The beats list is the model's
summary of the spine, five to eight entries however dense the episode is, so nothing is measured from it; the ledger
reads every shot's `key` (its `feel`, and its role for reaction cutaways):

- nodes      the moments the audience stays for: runs of shots hitting the same emotion
- gaps       the quiet time between them (before the first one, average, the stretches with none)
- pressure   each 爽 with the 憋 it lets go, how long it was held, and what is still owed when the episode ends
"""

from __future__ import annotations

from collections import Counter

HITS = ("爽", "虐", "甜", "笑", "惊", "燃")  # the feels that are a hit; 憋 and 悬 are the wait for one
BRIDGE = 3.0  # seconds of reaction shots a node may run through


def feel_of(shot: dict) -> str:
    return str((shot.get("key") or {}).get("feel") or "")


def has_feels(story: dict) -> bool:
    """Whether the breakdown judged what each shot does to the audience (every breakdown does; one made before the
    ledger did not, and has to be redone: nothing is counted from dramatic roles in its place)."""
    return any(feel_of(s) for s in story.get("shots") or [])


def _hit(shot: dict) -> str:
    """What this shot hits the audience with: its feel, when it is one that lands (a reveal the breakdown gave no
    feel was judged not to land)."""
    return feel_of(shot) if feel_of(shot) in HITS else ""


def nodes(story: dict) -> list[dict]:
    """Emotional nodes in order: {t, end, shots, feel, what}. Consecutive shots with the same hit are one node, and
    a cutaway to someone's reaction does not end it."""
    out: list[dict] = []
    cur, away = None, 0.0
    for s in story.get("shots") or []:
        k = s.get("key") or {}
        hit = _hit(s)
        if cur and hit == cur["feel"]:
            cur["all"].append(s)
            away = 0.0
        elif hit:
            cur = {"feel": hit, "all": [s]}
            out.append(cur)
            away = 0.0
        elif cur and k.get("role") == "reaction" and away + s["duration"] <= BRIDGE:
            away += s["duration"]
        else:
            cur = None
    rank = {"must": 0, "high": 1}
    for n in out:
        shots = n.pop("all")
        best = min(shots, key=lambda s: rank.get((s.get("key") or {}).get("priority"), 2))
        n.update(t=round(shots[0]["start"], 2), end=round(shots[-1]["end"], 2), shots=[s["index"] for s in shots],
                 what=(best.get("key") or {}).get("function") or (best.get("analysis") or {}).get("action_summary") or "")
    return out


def gaps(spans: list[tuple[float, float]], dur: float) -> list[dict]:
    """The quiet stretches: from the start to the first node, from the end of each node to the start of the next,
    from the last one to the end. Measured end to start: a comedy runs a gag every shot or two for half a minute,
    which is one long node, not a half-minute hole."""
    starts = [a for a, _ in spans] + [dur]
    ends = [0.0] + [b for _, b in spans]
    return [{"from": round(float(a), 1), "to": round(float(b), 1), "len": round(max(0.0, float(b - a)), 1)}
            for a, b in zip(ends, starts)]


def feel_mix(story: dict) -> dict[str, dict]:
    """Per feel: how many nodes (runs of shots) and how many seconds, most seconds first."""
    runs, secs, prev = Counter(), Counter(), ""
    for s in story.get("shots") or []:
        f = feel_of(s)
        if f:
            secs[f] += s["duration"]
            runs[f] += f != prev
        prev = f
    return {f: {"n": runs[f], "seconds": round(v, 1)} for f, v in secs.most_common()}


def pressure(story: dict) -> dict:
    """Press and release: every 爽 node with the 憋 before it. {"pairs": [{press, release, hold, pressed}],
    "free": 爽 nodes nothing in this episode pressed for (the pressure came from earlier episodes, or there was none),
    "owed": seconds of 憋 after the last 爽: what the next episode has to pay}."""
    pairs, free = [], 0
    first, pressed = None, 0.0
    marks = {n["shots"][0]: n for n in nodes(story) if n["feel"] == "爽"}
    for s in story.get("shots") or []:
        if s["index"] in marks:
            if first is None:
                free += 1
            else:
                pairs.append({"press": round(float(first), 1), "release": round(float(s["start"]), 1),
                              "hold": round(float(s["start"] - first), 1), "pressed": round(float(pressed), 1)})
            first, pressed = None, 0.0
        elif feel_of(s) == "憋":
            first = s["start"] if first is None else first
            pressed += s["duration"]
    return {"pairs": pairs, "free": free, "owed": round(float(pressed), 1)}
