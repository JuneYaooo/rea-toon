"""Hit-formula report of one episode, for writers and producers: why it holds people, what the formula is, how to reuse it.

The numbers are measured from the breakdown by code (pacing curve, the emotion ledger of emotion.py: nodes, the gaps
between them, press and release; screen time, who plays against whom, talk density); one model call reads them with the beats and writes the judgement (logline, hook, structure, tropes,
reusable formula), always pointing at shots and seconds. Writes data/report.json and pages/report.html (the same reading for models is
video/x-report.md, videomd.py); never touches story.json, so it does not unlock a reviewed breakdown.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import emotion, layout
from .genre import FORMATS, format_of, shown
from .lang import part as _part
from .page import bar_chart, cast_names, esc, fold, line_chart, meter, page, player, point, ref, stamp, statline, up
from .prompts import REPORT_PROMPT
from .story import PRIORITY_CN, ROLE_CN, gap_line, lock_state, prior_text, said_tag
from .subtitles import MODE_CN, line_mode, unique_lines
from .vlm import VLM, for_work

_ = _part("report")  # page words: rea_toon/i18n/en_report.py

LOG = logging.getLogger(__name__)

# how much a shot of each dramatic role lifts the curve; must / high shots lift it further
WEIGHT = {"hook": 3, "setup": 1, "conflict": 3, "reversal": 4, "reveal": 4, "climax": 5, "payoff": 5, "cliffhanger": 4,
          "reaction": 2, "transition": 0.5, "normal": 1}
FEEL_WEIGHT = {"爽": 5, "燃": 5, "甜": 4, "笑": 4, "虐": 4, "惊": 4, "憋": 3, "悬": 3}  # a gag shot is a peak of a comedy
BOOST = {"must": 1.5, "high": 0.5, "normal": 0}
_QUOTED = ("duration", "shots", "first_peak", "peaks", "nodes", "peak_gaps", "feels", "pressure", "cliffhanger")
DEAD_GAP = 30.0  # seconds without an emotional node: where people swipe away


def _main(story: dict) -> list[dict]:
    return [c for c in story.get("characters") or [] if c.get("importance") != "extra" and "crowd" not in c["id"]]


def _lines(story: dict) -> list[dict]:
    return unique_lines(story)


def intensity(shot: dict) -> float:
    k = shot.get("key") or {}
    return max(WEIGHT.get(k.get("role"), 1), FEEL_WEIGHT.get(k.get("feel"), 0)) + BOOST.get(k.get("priority"), 0)


def curve(story: dict, step: float | None = None) -> list[dict]:
    """Per time window: duration-weighted intensity, cuts and spoken characters per second."""
    shots = story.get("shots") or []
    dur = float(story.get("duration") or (shots[-1]["end"] if shots else 0))
    if not dur:
        return []
    step = step or max(2.0, round(dur / 24))
    out = []
    t = 0.0
    while t < dur - 0.01:
        a, b = t, min(dur, t + step)
        inside = [(s, min(b, s["end"]) - max(a, s["start"])) for s in shots if s["end"] > a and s["start"] < b]
        span = sum(w for _, w in inside) or 1
        talk = sum(len(str(x.get("text", ""))) * max(0.0, min(b, x["end"]) - max(a, x["start"])) / max(0.1, x["end"] - x["start"])
                   for x in _lines(story) if x["end"] > a and x["start"] < b)
        out.append({"t0": round(a, 2), "t1": round(b, 2), "intensity": round(sum(intensity(s) * w for s, w in inside) / span, 2),
                    "cuts": sum(1 for s in shots if a <= s["start"] < b and s["start"] > 0),
                    "talk": round(talk / (b - a), 2), "shots": [s["index"] for s, _ in inside]})
        t = b
    return out


def beat_times(story: dict) -> list[dict]:
    start = {s["index"]: s["start"] for s in story.get("shots") or []}
    out = []
    for b in (story.get("breakdown") or {}).get("beats") or []:
        shots = [int(i) for i in b.get("shots") or [] if str(i).isdigit() and int(i) in start]
        if shots:
            out.append({**b, "shots": shots, "t": round(min(start[i] for i in shots), 2)})
    return sorted(out, key=lambda b: b["t"])


def _move(shot: dict) -> str:
    """The camera move's main word: '升镜头同时轻微前推' -> '升镜头'."""
    text = str((shot.get("analysis") or {}).get("camera_move") or "").strip()
    return re.split(r"[，,、；;（(\s+/]|同时|然后|并", text)[0][:5] or "?"


