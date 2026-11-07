"""Hit-formula report of one episode, for writers and producers: why it holds people, what the formula is, how to reuse it.

The numbers are measured from the breakdown by code (pacing curve, payoff gaps, screen time, who plays against whom,
talk density); one model call reads them with the beats and writes the judgement (logline, hook, structure, tropes,
reusable formula), always pointing at shots and seconds. Writes report.json / report.md / report.html; never touches
story.json, so it does not unlock a reviewed breakdown.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from .breakdown import lock_state
from .genre import format_of
from .page import bar_chart, esc, line_chart, page
from .prompts import REPORT_PROMPT
from .subtitles import MODE_CN, line_mode, unique_lines
from .vlm import VLM

LOG = logging.getLogger(__name__)

# how much a shot of each dramatic role lifts the curve; must / high shots lift it further
WEIGHT = {"hook": 3, "setup": 1, "conflict": 3, "reversal": 4, "reveal": 4, "climax": 5, "payoff": 5, "cliffhanger": 4,
          "reaction": 2, "transition": 0.5, "normal": 1}
BOOST = {"must": 1.5, "high": 0.5, "normal": 0}
PEAKS = ("reversal", "reveal", "climax", "payoff")  # the beats an audience stays for
DEAD_GAP = 30.0  # seconds without any of them: where people swipe away


def _main(story: dict) -> list[dict]:
    return [c for c in story.get("characters") or [] if c.get("importance") != "extra" and "crowd" not in c["id"]]


def _lines(story: dict) -> list[dict]:
    return unique_lines(story)


def intensity(shot: dict) -> float:
    k = shot.get("key") or {}
    return WEIGHT.get(k.get("role"), 1) + BOOST.get(k.get("priority"), 0)


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
    peaks = [b for b in beats if b["type"] in PEAKS]
    stops = [0.0] + [b["t"] for b in peaks] + [dur]
    gaps = [{"from": round(a, 1), "to": round(b, 1), "len": round(b - a, 1)} for a, b in zip(stops, stops[1:])]
    first = lambda types: next((b["t"] for b in beats if b["type"] in types), None)

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
        "first_conflict": first(("conflict",)), "first_peak": first(PEAKS), "peaks": len(peaks),
        "cliffhanger": any(b["type"] == "cliffhanger" for b in beats),
        # the stretch before the first peak is the setup (measured as first_peak), not a lull; with no peak at all
        # the whole episode is one
        "peak_gaps": gaps, "dead_zones": [g for g in (gaps[1:] if peaks else gaps) if g["len"] > DEAD_GAP],
        "longest_gap": max((g["len"] for g in gaps[1:]), default=None) if peaks else None,
        "avg_gap": round(sum(g["len"] for g in gaps[1:-1]) / len(gaps[1:-1]), 1) if len(gaps) > 2 else None,
        "opening": {"shots": [s["index"] for s in opening],
                    "lines": [x["text"] for x in lines if x["start"] < 3.0]},
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
        f"第一个冲突在 {m['first_conflict']}s，第一个爽点/反转在 {m['first_peak']}s，爽点和反转共 {m['peaks']} 个，"
        f"{'有' if m['cliffhanger'] else '没有'}结尾钩子",
        f"爽点之间的间隔：{gaps}；两个爽点之间平均 {m['avg_gap']}s，最长空档（含最后一个爽点到片尾）{m['longest_gap']}s"
        f"（业内常说每 15–30 秒一个情绪节点、30 秒内出第一个爽点）",
        f"开头 3 秒：镜头 {m['opening']['shots']}，台词 {m['opening']['lines']}",
        f"出镜秒数 {m['screen_time']}；台词字数 {m['talk_chars']}",
        "对手戏（同框秒数/你来我往的台词次数）：" + "；".join(f"{p['a']}×{p['b']} {p['seconds']}s/{p['exchanges']}次" for p in m["pairs"]),
        f"景别 {m['shot_sizes']}；运镜 {m['camera_moves']}；画面动法 {m['motion']}",
        f"多宫格 {m['panels']} 镜，特效 {m['vfx_shots']} 镜，漫符 {m['manga_marks']} 镜，对话气泡 {m['bubbles']} 处，花字 {m['overlays']} 处",
    ])


def _prompt(story: dict, m: dict, fmt: dict) -> str:
    from .breakdown import ROLE_CN, PRIORITY_CN, _said
    main = {c["id"] for c in _main(story)}
    cast = "\n".join(f"{c['id']}|{c.get('role', '')}|{c.get('importance', '')}|{m['screen_time'].get(c['id'], 0)}|"
                     f"{m['talk_chars'].get(c['id'], 0)}" for c in story.get("characters") or [] if c["id"] in main)
    scenes = "\n".join(f"{sc['id']} 镜{sc['shots'][0]}-{sc['shots'][1]} {sc.get('location', '')} {sc.get('time', '')}"
                       for sc in (story.get("breakdown") or {}).get("scenes") or [])
    beats = "\n".join(f"{b['type']}|{b['t']}|{b['shots']}|{b.get('what', '')}" for b in beat_times(story))
    rows = []
    for s in story.get("shots") or []:
        k = s.get("key") or {}
        talk = " / ".join(f"{x.get('speaker', '?')}{_said(x)}：{x['text']}" for x in s.get("subtitles") or [] if not x.get("card"))
        rows.append(f"{s['index']}|{s['start']:.1f}|{s['duration']:.1f}|{ROLE_CN.get(k.get('role'), '')}|"
                    f"{PRIORITY_CN.get(k.get('priority'), '')}|{k.get('function') or (s.get('analysis') or {}).get('action_summary', '')}|{talk}")
    return REPORT_PROMPT.format(
        format=fmt["cn"], format_why=fmt["why"], lens="\n".join(f"- {x}" for x in fmt["lens"]),
        summary=story.get("summary", ""), cast=cast or "（无）", scenes=scenes or "（无）", beats=beats or "（无）",
        table="\n".join(rows), metrics=_metrics_text(m))


def build(story: dict, vlm: VLM | None) -> dict:
    """The report as data. Without a model only the measured part is filled."""
    m = metrics(story)
    fmt = format_of(story)
    rep = {"schema": "rea_toon.report.v1", "title": (story.get("source") or {}).get("title") or "",
           "summary": story.get("summary", ""), "format": fmt, "metrics": m, "curve": curve(story),
           "beats": beat_times(story), "locked": lock_state(story) == "locked"}
    if vlm is not None:
        try:
            rep["read"] = vlm.ask_json(_prompt(story, m, fmt), [], max_tokens=8000, timeout=300)
        except Exception as exc:  # the measured part still stands
            LOG.warning("爆款拆解（模型解读）失败：%s", exc)
            rep["read_error"] = str(exc)
    return rep


def report(work: Path, vlm: VLM | None = None, use_vlm: bool = True) -> dict:
    story = json.loads((work / "story.json").read_text(encoding="utf-8"))
    if not (story.get("breakdown") or {}).get("beats"):
        raise RuntimeError(f"还没有戏剧节拍：先 rea-toon breakdown {work}")
    if use_vlm and vlm is None:
        vlm = VLM(cache_dir=work / "analysis" / "vlm_cache")
    rep = build(story, vlm if use_vlm else None)
    (work / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (work / "report.md").write_text(to_md(rep), encoding="utf-8")
    (work / "report.html").write_text(to_html(rep, story), encoding="utf-8")
    return rep


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


def to_md(rep: dict) -> str:
    from .breakdown import ROLE_CN
    m, r = rep["metrics"], rep.get("read") or {}
    tags = r.get("tags") or {}
    L = [f"# 爆款拆解：{rep['title'] or 'source'}", "", f"**类型**：{rep['format']['cn']}（{rep['format']['why']}）", ""]
    if r.get("logline"):
        L += [f"**一句话卖点**：{r['logline']}", ""]
    if tags:
        L += [f"**题材**：{'、'.join(tags.get('themes') or [])}　**受众**：{tags.get('audience', '')}　"
              f"**爽感**：{'、'.join(tags.get('pleasure') or [])}", ""]
    L += ["## 数据", "", f"- 时长 {m['duration']}s · {m['shots']} 镜 · {m['scenes']} 场 · 平均镜长 {m['avg_shot']}s · 每分钟 {m['cuts_per_min']} 刀",
          f"- 台词每秒 {m['talk_per_sec']} 字 · 第一个冲突 {_sec(m['first_conflict'])} · 第一个爽点 {_sec(m['first_peak'])} · "
          f"爽点/反转 {m['peaks']} 个 · {'有' if m['cliffhanger'] else '没有'}结尾钩子"]
    if m.get("avg_gap") is not None:
        L.append(f"- 爽点间隔：平均 {m['avg_gap']}s · 最长 {m['longest_gap']}s")
    if m["first_peak"] is not None and m["first_peak"] > DEAD_GAP:
        L.append(f"- ⚠️ 第一个爽点在 {m['first_peak']:.1f}s，晚于 {DEAD_GAP:.0f}s")
    L += [f"- ⚠️ {g['from']}–{g['to']}s 连续 {g['len']}s 没有爽点或反转" for g in m["dead_zones"]] + [""]
    if r.get("hook"):
        L += ["## 开头钩子", "", r["hook"], ""]
    if r.get("structure"):
        L += ["## 结构", ""] + [f"- **{x.get('part')}**（镜 {x.get('shots')}）：{x.get('what')}" for x in r["structure"]] + [""]
    L += ["## 戏剧节拍", ""] + [f"- {b['t']:.1f}s **{ROLE_CN.get(b['type'], b['type'])}** 镜 {span(b['shots'])}：{b.get('what', '')}"
                              for b in rep["beats"]] + [""]
    if r.get("tropes"):
        L += ["## 套路", ""] + [f"- **{x.get('name')}**（镜 {x.get('shots')}）：{x.get('how')}" for x in r["tropes"]] + [""]
    if r.get("characters"):
        L += ["## 人设", ""] + [f"- **{x.get('id')}** {x.get('persona')}：{x.get('function')}；记忆点：{x.get('memorable')}"
                              for x in r["characters"]] + [""]
    if r.get("relations"):
        L += ["## 关系和张力", ""] + [f"- {x.get('a')} × {x.get('b')}：{x.get('relation')}；{x.get('tension')}" for x in r["relations"]] + [""]
    for key, name in (("why_it_works", "为什么留得住人"), ("weaknesses", "短板"), ("remix", "改编方向")):
        if r.get(key):
            L += [f"## {name}", ""] + [f"- {x}" for x in r[key]] + [""]
    if r.get("formula"):
        L += ["## 可复用公式", ""] + [f"{i}. **{x.get('step')}**（约 {float(x.get('share') or 0):.0%}）：{x.get('what')}"
                                   for i, x in enumerate(r["formula"], 1)] + [""]
    L += ["## 这类剧要看的", ""] + [f"- {x}" for x in rep["format"]["lens"]]
    return "\n".join(L) + "\n"


def _list(items, fmt) -> str:
    return "<ul>" + "".join(f"<li>{fmt(x)}</li>" for x in items) + "</ul>" if items else ""


def to_html(rep: dict, story: dict | None = None) -> str:
    from .breakdown import ROLE_CN
    m, r = rep["metrics"], rep.get("read") or {}
    tags = r.get("tags") or {}
    B = [f"<h1>爆款拆解：{esc(rep['title'] or 'source')}</h1>",
         f"<p><span class=state>{esc(rep['format']['cn'])}</span> <span class=muted>{esc(rep['format']['why'])}"
         f"{'' if rep.get('locked') else ' · 拆解还没审核锁定，报告基于未审版本'}</span></p>"]
    if r.get("logline"):
        B.append(f"<p style='font-size:17px'><b>{esc(r['logline'])}</b></p>")
    chips = [*(tags.get("themes") or []), tags.get("audience"), *(tags.get("pleasure") or [])]
    if any(chips):
        B.append("<p>" + "".join(f"<span class=chip>{esc(c)}</span>" for c in chips if c) + "</p>")
    if rep.get("read_error"):
        B.append(f"<p class=err>模型解读失败（只有数据部分）：{esc(rep['read_error'])}；重跑 rea-toon report</p>")
    stats = [("时长", f"{m['duration']:.0f}s"), ("镜头", m["shots"]), ("平均镜长", f"{m['avg_shot']}s"),
             ("每分钟切镜", m["cuts_per_min"]), ("台词 字/秒", m["talk_per_sec"]), ("第一个爽点", _sec(m["first_peak"])),
             ("爽点/反转", m["peaks"]), ("爽点平均间隔", _sec(m.get("avg_gap"))), ("结尾钩子", "有" if m["cliffhanger"] else "没有")]
    B.append("<div class=stats>" + "".join(f"<div class=stat><b>{esc(v)}</b><span>{esc(k)}</span></div>" for k, v in stats) + "</div>")
    if r.get("hook"):
        B += ["<h2>开头钩子</h2>", f"<p>{esc(r['hook'])}</p>"]

    cv = rep.get("curve") or []
    if cv:
        pts = [((c["t0"] + c["t1"]) / 2, c["intensity"]) for c in cv]
        tips = [f"{c['t0']:.0f}–{c['t1']:.0f}s 强度 {c['intensity']} · 切 {c['cuts']} 刀 · 每秒 {c['talk']} 字 · 镜 {c['shots'][0]}–{c['shots'][-1]}"
                for c in cv]
        at = lambda t: _interp(pts, t)
        marks = [(b["t"], at(b["t"]), f"{b['t']:.1f}s {ROLE_CN.get(b['type'], b['type'])}：{b.get('what', '')}")
                 for b in rep["beats"] if b["type"] in PEAKS + ("hook", "cliffhanger")]
        B += ["<h2>节奏曲线</h2>", "<p class=muted>按镜头的戏剧作用算的情绪强度（爽点/高潮 5，反转/揭露 4，冲突/钩子 3，必保镜头再加）；"
              "圆点是钩子、反转、爽点和结尾钩子，悬停看细节。</p>",
              line_chart(pts, marks, "情绪强度随时间", max(6.5, max(p[1] for p in pts)), tips)]
        B += ["<p class=muted>每段切镜数</p>",
              line_chart([((c["t0"] + c["t1"]) / 2, c["cuts"]) for c in cv], [], "每段切镜数",
                         max(4, max(c["cuts"] for c in cv)), [f"{c['t0']:.0f}–{c['t1']:.0f}s 切 {c['cuts']} 刀" for c in cv], h=120)]
    if m["first_peak"] is not None and m["first_peak"] > DEAD_GAP:
        B.append(f"<p class=warn>第一个爽点在 {m['first_peak']:.1f}s，晚于 {DEAD_GAP:.0f}s：开头可能留不住人</p>")
    if m["dead_zones"]:
        B.append(_list(m["dead_zones"], lambda g: f"<span class=warn>{g['from']}–{g['to']}s 连续 {g['len']}s 没有爽点或反转</span>"))

    if r.get("structure"):
        B += ["<h2>结构</h2>", _list(r["structure"], lambda x: f"<b>{esc(x.get('part'))}</b> <span class=muted>镜 {esc(x.get('shots'))}</span>：{esc(x.get('what'))}")]
    B += ["<h2>戏剧节拍</h2><div class=tw><table><tr><th>时间</th><th>类型</th><th>镜头</th><th>发生了什么</th></tr>"
          + "".join(f"<tr><td class=n>{b['t']:.1f}s</td><td class=n>{esc(ROLE_CN.get(b['type'], b['type']))}</td><td class=n>{esc(span(b['shots']))}</td>"
                    f"<td>{esc(b.get('what'))}</td></tr>" for b in rep["beats"]) + "</table></div>"]
    if r.get("tropes"):
        B += ["<h2>套路</h2>", _list(r["tropes"], lambda x: f"<b>{esc(x.get('name'))}</b> <span class=muted>镜 {esc(x.get('shots'))}</span>：{esc(x.get('how'))}")]

    B.append("<h2>人物</h2>")
    names = {c["id"]: c.get("role", c["id"]) for c in (story or {}).get("characters") or []}
    if m["screen_time"]:
        B += ["<p class=muted>出镜秒数</p>", bar_chart([(f"{k} {names.get(k, '')}", v, f"{k}：出镜 {v}s，台词 {m['talk_chars'].get(k, 0)} 字")
                                                    for k, v in m["screen_time"].items()], "s")]
    if r.get("characters"):
        B.append("<div class=tw><table><tr><th>角色</th><th>人设</th><th>作用</th><th>记忆点</th><th>台词</th></tr>"
                 + "".join(f"<tr><td>{esc(x.get('id'))}</td><td>{esc(x.get('persona'))}</td><td>{esc(x.get('function'))}</td>"
                           f"<td>{esc(x.get('memorable'))}</td><td>{m['talk_lines'].get(x.get('id'), 0)} 句 / {m['talk_chars'].get(x.get('id'), 0)} 字</td></tr>"
                           for x in r["characters"]) + "</table></div>")
    if m["pairs"] or r.get("relations"):
        rel = {tuple(sorted((x.get("a", ""), x.get("b", "")))): x for x in r.get("relations") or []}
        B.append("<h2>对手戏</h2><div class=tw><table><tr><th>两人</th><th>同框</th><th>你来我往</th><th>关系和张力</th></tr>"
                 + "".join(f"<tr><td>{esc(p['a'])} × {esc(p['b'])}</td><td>{p['seconds']}s</td><td>{p['exchanges']} 次</td>"
                           f"<td>{esc((rel.get((p['a'], p['b'])) or {}).get('relation'))} {esc((rel.get((p['a'], p['b'])) or {}).get('tension'))}</td></tr>"
                           for p in m["pairs"]) + "</table></div>")
    for key, name in (("why_it_works", "为什么留得住人"), ("weaknesses", "短板")):
        if r.get(key):
            B += [f"<h2>{name}</h2>", _list(r[key], esc)]
    if r.get("formula"):
        B.append("<h2>可复用公式</h2><div class=tw><table><tr><th>#</th><th>步骤</th><th>占比</th><th>要做到</th></tr>"
                 + "".join(f"<tr><td>{i}</td><td><b>{esc(x.get('step'))}</b></td><td>{float(x.get('share') or 0):.0%}</td><td>{esc(x.get('what'))}</td></tr>"
                           for i, x in enumerate(r["formula"], 1)) + "</table></div>")
    if r.get("remix"):
        B += ["<h2>改编方向</h2>", _list(r["remix"], esc)]
    B += [f"<h2>{esc(rep['format']['cn'])}要看的</h2>", _list(rep["format"]["lens"], esc),
          "<details><summary>镜头语言数据</summary>"
          f"<p>景别：{esc(m['shot_sizes'])}<br>运镜：{esc(m['camera_moves'])}<br>画面动法：{esc(m['motion'])}<br>"
          f"台词说法：{esc(m['talk_modes'])}<br>多宫格 {m['panels']} 镜 · 特效 {m['vfx_shots']} 镜 · 漫符 {m['manga_marks']} 镜 · "
          f"气泡 {m['bubbles']} 处 · 花字 {m['overlays']} 处</p></details>",
          "<p class=muted>逐镜细节见 breakdown.html。</p>"]
    return page(f"爆款拆解 {rep['title'] or ''}".strip(), B)
