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
from .genre import FORMATS, format_of
from .page import bar_chart, cast_names, esc, fold, line_chart, meter, page, player, point, ref, stamp, statline, up
from .prompts import REPORT_PROMPT
from .story import PRIORITY_CN, ROLE_CN, gap_line, lock_state, prior_text, said_tag
from .subtitles import MODE_CN, line_mode, unique_lines
from .vlm import VLM

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
            rep["read"] = vlm.ask_json(_prompt(story, m, fmt), [], max_tokens=8000, timeout=300)
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
        vlm = VLM(cache_dir=layout.analysis(work, "vlm_cache"))
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
    return f"（{b['feel']}）" if b.get("feel") else ""


def _range(x, pair: bool = True) -> str:
    """Shots as the model gave them, for reading: [1, 9] (a from-to pair) -> 1–9; a list of shots -> 5、39、45."""
    if not isinstance(x, list) or not all(str(i).lstrip("-").isdigit() for i in x):
        return str(x or "")
    x = [int(i) for i in x]
    return f"{x[0]}–{x[1]}" if pair and len(x) == 2 and x[0] < x[1] else span(x).replace(", ", "、")


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
    return "、".join(str(x) for x in xs) if isinstance(xs, list) else str(xs or "")


def to_md(rep: dict) -> str:
    m, r = rep["metrics"], rep.get("read") or {}
    tags = r.get("tags") or {}
    L = [f"# 爆款拆解：{rep['title'] or 'source'}", "", f"**类型**：{rep['format']['cn']}（{rep['format']['why']}）", ""]
    if r.get("logline"):
        L += [f"**一句话卖点**：{r['logline']}", ""]
    if tags:
        L += [f"**题材**：{'、'.join(tags.get('themes') or [])}　**受众**：{tags.get('audience', '')}　"
              f"**爽感**：{'、'.join(tags.get('pleasure') or [])}", ""]
    L += ["## 数据", "", f"- 时长 {m['duration']}s · {m['shots']} 镜 · {m['scenes']} 场 · 平均镜长 {m['avg_shot']}s · 每分钟 {m['cuts_per_min']} 刀",
          f"- 台词每秒 {m['talk_per_sec']} 字 · 第一个冲突 {_sec(m['first_conflict'])} · 第一个情绪节点 {_sec(m['first_peak'])} · "
          f"情绪节点 {m['peaks']} 个 · {'有' if m['cliffhanger'] else '没有'}结尾钩子"]
    if m.get("avg_gap") is not None:
        L.append(f"- 节点之间的空档：平均 {m['avg_gap']}s · 最长 {m['longest_gap']}s · {m['hit_share']:.0%} 的时长在节点里")
    if m["first_peak"] is not None and m["first_peak"] > DEAD_GAP:
        L.append(f"- ⚠️ 第一个情绪节点在 {m['first_peak']:.1f}s，晚于 {DEAD_GAP:.0f}s")
    L += [f"- ⚠️ {g['from']}–{g['to']}s 连续 {g['len']}s 没有情绪节点" for g in m["dead_zones"]] + [""]
    if r.get("hook"):
        L += ["## 开头钩子", "", r["hook"], ""]
    L += ["## 情绪账", ""] + ([r["engine"], ""] if r.get("engine") else []) + [f"- {x}" for x in _ledger_text(m)]
    L += [f"- {n['t']:.1f}s **{n['feel']}** 镜 {span(n['shots'])}：{n['what']}" for n in m["nodes"]] + [""]
    if rep.get("info_gaps"):
        L += ["## 信息差", ""] + ([r["info_gap"], ""] if r.get("info_gap") else [])
        L += [f"- {gap_line(g)}" for g in rep["info_gaps"]] + [""]
    if r.get("structure"):
        L += ["## 结构", ""] + [f"- **{x.get('part')}**（镜 {x.get('shots')}）：{x.get('what')}" for x in r["structure"]] + [""]
    L += ["## 戏剧节拍", ""] + [f"- {b['t']:.1f}s **{ROLE_CN.get(b['type'], b['type'])}**{_beat_feel(b)} 镜 {span(b['shots'])}：{b.get('what', '')}"
                              + (f"（兑现：{b['pays']}）" if b.get("pays") else "") for b in rep["beats"]] + [""]
    if r.get("tropes"):
        L += ["## 套路", ""] + [f"- **{x.get('name')}**（镜 {x.get('shots')}）：{x.get('how')}" for x in r["tropes"]] + [""]
    if r.get("characters"):
        L += ["## 人设", ""] + [f"- **{x.get('id')}** {x.get('persona')}：{x.get('function')}；记忆点：{x.get('memorable')}"
                              for x in r["characters"]] + [""]
    if r.get("relations"):
        L += ["## 关系和张力", ""] + [f"- {x.get('a')} × {x.get('b')}：{x.get('relation')}；{x.get('tension')}" for x in r["relations"]] + [""]
    for key, name in (("why_it_works", "为什么留得住人"), ("weaknesses", "短板"), ("remix", "改编方向")):
        if r.get(key):
            L += [f"## {name}", ""] + [f"- {point(x)}" for x in r[key]] + [""]
    if r.get("formula"):
        L += ["## 可复用公式", ""] + [f"{i}. **{x.get('step')}**（约 {float(x.get('share') or 0):.0%}）：{x.get('what')}"
                                   for i, x in enumerate(r["formula"], 1)] + [""]
    L += ["## 这类剧要看的", ""] + [f"- {x}" for x in rep["format"]["lens"]]
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
    shots = {s["index"]: s for s in (story or {}).get("shots") or []}
    names = {**{c["id"]: _short(c.get("role")) or c["id"] for c in (story or {}).get("characters") or []}, **(called or {})}
    who = lambda x: "、".join(who(i) for i in x) if isinstance(x, list) else names.get(x, str(x or ""))
    title = re.split(r"\s*#", rep["title"] or "source")[0] or rep["title"]  # the post's hashtags are not the title
    B = [f"<h1>{esc(title)}</h1>",
         f"<p><span class=state title='{esc(rep['format']['why'])}'>{esc(rep['format']['cn'])}</span></p>"]
    if r.get("logline"):
        B.append(f"<p class=lead>{esc(r['logline'])}</p>")
    chips = [*(tags.get("themes") or []), tags.get("audience"), *(tags.get("pleasure") or [])]
    if any(chips):
        B.append("<p>" + "".join(f"<span class=chip>{esc(c)}</span>" for c in chips if c) + "</p>")
    if rep.get("read_stale"):
        B.append("<p class=warn>文字解读写在数据更新之前，引用的数字可能和图表对不上。</p>")
    if rep.get("read_error"):
        B.append(f"<p class=err>模型解读失败（只有数据部分）：{esc(rep['read_error'])}；重跑 rea-toon redo report</p>")
    B.append(statline([("", f"{m['duration']:.0f}s"), ("镜", m["shots"]), ("第一个情绪节点", _sec(m["first_peak"])),
                       ("个情绪节点", m["peaks"]), ("节点间平均空档", _sec(m.get("avg_gap"))),
                       ("结尾钩子", "有" if m["cliffhanger"] else "没有")]))

    # ---- the conclusions ----
    why, weak = r.get("why_it_works"), r.get("weaknesses")
    if why or weak:
        col = lambda name, xs: f"<div><h2>{name}</h2>{_list(xs, lambda x: esc(point(x)))}</div>" if xs else ""
        B.append(f"<div class=two>{col('为什么留得住人', why)}{col('短板', weak)}</div>")
    if r.get("formula"):
        B.append("<h2>可复用公式</h2><div class=tw><table><tr><th>#</th><th>步骤</th><th>占比</th><th>要做到</th></tr>"
                 + "".join(f"<tr><td>{i}</td><td class=n><b>{esc(x.get('step'))}</b></td><td class=n>{float(x.get('share') or 0):.0%}</td><td>{esc(x.get('what'))}</td></tr>"
                           for i, x in enumerate(r["formula"], 1)) + "</table></div>")
    if r.get("remix"):
        B += ["<h3>改编方向</h3>", _list(r["remix"], lambda x: esc(point(x)))]

    # ---- how it runs: the curve, and the parts under it ----
    cv = rep.get("curve") or []
    if cv or r.get("structure"):
        B.append("<h2>结构和节奏</h2>")
    if cv:
        pts = [((c["t0"] + c["t1"]) / 2, c["intensity"]) for c in cv]
        tips = [f"{c['t0']:.0f}–{c['t1']:.0f}s 强度 {c['intensity']} · 切 {c['cuts']} 刀 · 每秒 {c['talk']} 字"
                + (f" · 镜 {c['shots'][0]}–{c['shots'][-1]}" if c["shots"] else "") for c in cv]
        at = lambda t: _interp(pts, t)
        marks = sorted([(b["t"], at(b["t"]), f"{b['t']:.1f}s {ROLE_CN.get(b['type'], b['type'])}：{b.get('what', '')}")
                        for b in rep["beats"] if b["type"] in ("hook", "cliffhanger")]
                       + [(n["t"], at(n["t"]), f"{n['t']:.1f}s {n['feel']}：{n['what']}") for n in m["nodes"]])
        B += ["<p class=muted>情绪强度随时间；圆点是钩子和情绪节点，悬停看是哪一镜。</p>",
              line_chart(pts, marks, "情绪强度随时间", max(6.5, max(p[1] for p in pts)), tips)]
    if m["first_peak"] is not None and m["first_peak"] > DEAD_GAP:
        B.append(f"<p class=warn>第一个情绪节点在 {m['first_peak']:.1f}s，晚于 {DEAD_GAP:.0f}s：开头可能留不住人</p>")
    for g in m["dead_zones"]:  # a stretch held by suspense is waiting, not dead air: say which it is
        held = round(sum(max(0.0, min(g["to"], s["end"]) - max(g["from"], s["start"])) for s in (story or {}).get("shots") or []
                         if emotion.feel_of(s) in ("悬", "憋")), 1)
        if g["len"] - held > DEAD_GAP:
            B.append(f"<p class=warn>{g['from']}–{g['to']}s 连续 {g['len']}s 没有情绪节点，也没有悬念撑着：容易划走</p>")
        else:
            B.append(f"<p class=muted>{g['from']}–{g['to']}s 没有新的情绪节点，其中 {held}s 靠悬念撑着</p>")
    for x in r.get("structure") or []:
        pair = x.get("shots") if isinstance(x.get("shots"), list) else []
        a, b = (shots.get(_int(pair[0])), shots.get(_int(pair[-1]))) if pair else (None, None)
        when = f" · {a['start']:.0f}–{b['end']:.0f}s" if a and b else ""
        pic = (f"<img loading=lazy src='{esc(up((a.get('frames') or {}).get('mid')))}' alt='' data-shot={a['index']} data-to={b['index']}>"
               if a and b else "<span></span>")
        B.append(f"<div class=tl>{pic}<div><b>{esc(x.get('part'))}</b><span class=muted>镜 {ref(pair, _range(pair)) if a and b else esc(_range(x.get('shots')))}"
                 f"{when}</span><div>{esc(x.get('what'))}</div></div></div>")

    # ---- what backs it up: one line each ----
    F = []
    hook = (_paras(r["hook"]) if r.get("hook") else "")
    if r.get("tropes"):
        hook += ("<h3>套路</h3>" if hook else "") + _list(r["tropes"], lambda x: (
            f"<b>{esc(x.get('name'))}</b> <span class=muted>镜 {_refs(x.get('shots'))}</span>：{esc(x.get('how'))}"))
    F.append(fold("开头钩子和套路" if r.get("hook") and r.get("tropes") else "套路" if r.get("tropes") else "开头钩子",
                  "、".join(str(x.get("name")) for x in r.get("tropes") or []) or "前几秒怎么留人", hook))

    E = [_paras(r["engine"])] if r.get("engine") else []
    pr = m["pressure"]
    if m["feels"]:
        E += ["<p class=muted>每种情绪占的秒数（爽、虐、甜、笑、惊、燃是打到观众的；憋、悬是在攒）</p>",
              bar_chart([(k, v["seconds"], f"{k}：{v['n']} 段，共 {v['seconds']}s") for k, v in m["feels"].items()], "s")]
    if pr["pairs"]:
        E.append("<div class=tw><table><tr><th>开始憋</th><th>放（爽）</th><th>等了</th><th>其中憋的镜头</th></tr>"
                 + "".join(f"<tr><td class=n>{x['press']}s</td><td class=n>{x['release']}s</td><td class=n>{x['hold']}s</td>"
                           f"<td class=n>{x['pressed']}s</td></tr>" for x in pr["pairs"]) + "</table></div>")
    if pr["owed"]:
        E.append(f"<p class=muted>有 {pr['owed']} 秒的憋到片尾也没放，留给后面的集。</p>")
    if m["nodes"]:
        E.append("<div class=tw><table><tr><th>时间</th><th>情绪</th><th>镜头</th><th>做成了什么</th></tr>"
                 + "".join(f"<tr><td class=n>{n['t']:.1f}s</td><td class=n>{esc(n['feel'])}</td><td class=n>{ref(n['shots'], span(n['shots']))}</td>"
                           f"<td>{esc(n['what'])}</td></tr>" for n in m["nodes"]) + "</table></div>")
    mix = "、".join(f"{k} {v['seconds']:g}s" for k, v in list(m["feels"].items())[:3])
    F.append(fold("情绪账", f"{m['peaks']} 个节点" + (f" · {mix}" if mix else ""), "".join(E)))

    if rep.get("info_gaps"):
        F.append(fold("信息差", f"{len(rep['info_gaps'])} 条 · 谁知道、谁不知道、哪一镜揭开",
                      (_paras(r["info_gap"]) if r.get("info_gap") else "")
                      + "<div class=tw><table><tr><th>是什么</th><th>谁知道</th><th>谁不知道</th><th>揭开</th><th>怎么做戏</th></tr>"
                      + "".join(f"<tr><td>{esc(g.get('what'))}</td><td>{esc(who(g.get('knows')))}</td><td>{esc(who(g.get('blind')))}</td>"
                                f"<td class=n>{'镜 ' + ref(g['to']) if g.get('to') else '<span class=warn>没揭开</span>'}</td>"
                                f"<td>{esc(g.get('play'))}</td></tr>" for g in rep["info_gaps"]) + "</table></div>"))

    # one table of people: screen time is a column of it, not a chart of its own
    read = {x.get("id"): x for x in r.get("characters") or []}
    top = max(m["screen_time"].values(), default=0)
    P = ""
    if m["screen_time"]:
        P += ("<div class=tw><table><tr><th>角色</th><th>出镜</th><th>台词</th>" + ("<th>人设</th><th>作用</th><th>记忆点</th>" if read else "") + "</tr>"
              + "".join(f"<tr><td class=n><b>{esc(who(k))}</b></td><td class=n>{meter(v, top, f'{v:g}s')}</td>"
                        f"<td class=n>{m['talk_lines'].get(k, 0)} 句 / {m['talk_chars'].get(k, 0)} 字</td>"
                        + (f"<td>{esc((read.get(k) or {}).get('persona'))}</td><td>{esc((read.get(k) or {}).get('function'))}</td>"
                           f"<td>{esc((read.get(k) or {}).get('memorable'))}</td>" if read else "") + "</tr>"
                        for k, v in m["screen_time"].items() if v or k in read) + "</table></div>")
    rel = {tuple(sorted((x.get("a", ""), x.get("b", "")))): x for x in r.get("relations") or []}
    duels = [p for p in m["pairs"] if p["exchanges"] or (p["a"], p["b"]) in rel]  # sharing a frame in silence is no scene
    if duels:
        P += ("<h3>对手戏</h3><div class=tw><table><tr><th>两人</th><th>同框</th><th>你来我往</th><th>关系和张力</th></tr>"
              + "".join(f"<tr><td>{esc(who(p['a']))} × {esc(who(p['b']))}</td><td class=n>{p['seconds']}s</td><td class=n>{p['exchanges']} 次</td>"
                        f"<td>{esc((rel.get((p['a'], p['b'])) or {}).get('relation'))} {esc((rel.get((p['a'], p['b'])) or {}).get('tension'))}</td></tr>"
                        for p in duels) + "</table></div>")
    F.append(fold("人物和对手戏", "、".join(who(k) for k in list(m["screen_time"])[:4]) + (" 等" if len(m["screen_time"]) > 4 else ""), P))

    kv = lambda d, k=4: "、".join(f"{a} {b}" for a, b in list(d.items())[:k]) if isinstance(d, dict) else str(d or "")
    scenes = ((story or {}).get("breakdown") or {}).get("scenes") or []
    places = list(dict.fromkeys(re.sub(r"[（(][^）)]*[）)]", "", str(x.get("location") or "")).strip() for x in scenes))
    looks = sum(len(x.get("looks") or {}) for x in scenes)
    F.append(fold("制作量", f"{len(places) or m['scenes']} 个场景 · {len(m['screen_time'])} 个角色 · {m['shots']} 镜", "".join([
        "<ul>", (f"<li><b>场景</b> {len(places)} 个：{esc('、'.join(p for p in places if p))}</li>" if places else ""),
        f"<li><b>演员</b> {len(m['screen_time'])} 个有戏份的角色" + (f"，分场造型共 {looks} 套次" if looks else "") + "</li>",
        f"<li><b>镜头</b> {m['shots']} 个，平均 {m['avg_shot']}s，每分钟切 {m['cuts_per_min']} 刀；景别：{esc(kv(m['shot_sizes']))}；运镜：{esc(kv(m['camera_moves'], 3))}</li>",
        f"<li><b>台词</b> 每秒 {m['talk_per_sec']} 字；{esc(kv(m['talk_modes']))}</li>",
        (f"<li><b>后期</b> 特效 {m['vfx_shots']} 镜、花字 {m['overlays']} 处</li>" if m["vfx_shots"] or m["overlays"] else ""), "</ul>"])))
    F = [x for x in F if x]
    if F:
        B.append("<div class=more><p class=muted>依据和细节，点开看</p>" + "".join(F) + "</div>")
    return page(f"爆款拆解 {rep['title'] or ''}".strip(), B, player(story, video))


def _int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _refs(xs) -> str:
    """Shots a trope sits on, each one to click: 2、6、45."""
    if not isinstance(xs, list) or any(_int(i) is None for i in xs):
        return esc(_range(xs, False))
    return "、".join(ref(int(i)) for i in xs)