def metrics(story: dict) -> dict:
    """Everything the report measures; no model call."""
    shots = story.get("shots") or []
    dur = float(story.get("duration") or (shots[-1]["end"] if shots else 0))
    lens = [float(s["duration"]) for s in shots]
    lines = _lines(story)
    by_index = {s["index"]: s for s in shots}
    beats = beat_times(story)
    # counted per shot, not per beat: the beats are the model's summary of the spine (see emotion.py)
    peaks = emotion.nodes(story)
    gaps = emotion.gaps([(n["t"], n["end"]) for n in peaks], dur)
    hit = sum(n["end"] - n["t"] for n in peaks)
    is_a = lambda s, role: (s.get("key") or {}).get("role") == role
    conflicts = [b["t"] for b in beats if b["type"] == "conflict"] + [s["start"] for s in shots if is_a(s, "conflict")]
    from .script import clean_line  # a sign OCR read into the subtitle band is not an opening line

    screen, talk, said = defaultdict(float), Counter(), Counter()
    for c in _main(story):
        screen[c["id"]] = round(sum(by_index[i]["duration"] for i in c.get("shots") or [] if i in by_index), 1)
    for x in lines:
        if x.get("speaker") and x["speaker"] != "title":
            talk[x["speaker"]] += len(str(x.get("text", "")))
            said[x["speaker"]] += 1
    main = {c["id"] for c in _main(story)}
    pairs: Counter = Counter()
    for s in shots:  # who shares the frame, and for how long
        here = sorted({c.get("id") for c in (s.get("analysis") or {}).get("characters") or []} & main)
        for i, a in enumerate(here):
            for b in here[i + 1:]:
                pairs[(a, b)] += s["duration"]
    exchanges: Counter = Counter()
    spoken = [x["speaker"] for x in lines if x.get("speaker") in main]
    for a, b in zip(spoken, spoken[1:]):  # one line answers another
        if a != b:
            exchanges[tuple(sorted((a, b)))] += 1

    modes = Counter(line_mode(x) for x in lines)
    chars = sum(len(str(x.get("text", ""))) for x in lines)
    size = Counter(str((s.get("analysis") or {}).get("shot_size") or "?").split("|")[0] for s in shots)
    move = Counter(_move(s) for s in shots)
    motion = Counter((s.get("motion") or {}).get("kind") for s in shots if (s.get("motion") or {}).get("kind"))
    st = [s.get("staging") or {} for s in shots]
    opening = [s for s in shots if s["start"] < 3.0]
    return {
        "duration": round(dur, 1), "shots": len(shots), "scenes": len((story.get("breakdown") or {}).get("scenes") or []),
        "avg_shot": round(sum(lens) / len(lens), 2) if lens else 0, "cuts_per_min": round(60 * len(shots) / dur, 1) if dur else 0,
        "short_shots": sum(1 for x in lens if x < 1.0), "long_shots": sum(1 for x in lens if x > 6.0),
        "talk_per_sec": round(chars / dur, 2) if dur else 0, "lines": len(lines),
        "talk_modes": {MODE_CN.get(k) or "画内": v for k, v in modes.items()},
        "first_conflict": min(conflicts, default=None), "first_peak": peaks[0]["t"] if peaks else None, "peaks": len(peaks),
        "hit_share": round(hit / dur, 2) if dur else 0,  # how much of the running time is inside a node
        "nodes": peaks, "node_kinds": dict(Counter(n["feel"] for n in peaks).most_common()),
        "feels": emotion.feel_mix(story), "pressure": emotion.pressure(story),
        "cliffhanger": any(b["type"] == "cliffhanger" for b in beats) or any(is_a(s, "cliffhanger") for s in shots),
        # the stretch before the first node is the setup (measured as first_peak), not a lull; with no node at all
        # the whole episode is one
        "peak_gaps": gaps, "dead_zones": [g for g in (gaps[1:] if peaks else gaps) if g["len"] > DEAD_GAP],
        "longest_gap": max((g["len"] for g in gaps[1:]), default=None) if peaks else None,
        "avg_gap": round(sum(g["len"] for g in gaps[1:-1]) / len(gaps[1:-1]), 1) if len(gaps) > 2 else None,
        "opening": {"shots": [s["index"] for s in opening],
                    "lines": [t for t in (clean_line(str(x["text"])) for x in lines if x["start"] < 3.0) if t]},
        "screen_time": dict(sorted(screen.items(), key=lambda kv: -kv[1])),
        "talk_chars": dict(talk.most_common()), "talk_lines": dict(said.most_common()),
        "pairs": [{"a": a, "b": b, "seconds": round(v, 1), "exchanges": exchanges.get((a, b), 0)}
                  for (a, b), v in pairs.most_common(8)],
        "shot_sizes": dict(size.most_common()), "camera_moves": dict(move.most_common(6)),
        "motion": dict(motion), "panels": sum(1 for x in st if (x.get("panels") or 1) > 1),
        "vfx_shots": sum(1 for x in st if x.get("effects")), "manga_marks": sum(1 for x in st if x.get("marks")),
        "bubbles": sum(1 for s in shots for t in s.get("screen_text") or [] if t.get("kind") == "bubble"),
        "overlays": sum(1 for s in shots for t in s.get("screen_text") or [] if t.get("kind") == "overlay"),
    }


def _metrics_text(m: dict) -> str:
    gaps = "、".join(f"{g['from']}–{g['to']}s（{g['len']}s）" for g in m["peak_gaps"])
    return "\n".join([
        f"时长 {m['duration']}s，{m['shots']} 镜，{m['scenes']} 场；平均镜长 {m['avg_shot']}s，每分钟 {m['cuts_per_min']} 刀；"
        f"不到 1 秒的镜头 {m['short_shots']} 个，超过 6 秒的 {m['long_shots']} 个",
        f"台词 {m['lines']} 句，每秒 {m['talk_per_sec']} 字；说法分布 {m['talk_modes']}",
        f"第一个冲突在 {m['first_conflict']}s，第一个情绪节点在 {m['first_peak']}s，情绪节点共 {m['peaks']} 个 {m['node_kinds']}"
        f"（按逐镜数：连续几镜打同一种情绪算一个），{'有' if m['cliffhanger'] else '没有'}结尾钩子",
        f"节点之间的空档（上一个节点结束到下一个开始）：{gaps}；平均 {m['avg_gap']}s，最长（含最后一个节点到片尾）{m['longest_gap']}s；"
        f"全片 {m['hit_share']:.0%} 的时长在情绪节点里（业内常说每 15–30 秒一个情绪节点、30 秒内出第一个）",
        *_ledger_text(m),
        f"开头 3 秒：镜头 {m['opening']['shots']}，台词 {m['opening']['lines']}",
        f"出镜秒数 {m['screen_time']}；台词字数 {m['talk_chars']}",
        "对手戏（同框秒数/你来我往的台词次数）：" + "；".join(f"{p['a']}×{p['b']} {p['seconds']}s/{p['exchanges']}次" for p in m["pairs"]),
        f"景别 {m['shot_sizes']}；运镜 {m['camera_moves']}；画面动法 {m['motion']}",
        f"多宫格 {m['panels']} 镜，特效 {m['vfx_shots']} 镜，漫符 {m['manga_marks']} 镜，对话气泡 {m['bubbles']} 处，花字 {m['overlays']} 处",
    ])


def _ledger_text(m: dict) -> list[str]:
    pr = m["pressure"]
    pairs = "、".join(f"{x['press']}s 开始憋 → {x['release']}s 放（等了 {x['hold']}s，其中憋的镜头 {x['pressed']}s）" for x in pr["pairs"])
    return ["情绪配比（节点数/秒数）：" + "，".join(f"{k} {v['n']} 个/{v['seconds']}s" for k, v in m["feels"].items()),
            f"憋→爽：{pairs or '没有成对的'}；没有先憋就放的爽点 {pr['free']} 个；片尾还欠着的憋 {pr['owed']}s"]


def _gap_rows(story: dict) -> str:
    who = lambda xs: "、".join(str(x) for x in xs) if isinstance(xs, list) else str(xs or "")
    return "\n".join(f"{g.get('what')}|{who(g.get('knows'))}|{who(g.get('blind'))}|镜{g.get('from')}–"
                     f"{g.get('to') if g.get('to') else '没揭开'}|{g.get('play', '')}"
                     for g in (story.get("breakdown") or {}).get("info_gaps") or [])


def _prompt(story: dict, m: dict, fmt: dict) -> str:
    main = {c["id"] for c in _main(story)}
    cast = "\n".join(f"{c['id']}|{c.get('role', '')}|{c.get('importance', '')}|{m['screen_time'].get(c['id'], 0)}|"
                     f"{m['talk_chars'].get(c['id'], 0)}" for c in story.get("characters") or [] if c["id"] in main)
    scenes = "\n".join(f"{sc['id']} 镜{sc['shots'][0]}-{sc['shots'][1]} {sc.get('location', '')} {sc.get('time', '')}"
                       for sc in (story.get("breakdown") or {}).get("scenes") or [])
    beats = "\n".join(f"{b['type']}|{b.get('feel', '')}|{b['t']}|{span(b['shots'])}|{b.get('what', '')}|{b.get('pays', '')}"
                      for b in beat_times(story))
    rows = []
    for s in story.get("shots") or []:
        k = s.get("key") or {}
        talk = " / ".join(f"{x.get('speaker', '?')}{said_tag(x)}：{x['text']}" for x in s.get("subtitles") or [] if not x.get("card"))
        rows.append(f"{s['index']}|{s['start']:.1f}|{s['duration']:.1f}|{ROLE_CN.get(k.get('role'), '')}|{k.get('feel', '')}|"
                    f"{PRIORITY_CN.get(k.get('priority'), '')}|{k.get('function') or (s.get('analysis') or {}).get('action_summary', '')}|{talk}")
    return REPORT_PROMPT.format(
        format=fmt["cn"], format_why=fmt["why"], lens="\n".join(f"- {x}" for x in fmt["lens"]),
        summary=story.get("summary", ""), prior=prior_text(story), cast=cast or "（无）", scenes=scenes or "（无）",
        beats=beats or "（无）", gaps=_gap_rows(story) or "（无）",
        table="\n".join(rows), metrics=_metrics_text(m))


SCHEMA = "rea_toon.report.v2"  # the shape of report.json; one written under another is not drawn, it is redone


def series_format(work: Path) -> str | None:
    """The format the series this episode belongs to settled on (series.json next to the episode dir), if any."""
    root = layout.series_of(work)
    try:
        fid = json.loads(layout.series(root).read_text(encoding="utf-8")).get("format") if root else None
    except (OSError, ValueError):
        return None
    return fid if fid in FORMATS else None


def build(story: dict, vlm: VLM | None, series: str | None = None) -> dict:
    """The report as data. Without a model only the measured part is filled. series: the format of the whole series."""
    m = metrics(story)
    fmt = format_of(story, series)
    rep = {"schema": SCHEMA, "title": (story.get("source") or {}).get("title") or "",
           "summary": story.get("summary", ""), "format": fmt, "metrics": m, "curve": curve(story),
           "beats": beat_times(story), "info_gaps": (story.get("breakdown") or {}).get("info_gaps") or [],
           "locked": lock_state(story) == "locked"}
    if vlm is not None:
        try:
            rep["read"] = vlm.ask_json(_prompt(story, m, fmt), [], max_tokens=8000, timeout=300, prose=True)
        except Exception as exc:  # the measured part still stands
            LOG.warning("爆款拆解（模型解读）失败：%s", exc)
            rep["read_error"] = str(exc)
    return rep


def report(work: Path, vlm: VLM | None = None, use_vlm: bool = True) -> dict:
    story = json.loads(layout.story(work).read_text(encoding="utf-8"))
    if not (story.get("breakdown") or {}).get("beats"):
        raise RuntimeError(f"还没有戏剧节拍：先 rea-toon redo breakdown {work}")
    if not emotion.has_feels(story):
        raise RuntimeError(f"这份拆解没有逐镜情绪，出不了报告：先 rea-toon redo breakdown {work}")
    if use_vlm and vlm is None:
        vlm = for_work(work)
    rep = build(story, vlm if use_vlm else None, series_format(work))
    old = layout.data(work, "report.json")
    if not use_vlm and old.is_file():  # numbers redone without a model: the last read is kept, and marked if they moved
        was = json.loads(old.read_text(encoding="utf-8"))
        if was.get("read"):
            rep["read"] = was["read"]
            now = json.loads(json.dumps(rep["metrics"]))  # the read quotes the pacing and the emotion ledger: stale when those moved
            rep["read_stale"] = bool(was.get("read_stale")) or any((was.get("metrics") or {}).get(k) != now.get(k) for k in _QUOTED)
    layout.put(old, json.dumps(rep, ensure_ascii=False, indent=1))
    render(work, rep, story)
    from .videomd import write as package
    package(work, story)  # the same reading for models: video/x-report.md
    return rep


def render(work: Path, rep: dict | None = None, story: dict | None = None) -> bool:
    """pages/report.html from data/report.json on disk (no model call, nothing else written); False when there is none."""
    if rep is None:
        if not layout.data(work, "report.json").is_file():
            return False
        rep = json.loads(layout.data(work, "report.json").read_text(encoding="utf-8"))
        if rep.get("schema") != SCHEMA:
            LOG.warning("%s 的 report.json 不是这一版的，页面没有出：rea-toon redo report %s", work.name, work)
            return False
    if story is None:
        story = json.loads(layout.story(work).read_text(encoding="utf-8"))
    out = layout.page(work, "report.html")
    layout.put(out, to_html(rep, story, layout.from_page(work, layout.source(work)) if layout.source(work).is_file() else None, cast_names(work)))
    stamp(out)
    return True


# ---- pages ----

def _interp(pts: list[tuple[float, float]], t: float) -> float:
    """y of the drawn line at x = t, so a beat marker sits on the curve."""
    if t <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= t <= x1:
            return y0 + (y1 - y0) * (t - x0) / ((x1 - x0) or 1)
    return pts[-1][1]


def span(shots: list) -> str:
    """[1, 2, 3, 5] -> '1–3, 5'."""
    out, run = [], []
    for i in sorted({int(x) for x in shots}):
        if run and i == run[-1] + 1:
            run.append(i)
        else:
            if run:
                out.append(f"{run[0]}–{run[-1]}" if len(run) > 1 else str(run[0]))
            run = [i]
    if run:
        out.append(f"{run[0]}–{run[-1]}" if len(run) > 1 else str(run[0]))
    return ", ".join(out)


def _sec(x) -> str:
    return "—" if x is None else f"{x:.1f}s"


def _beat_feel(b: dict) -> str:
    return _("（{feel}）").format(feel=_(b["feel"])) if b.get("feel") else ""


def _range(x, pair: bool = True) -> str:
    """Shots as the model gave them, for reading: [1, 9] (a from-to pair) -> 1–9; a list of shots -> 5、39、45."""
    if not isinstance(x, list) or not all(str(i).lstrip("-").isdigit() for i in x):
        return str(x or "")
    x = [int(i) for i in x]
    return f"{x[0]}–{x[1]}" if pair and len(x) == 2 and x[0] < x[1] else span(x).replace(", ", _("、"))


def _paras(text) -> str:
    """A long paragraph as a list, one point a line: a wall of numbers is not read."""
    text = str(text or "")
    parts = [p for p in re.split(r"(?<=[。！？])", text) if p.strip()]
    if len(text) < 140 or len(parts) < 3:
        return f"<p>{esc(text)}</p>"
    return "<ul>" + "".join(f"<li>{esc(p.strip())}</li>" for p in parts) + "</ul>"


def _short(role) -> str:
    """A character's role as a label: 姐姐（温月） -> 温月, 纪氏集团总裁/大曾孙 -> 纪氏集团总裁."""
    role = str(role or "")
    inside = re.findall(r"[（(]([^）)]+)[）)]", role)
    return (inside[0] if inside else re.split(r"[/，,、]", role)[0]).strip()


def _names(xs) -> str:
    return _("、").join(str(x) for x in xs) if isinstance(xs, list) else str(xs or "")


def _ledger_shown(m: dict) -> list[str]:
    """_ledger_text in the page language (that one goes into the prompt and stays as it is)."""
    pr = m["pressure"]
    pairs = _("、").join(_("{press}s 开始憋 → {release}s 放（等了 {hold}s，其中憋的镜头 {pressed}s）").format(**x)
                         for x in pr["pairs"])
    return [_("情绪配比（节点数/秒数）：") + _("，").join(_("{feel} {n} 个/{seconds}s").format(feel=_(k), n=v["n"], seconds=v["seconds"])
                                                for k, v in m["feels"].items()),
            _("憋→爽：{pairs}；没有先憋就放的爽点 {free} 个；片尾还欠着的憋 {owed}s").format(
                pairs=pairs or _("没有成对的"), free=pr["free"], owed=pr["owed"])]


def _gap_shown(g: dict) -> str:
    """story.gap_line in the page language."""
    opened = _("镜 {shot} 揭开").format(shot=g["to"]) if g.get("to") else _("这一集没揭开")
    return _("{what}：{knows} 知道，{blind} 不知道；{opened}。{play}").format(
        what=g.get("what"), knows=_names(g.get("knows")), blind=_names(g.get("blind")), opened=opened, play=g.get("play") or "")


def to_md(rep: dict) -> str:
    m, r = rep["metrics"], rep.get("read") or {}
    tags = r.get("tags") or {}
    fmt = shown(rep["format"])
    L = [_("# 爆款拆解：{title}").format(title=rep["title"] or "source"), "",
         _("**类型**：{format}（{why}）").format(format=fmt["cn"], why=fmt["why"]), ""]
    if r.get("logline"):
        L += [_("**一句话卖点**：{logline}").format(logline=r["logline"]), ""]
    if tags:
        L += [_("**题材**：{themes}　**受众**：{audience}　**爽感**：{pleasure}").format(
            themes=_("、").join(tags.get("themes") or []), audience=tags.get("audience", ""),
            pleasure=_("、").join(tags.get("pleasure") or [])), ""]
    L += [_("## 数据"), "",
          "- " + _("时长 {duration}s · {shots} 镜 · {scenes} 场 · 平均镜长 {avg_shot}s · 每分钟 {cuts_per_min} 刀").format(**m),
          "- " + _("台词每秒 {talk} 字 · 第一个冲突 {conflict} · 第一个情绪节点 {peak} · 情绪节点 {peaks} 个 · {cliff}").format(
              talk=m["talk_per_sec"], conflict=_sec(m["first_conflict"]), peak=_sec(m["first_peak"]), peaks=m["peaks"],
              cliff=_("有结尾钩子") if m["cliffhanger"] else _("没有结尾钩子"))]
    if m.get("avg_gap") is not None:
        L.append("- " + _("节点之间的空档：平均 {avg_gap}s · 最长 {longest_gap}s · {hit_share:.0%} 的时长在节点里").format(**m))
    if m["first_peak"] is not None and m["first_peak"] > DEAD_GAP:
        L.append("- ⚠️ " + _("第一个情绪节点在 {t:.1f}s，晚于 {limit:.0f}s").format(t=m["first_peak"], limit=DEAD_GAP))
    L += ["- ⚠️ " + _("{a}–{b}s 连续 {n}s 没有情绪节点").format(a=g["from"], b=g["to"], n=g["len"]) for g in m["dead_zones"]] + [""]
    if r.get("hook"):
        L += [_("## 开头钩子"), "", r["hook"], ""]
    L += [_("## 情绪账"), ""] + ([r["engine"], ""] if r.get("engine") else []) + [f"- {x}" for x in _ledger_shown(m)]
    L += [_("- {t:.1f}s **{feel}** 镜 {shots}：{what}").format(t=n["t"], feel=_(n["feel"]), shots=span(n["shots"]), what=n["what"])
          for n in m["nodes"]] + [""]
    if rep.get("info_gaps"):
        L += [_("## 信息差"), ""] + ([r["info_gap"], ""] if r.get("info_gap") else [])
        L += [f"- {_gap_shown(g)}" for g in rep["info_gaps"]] + [""]
    if r.get("structure"):
        L += [_("## 结构"), ""] + [_("- **{part}**（镜 {shots}）：{what}").format(part=x.get("part"), shots=x.get("shots"), what=x.get("what"))
                                 for x in r["structure"]] + [""]
    L += [_("## 戏剧节拍"), ""] + [_("- {t:.1f}s **{role}**{feel} 镜 {shots}：{what}").format(
        t=b["t"], role=_(ROLE_CN.get(b["type"], b["type"])), feel=_beat_feel(b), shots=span(b["shots"]), what=b.get("what", ""))
        + (_("（兑现：{pays}）").format(pays=b["pays"]) if b.get("pays") else "") for b in rep["beats"]] + [""]
    if r.get("tropes"):
        L += [_("## 套路"), ""] + [_("- **{name}**（镜 {shots}）：{how}").format(name=x.get("name"), shots=x.get("shots"), how=x.get("how"))
                                 for x in r["tropes"]] + [""]
    if r.get("characters"):
        L += [_("## 人设"), ""] + [_("- **{id}** {persona}：{function}；记忆点：{memorable}").format(
            id=x.get("id"), persona=x.get("persona"), function=x.get("function"), memorable=x.get("memorable"))
            for x in r["characters"]] + [""]
    if r.get("relations"):
        L += [_("## 关系和张力"), ""] + [_("- {a} × {b}：{relation}；{tension}").format(
            a=x.get("a"), b=x.get("b"), relation=x.get("relation"), tension=x.get("tension")) for x in r["relations"]] + [""]
    for key, name in (("why_it_works", _("为什么留得住人")), ("weaknesses", _("短板")), ("remix", _("改编方向"))):
        if r.get(key):
            L += [f"## {name}", ""] + [f"- {point(x)}" for x in r[key]] + [""]
    if r.get("formula"):
        L += [_("## 可复用公式"), ""] + [_("{i}. **{step}**（约 {share:.0%}）：{what}").format(
            i=i, step=x.get("step"), share=float(x.get("share") or 0), what=x.get("what"))
            for i, x in enumerate(r["formula"], 1)] + [""]
    L += [_("## 这类剧要看的"), ""] + [f"- {x}" for x in fmt["lens"]]
    return "\n".join(L) + "\n"


def _list(items, fmt) -> str:
    return "<ul>" + "".join(f"<li>{fmt(x)}</li>" for x in items) + "</ul>" if items else ""


def to_html(rep: dict, story: dict | None = None, video: str | None = None, called: dict | None = None) -> str:
    """The page, in the order it is read: what the episode sells and why it holds (the conclusions), how to reuse
    it, how it runs over time; everything that backs those up is one line each at the bottom, opened when wanted.
    Every shot named anywhere on the page plays on a click (page.player; video: the source's path from the page,
    which sits in pages/).
    called: names to use for the characters (page.cast_names) instead of their roles."""
    m, r = rep["metrics"], rep.get("read") or {}
    tags = r.get("tags") or {}
    fmt = shown(rep["format"])
    sep = _("、")
    shots = {s["index"]: s for s in (story or {}).get("shots") or []}
    names = {**{c["id"]: _short(c.get("role")) or c["id"] for c in (story or {}).get("characters") or []}, **(called or {})}
    who = lambda x: sep.join(who(i) for i in x) if isinstance(x, list) else names.get(x, str(x or ""))
    title = re.split(r"\s*#", rep["title"] or "source")[0] or rep["title"]  # the post's hashtags are not the title
    B = [f"<h1>{esc(title)}</h1>",
         f"<p><span class=state title='{esc(fmt['why'])}'>{esc(fmt['cn'])}</span></p>"]
    if r.get("logline"):
        B.append(f"<p class=lead>{esc(r['logline'])}</p>")
    chips = [*(tags.get("themes") or []), tags.get("audience"), *(tags.get("pleasure") or [])]
    if any(chips):
        B.append("<p>" + "".join(f"<span class=chip>{esc(c)}</span>" for c in chips if c) + "</p>")
    if rep.get("read_stale"):
        B.append("<p class=warn>" + _("文字解读写在数据更新之前，引用的数字可能和图表对不上。") + "</p>")
    if rep.get("read_error"):
        B.append("<p class=err>" + _("模型解读失败（只有数据部分）：{error}；重跑 rea-toon redo report").format(error=esc(rep["read_error"])) + "</p>")
    B.append(statline([("", f"{m['duration']:.0f}s"), (_("镜"), m["shots"]), (_("第一个情绪节点"), _sec(m["first_peak"])),
                       (_("个情绪节点"), m["peaks"]), (_("节点间平均空档"), _sec(m.get("avg_gap"))),
                       (_("结尾钩子"), _("有") if m["cliffhanger"] else _("没有"))]))

    # ---- the conclusions ----
    why, weak = r.get("why_it_works"), r.get("weaknesses")
    if why or weak:
        col = lambda name, xs: f"<div><h2>{name}</h2>{_list(xs, lambda x: esc(point(x)))}</div>" if xs else ""
        B.append(f"<div class=two>{col(_('为什么留得住人'), why)}{col(_('短板'), weak)}</div>")
    if r.get("formula"):
        B.append(f"<h2>{_('可复用公式')}</h2><div class=tw><table><tr><th>#</th><th>{_('步骤')}</th><th>{_('占比')}</th><th>{_('要做到')}</th></tr>"
                 + "".join(f"<tr><td>{i}</td><td class=n><b>{esc(x.get('step'))}</b></td><td class=n>{float(x.get('share') or 0):.0%}</td><td>{esc(x.get('what'))}</td></tr>"
                           for i, x in enumerate(r["formula"], 1)) + "</table></div>")
    if r.get("remix"):
        B += [f"<h3>{_('改编方向')}</h3>", _list(r["remix"], lambda x: esc(point(x)))]

    # ---- how it runs: the curve, and the parts under it ----
    cv = rep.get("curve") or []
    if cv or r.get("structure"):
        B.append(f"<h2>{_('结构和节奏')}</h2>")
    if cv:
        pts = [((c["t0"] + c["t1"]) / 2, c["intensity"]) for c in cv]
        tips = [_("{t0:.0f}–{t1:.0f}s 强度 {intensity} · 切 {cuts} 刀 · 每秒 {talk} 字").format(**c)
                + (_(" · 镜 {a}–{b}").format(a=c["shots"][0], b=c["shots"][-1]) if c["shots"] else "") for c in cv]
        at = lambda t: _interp(pts, t)
        mark = _("{t:.1f}s {what}：{how}")
        marks = sorted([(b["t"], at(b["t"]), mark.format(t=b["t"], what=_(ROLE_CN.get(b["type"], b["type"])), how=b.get("what", "")))
                        for b in rep["beats"] if b["type"] in ("hook", "cliffhanger")]
                       + [(n["t"], at(n["t"]), mark.format(t=n["t"], what=_(n["feel"]), how=n["what"])) for n in m["nodes"]])
        B += ["<p class=muted>" + _("情绪强度随时间；圆点是钩子和情绪节点，悬停看是哪一镜。") + "</p>",
              line_chart(pts, marks, _("情绪强度随时间"), max(6.5, max(p[1] for p in pts)), tips)]
    if m["first_peak"] is not None and m["first_peak"] > DEAD_GAP:
        B.append("<p class=warn>" + _("第一个情绪节点在 {t:.1f}s，晚于 {limit:.0f}s：开头可能留不住人").format(t=m["first_peak"], limit=DEAD_GAP) + "</p>")
    for g in m["dead_zones"]:  # a stretch held by suspense is waiting, not dead air: say which it is
        held = round(sum(max(0.0, min(g["to"], s["end"]) - max(g["from"], s["start"])) for s in (story or {}).get("shots") or []
                         if emotion.feel_of(s) in ("悬", "憋")), 1)
        if g["len"] - held > DEAD_GAP:
            B.append("<p class=warn>" + _("{a}–{b}s 连续 {n}s 没有情绪节点，也没有悬念撑着：容易划走").format(a=g["from"], b=g["to"], n=g["len"]) + "</p>")
        else:
            B.append("<p class=muted>" + _("{a}–{b}s 没有新的情绪节点，其中 {held}s 靠悬念撑着").format(a=g["from"], b=g["to"], held=held) + "</p>")
    for x in r.get("structure") or []:
        pair = x.get("shots") if isinstance(x.get("shots"), list) else []
        a, b = (shots.get(_int(pair[0])), shots.get(_int(pair[-1]))) if pair else (None, None)
        when = f" · {a['start']:.0f}–{b['end']:.0f}s" if a and b else ""
        pic = (f"<img loading=lazy src='{esc(up((a.get('frames') or {}).get('mid')))}' alt='' data-shot={a['index']} data-to={b['index']}>"
               if a and b else "<span></span>")
        at_shots = _("镜 {shots}").format(shots=ref(pair, _range(pair)) if a and b else esc(_range(x.get("shots"))))
        B.append(f"<div class=tl>{pic}<div><b>{esc(x.get('part'))}</b><span class=muted>{at_shots}"
                 f"{when}</span><div>{esc(x.get('what'))}</div></div></div>")

    # ---- what backs it up: one line each ----
    F = []
    hook = (_paras(r["hook"]) if r.get("hook") else "")
    if r.get("tropes"):
        hook += (f"<h3>{_('套路')}</h3>" if hook else "") + _list(r["tropes"], lambda x: (
            f"<b>{esc(x.get('name'))}</b> <span class=muted>{_('镜 {shots}').format(shots=_refs(x.get('shots')))}</span>{_('：')}{esc(x.get('how'))}"))
    F.append(fold(_("开头钩子和套路") if r.get("hook") and r.get("tropes") else _("套路") if r.get("tropes") else _("开头钩子"),
                  sep.join(str(x.get("name")) for x in r.get("tropes") or []) or _("前几秒怎么留人"), hook))

    E = [_paras(r["engine"])] if r.get("engine") else []
    pr = m["pressure"]
    if m["feels"]:
        E += ["<p class=muted>" + _("每种情绪占的秒数（爽、虐、甜、笑、惊、燃是打到观众的；憋、悬是在攒）") + "</p>",
              bar_chart([(_(k), v["seconds"], _("{feel}：{n} 段，共 {seconds}s").format(feel=_(k), n=v["n"], seconds=v["seconds"]))
                         for k, v in m["feels"].items()], "s")]
    if pr["pairs"]:
        E.append(f"<div class=tw><table><tr><th>{_('开始憋')}</th><th>{_('放（爽）')}</th><th>{_('等了')}</th><th>{_('其中憋的镜头')}</th></tr>"
                 + "".join(f"<tr><td class=n>{x['press']}s</td><td class=n>{x['release']}s</td><td class=n>{x['hold']}s</td>"
                           f"<td class=n>{x['pressed']}s</td></tr>" for x in pr["pairs"]) + "</table></div>")
    if pr["owed"]:
        E.append("<p class=muted>" + _("有 {owed} 秒的憋到片尾也没放，留给后面的集。").format(owed=pr["owed"]) + "</p>")
    if m["nodes"]:
        E.append(f"<div class=tw><table><tr><th>{_('时间')}</th><th>{_('情绪')}</th><th>{_('镜头')}</th><th>{_('做成了什么')}</th></tr>"
                 + "".join(f"<tr><td class=n>{n['t']:.1f}s</td><td class=n>{esc(_(n['feel']))}</td><td class=n>{ref(n['shots'], span(n['shots']))}</td>"
                           f"<td>{esc(n['what'])}</td></tr>" for n in m["nodes"]) + "</table></div>")
    mix = sep.join(_("{feel} {seconds:g}s").format(feel=_(k), seconds=v["seconds"]) for k, v in list(m["feels"].items())[:3])
    F.append(fold(_("情绪账"), _("{n} 个节点").format(n=m["peaks"]) + (f" · {mix}" if mix else ""), "".join(E)))

    if rep.get("info_gaps"):
        F.append(fold(_("信息差"), _("{n} 条 · 谁知道、谁不知道、哪一镜揭开").format(n=len(rep["info_gaps"])),
                      (_paras(r["info_gap"]) if r.get("info_gap") else "")
                      + f"<div class=tw><table><tr><th>{_('是什么')}</th><th>{_('谁知道')}</th><th>{_('谁不知道')}</th><th>{_('揭开')}</th><th>{_('怎么做戏')}</th></tr>"
                      + "".join(f"<tr><td>{esc(g.get('what'))}</td><td>{esc(who(g.get('knows')))}</td><td>{esc(who(g.get('blind')))}</td>"
                                f"<td class=n>{_('镜 {shot}').format(shot=ref(g['to'])) if g.get('to') else '<span class=warn>' + _('没揭开') + '</span>'}</td>"
                                f"<td>{esc(g.get('play'))}</td></tr>" for g in rep["info_gaps"]) + "</table></div>"))

    # one table of people: screen time is a column of it, not a chart of its own
    read = {x.get("id"): x for x in r.get("characters") or []}
    top = max(m["screen_time"].values(), default=0)
    P = ""
    if m["screen_time"]:
        P += (f"<div class=tw><table><tr><th>{_('角色')}</th><th>{_('出镜')}</th><th>{_('台词')}</th>"
              + (f"<th>{_('人设')}</th><th>{_('作用')}</th><th>{_('记忆点')}</th>" if read else "") + "</tr>"
              + "".join(f"<tr><td class=n><b>{esc(who(k))}</b></td><td class=n>{meter(v, top, f'{v:g}s')}</td>"
                        f"<td class=n>{_('{lines} 句 / {chars} 字').format(lines=m['talk_lines'].get(k, 0), chars=m['talk_chars'].get(k, 0))}</td>"
                        + (f"<td>{esc((read.get(k) or {}).get('persona'))}</td><td>{esc((read.get(k) or {}).get('function'))}</td>"
                           f"<td>{esc((read.get(k) or {}).get('memorable'))}</td>" if read else "") + "</tr>"
                        for k, v in m["screen_time"].items() if v or k in read) + "</table></div>")
    rel = {tuple(sorted((x.get("a", ""), x.get("b", "")))): x for x in r.get("relations") or []}
    duels = [p for p in m["pairs"] if p["exchanges"] or (p["a"], p["b"]) in rel]  # sharing a frame in silence is no scene
    if duels:
        P += (f"<h3>{_('对手戏')}</h3><div class=tw><table><tr><th>{_('两人')}</th><th>{_('同框')}</th><th>{_('你来我往')}</th><th>{_('关系和张力')}</th></tr>"
              + "".join(f"<tr><td>{esc(who(p['a']))} × {esc(who(p['b']))}</td><td class=n>{p['seconds']}s</td><td class=n>{_('{n} 次').format(n=p['exchanges'])}</td>"
                        f"<td>{esc((rel.get((p['a'], p['b'])) or {}).get('relation'))} {esc((rel.get((p['a'], p['b'])) or {}).get('tension'))}</td></tr>"
                        for p in duels) + "</table></div>")
    F.append(fold(_("人物和对手戏"), sep.join(who(k) for k in list(m["screen_time"])[:4]) + (_(" 等") if len(m["screen_time"]) > 4 else ""), P))

    kv = lambda d, k=4: sep.join(f"{_(a)} {b}" for a, b in list(d.items())[:k]) if isinstance(d, dict) else str(d or "")
    scenes = ((story or {}).get("breakdown") or {}).get("scenes") or []
    places = list(dict.fromkeys(re.sub(r"[（(][^）)]*[）)]", "", str(x.get("location") or "")).strip() for x in scenes))
    looks = sum(len(x.get("looks") or {}) for x in scenes)
    F.append(fold(_("制作量"), _("{places} 个场景 · {cast} 个角色 · {shots} 镜").format(places=len(places) or m["scenes"], cast=len(m["screen_time"]), shots=m["shots"]), "".join([
        "<ul>", (_("<li><b>场景</b> {n} 个：{places}</li>").format(n=len(places), places=esc(sep.join(p for p in places if p))) if places else ""),
        _("<li><b>演员</b> {n} 个有戏份的角色").format(n=len(m["screen_time"])) + (_("，分场造型共 {looks} 套次").format(looks=looks) if looks else "") + "</li>",
        _("<li><b>镜头</b> {n} 个，平均 {avg}s，每分钟切 {cuts} 刀；景别：{sizes}；运镜：{moves}</li>").format(
            n=m["shots"], avg=m["avg_shot"], cuts=m["cuts_per_min"], sizes=esc(kv(m["shot_sizes"])), moves=esc(kv(m["camera_moves"], 3))),
        _("<li><b>台词</b> 每秒 {talk} 字；{modes}</li>").format(talk=m["talk_per_sec"], modes=esc(kv(m["talk_modes"]))),
        (_("<li><b>后期</b> 特效 {vfx} 镜、花字 {overlays} 处</li>").format(vfx=m["vfx_shots"], overlays=m["overlays"]) if m["vfx_shots"] or m["overlays"] else ""), "</ul>"])))
    F = [x for x in F if x]
    if F:
        B.append("<div class=more><p class=muted>" + _("依据和细节，点开看") + "</p>" + "".join(F) + "</div>")
    return page(_("爆款拆解 {title}").format(title=rep["title"] or "").strip(), B, player(story, video))


def _int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _refs(xs) -> str:
    """Shots a trope sits on, each one to click: 2、6、45."""
    if not isinstance(xs, list) or any(_int(i) is None for i in xs):
        return esc(_range(xs, False))
    return _("、").join(ref(int(i)) for i in xs)
