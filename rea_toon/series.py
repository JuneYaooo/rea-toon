"""A whole series: every episode broken down on its own, then read together.

    work/<剧名>/
      series.json          episodes, the series cast (one id per person across episodes), the cross-episode read
      series.html / .md    the report: arcs, setups and payoffs, how each ending hooks the next episode, paywall spots
      ep01/ ep02/ …        ordinary work dirs (story.json, breakdown.html, report.html)

Episodes are analysed one by one and skipped once done, so a long series can be resumed after any failure. Each
episode keeps its own character ids (its story.json may already be reviewed and locked); the series cast maps them.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .breakdown import lock_state
from .genre import format_of
from .media import font_path
from .page import bar_chart, esc, page
from .prompts import SERIES_CAST_PROMPT, SERIES_PROMPT
from .report import metrics, span
from .vlm import VLM

LOG = logging.getLogger(__name__)
VIDEO = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".flv")


def _natural(p: Path):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p.name)]


def expand(sources: list[str]) -> list[str]:
    """A folder stands for its videos in episode order (第2集 before 第10集), a Douyin collection link for its
    episodes in order; anything else is one episode."""
    from . import douyin
    out = []
    for s in sources:
        d = Path(s).expanduser()
        if douyin.mix_id(s):
            out += douyin.episodes(douyin.mix_id(s))
        elif d.is_dir():
            out += [str(p) for p in sorted((p for p in d.iterdir() if p.suffix.lower() in VIDEO), key=_natural)]
        else:
            out.append(s)
    return out


def load(root: Path) -> dict:
    p = root / "series.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"schema": "rea_toon.series.v1", "title": root.name, "episodes": []}


def save(root: Path, ser: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "series.json").write_text(json.dumps(ser, ensure_ascii=False, indent=1), encoding="utf-8")


def _same_source(src: str) -> str:
    p = Path(src).expanduser()
    return str(p.resolve()) if p.exists() else src.strip()


def add(root: Path, sources: list[str], title: str | None = None, first: int | None = None) -> dict:
    """Ingest new episodes as ep01, ep02, … after the ones already there. A source already in the series is skipped,
    so re-running the first command (with --source) resumes instead of adding every episode again."""
    from .ingest import ingest
    ser = load(root)
    if title:
        ser["title"] = title
    have = {_same_source(e["source"]) for e in ser["episodes"]}
    todo = expand(sources)
    for src in todo[:first] if first else todo:
        if _same_source(src) in have:
            continue
        ep = max((e["ep"] for e in ser["episodes"]), default=0) + 1
        d = f"ep{ep:02d}"
        LOG.info("第 %d 集 ← %s", ep, src[:80])
        try:
            meta = ingest(src, root / d)
        except Exception as exc:  # one bad link does not stop the rest; it gets the next number when it works
            LOG.warning("导入失败（跳过，重跑再试）：%s：%s", src[:80], exc)
            continue
        have.add(_same_source(src))
        ser["episodes"].append({"ep": ep, "dir": d, "source": src, "title": meta.get("title") or ""})
        save(root, ser)
    return ser


def _story(root: Path, e: dict) -> dict | None:
    p = root / e["dir"] / "story.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def prior_of(root: Path, ser: dict, ep: int, recap_eps: int = 3) -> dict | None:
    """What the episodes before `ep` settled, for analysing it: the last few plots and the cast so far. A person seen
    in several episodes is listed once (by the cross-episode id when series.json has one; its guessed name is left out,
    the plots spell names as the subtitles do), with the role from the
    episode that introduced them (later episodes guessed it with less to go by) and the latest look."""
    seen = {m: c for c in ser.get("characters") or [] for m in c.get("members") or []}
    recap, cast = [], {}
    for e in sorted((e for e in ser["episodes"] if e["ep"] < ep), key=lambda e: e["ep"]):
        story = _story(root, e)
        if not story or not story.get("summary"):
            continue
        recap.append(f"第 {e['ep']} 集：{story['summary']}")
        for c in story.get("characters") or []:
            if c.get("importance") == "extra" or "crowd" in str(c.get("id")):
                continue
            u = seen.get(_label(e["ep"], c["id"])) or {}
            cid = u.get("id") or c["id"]
            row = cast.setdefault(cid, {"role": c.get("role", "")})
            row["look"] = str(c.get("appearance", ""))[:80]
    if not cast:
        return None
    lines = [f"- {cid}：{r['role']}；外貌：{r['look']}" for cid, r in cast.items()]
    return {"ep": ep, "recap": "\n".join(recap[-recap_eps:]), "cast": "\n".join(lines)}


def analyze_all(root: Path, workers: int = 6, use_vlm: bool = True) -> list[dict]:
    """Bring every episode up to analysis + breakdown + report; skip what is done; one failure does not stop the rest."""
    from .analyze import analyze
    from .breakdown import breakdown
    from .report import report
    ser, failed = load(root), []
    for e in ser["episodes"]:
        w = root / e["dir"]
        try:
            story = _story(root, e)
            if story is None or not any(s.get("analysis") for s in story.get("shots") or []):
                LOG.info("第 %d 集：拆剧本 …", e["ep"])
                story = analyze(w, use_vlm=use_vlm, workers=workers, prior=prior_of(root, ser, e["ep"]))
            if use_vlm and not (story.get("breakdown") or {}).get("beats"):
                LOG.info("第 %d 集：深度拆解 …", e["ep"])
                breakdown(w, workers=workers)
            rp = w / "report.json"  # redo a report older than its (re-broken-down) episode
            if use_vlm and (not rp.is_file() or rp.stat().st_mtime < (w / "story.json").stat().st_mtime):
                LOG.info("第 %d 集：爆款拆解 …", e["ep"])
                report(w)
        except Exception as exc:
            LOG.warning("第 %d 集失败（重跑同一命令续跑）：%s", e["ep"], exc)
            failed.append({"ep": e["ep"], "error": str(exc)})
    return failed


# ---- one id per person across episodes ----

def _label(ep: int, cid: str) -> str:
    return f"E{ep}:{cid}"


def cast_entries(root: Path, ser: dict) -> list[dict]:
    from .analyze import _names, _portraits
    out = []
    for e in ser["episodes"]:
        story = _story(root, e)
        if not story:
            continue
        for c in story.get("characters") or []:
            if c.get("importance") == "extra" or "crowd" in c["id"]:
                continue
            pic = _portraits(story, root / e["dir"], c["id"], 1)
            out.append({"label": _label(e["ep"], c["id"]), "ep": e["ep"], "id": c["id"], "role": c.get("role", ""),
                        "importance": c.get("importance", ""), "shots": len(c.get("shots") or []),
                        "appearance": str(c.get("appearance", ""))[:120], "names": sorted(_names(c) - {c["id"]}),
                        "pic": str(pic[0]) if pic else ""})
    return out


def portrait_sheets(entries: list[dict], out_dir: Path, per_sheet: int = 16, cols: int = 4) -> list[Path]:
    font = ImageFont.truetype(font_path(), 22) if font_path() else ImageFont.load_default()
    have = [x for x in entries if x["pic"]]
    paths = []
    for k in range(0, len(have), per_sheet):
        group = have[k:k + per_sheet]
        tw = th = 260
        sheet = Image.new("RGB", (cols * tw, ((len(group) + cols - 1) // cols) * th), "black")
        for i, x in enumerate(group):
            im = Image.open(x["pic"]).convert("RGB")
            side = min(im.size)  # centre square: the face is usually mid-frame
            im = im.crop(((im.width - side) // 2, (im.height - side) // 2, (im.width + side) // 2, (im.height + side) // 2))
            im = im.resize((tw, th))
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, tw, 30], fill="white")
            d.text((6, 3), x["label"], fill="black", font=font)
            sheet.paste(im, ((i % cols) * tw, (i // cols) * th))
        p = out_dir / f"series_cast_{k // per_sheet + 1}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(p, quality=88)
        paths.append(p)
    return paths


def _clean_groups(groups: list[dict], entries: list[dict]) -> list[dict]:
    """Every label in exactly one character; labels the model dropped stand alone; ids unique."""
    by_label = {x["label"]: x for x in entries}
    seen, out = set(), []
    for g in groups:
        members = [m for m in g.get("members") or [] if m in by_label and m not in seen]
        if not members:
            continue
        seen |= set(members)
        out.append({"id": str(g.get("id") or by_label[members[0]]["id"]).strip().lower().replace(" ", "_"),
                    "name": g.get("name") or "", "role": g.get("role") or by_label[members[0]]["role"], "members": members})
    for x in entries:
        if x["label"] not in seen:
            out.append({"id": x["id"], "name": "", "role": x["role"], "members": [x["label"]]})
    taken: dict[str, int] = defaultdict(int)
    for g in out:
        taken[g["id"]] += 1
        if taken[g["id"]] > 1:
            g["id"] = f"{g['id']}_{taken[g['id']]}"
    return out


def unify(root: Path, ser: dict, vlm: VLM | None) -> list[dict]:
    entries = cast_entries(root, ser)
    if not entries:
        return []
    if vlm is None:  # same id in every episode = same person; the model pass is what catches renamed ids
        groups = defaultdict(list)
        for x in entries:
            groups[x["id"]].append(x["label"])
        return _clean_groups([{"id": k, "members": v} for k, v in groups.items()], entries)
    rows = "\n".join(f"{x['label']}|{x['role']}|{x['importance']}|{x['shots']}|{x['appearance']}|{'、'.join(x['names']) or '-'}"
                     for x in entries)
    sheets = portrait_sheets(entries, root / "analysis")
    try:
        r = vlm.ask_json(SERIES_CAST_PROMPT.format(rows=rows), sheets, max_tokens=8000, timeout=300)
    except Exception as exc:
        LOG.warning("跨集角色合并失败，先按 id 合并：%s", exc)
        return unify(root, ser, None)
    groups = _clean_groups(r.get("characters") or [], entries)
    text = _script(root, ser)
    for g in groups:
        if g.get("name"):
            g["name"] = spelled(g["name"], text)
    return groups


def _script(root: Path, ser: dict) -> str:
    """Every plot summary and subtitle line of the series, to check a name against."""
    out = []
    for e in ser["episodes"]:
        story = _story(root, e) or {}
        out.append(str(story.get("summary", "")))
        out += [str(x.get("text", "")) for s in story.get("shots") or [] for x in s.get("subtitles") or []]
    return "\n".join(out)


def spelled(name: str, text: str) -> str:
    """The name as the show writes it: a model naming people from pictures often picks a homophone (江月初 for 姜月初).
    A name the script never has is swapped for the most frequent spelling in it that differs by one character."""
    import re
    if not name or name in text or len(name) < 2:
        return name
    seen = Counter()
    for i in range(len(name)):
        seen.update(re.findall(re.escape(name[:i]) + r"[\u4e00-\u9fff]" + re.escape(name[i + 1:]), text))
    return seen.most_common(1)[0][0] if seen else name


# ---- the series read ----

def episode_rows(root: Path, ser: dict) -> list[dict]:
    from .report import beat_times
    out = []
    for e in ser["episodes"]:
        story = _story(root, e)
        if not story or not (story.get("breakdown") or {}).get("beats"):  # not broken down yet: no numbers to read
            out.append({"ep": e["ep"], "dir": e["dir"], "missing": True})
            continue
        m = metrics(story)
        beats = beat_times(story)
        rp = root / e["dir"] / "report.json"
        read = (json.loads(rp.read_text(encoding="utf-8")).get("read") or {}) if rp.is_file() else {}
        ending = next((b for b in reversed(beats) if b["type"] == "cliffhanger"), beats[-1] if beats else None)
        out.append({"ep": e["ep"], "dir": e["dir"], "title": e.get("title") or (story.get("source") or {}).get("title") or "",
                    "summary": story.get("summary", ""), "duration": m["duration"], "shots": m["shots"], "peaks": m["peaks"],
                    "first_peak": m["first_peak"], "cliffhanger": m["cliffhanger"], "screen_time": m["screen_time"],
                    "opening": read.get("hook") or (beats[0].get("what", "") if beats else ""),
                    "ending": (ending or {}).get("what", ""), "logline": read.get("logline", ""),
                    "beats": [b["type"] for b in beats], "format": format_of(story)["cn"], "locked": lock_state(story) == "locked"})
    return out


def _screen(cast: list[dict], eps: list[dict]) -> dict[str, dict[int, float]]:
    """Seconds on screen per series character per episode."""
    by_ep = {r["ep"]: r for r in eps if not r.get("missing")}
    out: dict[str, dict[int, float]] = {}
    for c in cast:
        per = defaultdict(float)
        for m in c["members"]:
            ep, cid = m[1:].split(":", 1)
            per[int(ep)] += (by_ep.get(int(ep)) or {}).get("screen_time", {}).get(cid, 0)
        out[c["id"]] = dict(per)
    return out


def build(root: Path, use_vlm: bool = True) -> dict:
    ser = load(root)
    vlm = VLM(cache_dir=root / "analysis" / "vlm_cache") if use_vlm else None
    eps = episode_rows(root, ser)
    if ser.get("cast_fixed") and ser.get("characters"):  # hand-corrected mapping: keep it, add only new labels
        entries = cast_entries(root, ser)
        cast = _clean_groups([{k: c.get(k) for k in ("id", "name", "role", "members")} for c in ser["characters"]], entries)
    else:
        cast = unify(root, ser, vlm)
    screen = _screen(cast, eps)
    for c in cast:
        c["episodes"] = sorted(screen[c["id"]])
        c["seconds"] = round(sum(screen[c["id"]].values()), 1)
    cast.sort(key=lambda c: -c["seconds"])
    ser.update(characters=cast, rows=eps)
    done = [r for r in eps if not r.get("missing")]
    fmt = next((r["format"] for r in done), "")
    if vlm is not None and done:
        cast_txt = "\n".join(f"{c['id']}|{c['name']}|{c['role']}|{c['episodes']}|{c['seconds']}" for c in cast[:20])
        ep_txt = "\n".join(f"{r['ep']}|{r['duration']:.0f}s|{r['summary']}|{r['opening']}|{r['ending']}|{'→'.join(r['beats'])}" for r in done)
        met = "\n".join(f"{r['ep']}|{r['duration']:.0f}s|{r['shots']}|{r['peaks']}|{r['first_peak']}|{'有' if r['cliffhanger'] else '无'}"
                        for r in done)
        try:
            ser["read"] = vlm.ask_json(SERIES_PROMPT.format(title=ser["title"], n=len(done), format=fmt, cast=cast_txt,
                                                            episodes=ep_txt, metrics=met), [], max_tokens=12000, timeout=400)
            ser.pop("read_error", None)
        except Exception as exc:
            LOG.warning("跨集拆解失败：%s", exc)
            ser["read_error"] = str(exc)
    save(root, ser)
    (root / "series.md").write_text(to_md(ser), encoding="utf-8")
    (root / "series.html").write_text(to_html(ser), encoding="utf-8")
    return ser


# ---- pages ----

def to_md(ser: dict) -> str:
    r, rows = ser.get("read") or {}, [x for x in ser.get("rows") or [] if not x.get("missing")]
    L = [f"# 整部剧拆解：{ser['title']}", "", f"{len(rows)} 集，共 {sum(x['duration'] for x in rows) / 60:.1f} 分钟", ""]
    for k, name in (("logline", "一句话卖点"), ("main_conflict", "主冲突")):
        if r.get(k):
            L += [f"**{name}**：{r[k]}", ""]
    L += ["## 逐集", "", "| 集 | 时长 | 镜头 | 爽点 | 第一个爽点 | 结尾钩子 | 剧情 |", "|---|---|---|---|---|---|---|"]
    L += [f"| {x['ep']} | {x['duration']:.0f}s | {x['shots']} | {x['peaks']} | {x['first_peak'] if x['first_peak'] is not None else '—'} | "
          f"{x['ending'] if x['cliffhanger'] else '无'} | {x['summary']} |" for x in rows] + [""]
    L += ["## 角色", ""] + [f"- **{c['id']}** {c['name']} {c['role']}：第 {span(c['episodes'])} 集，出镜 {c['seconds']}s（{'、'.join(c['members'])}）"
                          for c in ser.get("characters") or []] + [""]
    if r.get("arcs"):
        L += ["## 人物弧光", ""] + [f"- **{a.get('id')}**：{a.get('arc')}；" + "；".join(f"第{t.get('ep')}集 {t.get('what')}" for t in a.get("turns") or [])
                                for a in r["arcs"]] + [""]
    if r.get("threads"):
        L += ["## 伏笔和回收", ""] + [f"- {t.get('what')}：第 {t.get('planted')} 集埋，" + (f"第 {t['paid']} 集收" if t.get("paid") else "**还没收**")
                                 + f"。{t.get('note', '')}" for t in r["threads"]] + [""]
    if r.get("links"):
        L += ["## 集与集的钩子", ""] + [f"- 第 {x.get('ep')} 集结尾：{x.get('end_hook')} → {x.get('picked_up')}" for x in r["links"]] + [""]
    if r.get("paywall"):
        L += ["## 付费卡点", ""] + [f"- 第 {x.get('ep')} 集：{x.get('why')}" for x in r["paywall"]] + [""]
    for k, name in (("pacing", "跨集节奏"), ("tropes", "反复使用的套路"), ("remix", "复用方向")):
        if r.get(k):
            L += [f"## {name}", ""] + [f"- {x}" for x in r[k]] + [""]
    if r.get("formula"):
        L += ["## 打法", ""] + [f"- **{x.get('stage')}**：{x.get('what')}" for x in r["formula"]] + [""]
    return "\n".join(L) + "\n"


def to_html(ser: dict) -> str:
    r = ser.get("read") or {}
    allrows = ser.get("rows") or []
    rows = [x for x in allrows if not x.get("missing")]
    total = sum(x["duration"] for x in rows)
    B = [f"<h1>整部剧拆解：{esc(ser['title'])}</h1>",
         f"<p><span class=state>{esc(rows[0]['format'] if rows else '')}</span> <span class=muted>{len(rows)} 集"
         + (f" · 还有 {len(allrows) - len(rows)} 集没拆完" if len(allrows) > len(rows) else "") + "</span></p>"]
    if r.get("logline"):
        B.append(f"<p style='font-size:17px'><b>{esc(r['logline'])}</b></p>")
    if r.get("main_conflict"):
        B.append(f"<p><b>主冲突</b>：{esc(r['main_conflict'])}</p>")
    if ser.get("read_error"):
        B.append(f"<p class=err>跨集解读失败（只有数据部分）：{esc(ser['read_error'])}；重跑 rea-toon series</p>")
    stats = [("集数", len(rows)), ("总时长", f"{total / 60:.1f} 分钟"), ("平均每集", f"{total / max(1, len(rows)):.0f}s"),
             ("有结尾钩子", f"{sum(1 for x in rows if x['cliffhanger'])}/{len(rows)} 集"),
             ("平均爽点", f"{sum(x['peaks'] for x in rows) / max(1, len(rows)):.1f}/集"), ("角色", len(ser.get("characters") or []))]
    B.append("<div class=stats>" + "".join(f"<div class=stat><b>{esc(v)}</b><span>{esc(k)}</span></div>" for k, v in stats) + "</div>")

    B.append("<h2>逐集</h2>")
    if rows:
        B += ["<p class=muted>每集爽点和反转数</p>",
              bar_chart([(f"第 {x['ep']} 集", x["peaks"], f"第 {x['ep']} 集：{x['peaks']} 个爽点/反转，第一个在 "
                          f"{x['first_peak'] if x['first_peak'] is not None else '—'}s") for x in rows], " 个")]
    B.append("<div class=tw><table><tr><th>集</th><th>时长</th><th>第一个爽点</th><th>结尾钩子</th><th>剧情</th><th></th></tr>"
             + "".join(f"<tr><td class=n>{x['ep']}</td><td class=n>{x['duration']:.0f}s</td>"
                       f"<td class=n>{'—' if x['first_peak'] is None else str(x['first_peak']) + 's'}</td>"
                       f"<td>{esc(x['ending']) if x['cliffhanger'] else '<span class=warn>没有</span>'}</td><td>{esc(x['summary'])}</td>"
                       f"<td class=n><a href='{esc(x['dir'])}/report.html'>报告</a> · <a href='{esc(x['dir'])}/breakdown.html'>逐镜</a></td></tr>"
                       if not x.get("missing") else
                       f"<tr><td>{x['ep']}</td><td colspan=5 class=muted>还没拆（rea-toon series 续跑）</td></tr>" for x in allrows)
             + "</table></div>")

    cast = ser.get("characters") or []
    if cast:
        B += ["<h2>角色</h2><p class=muted>全剧出镜秒数；同一个人在不同集里的 id 已合并</p>",
              bar_chart([(f"{c['id']} {c['name']}", c["seconds"], f"{c['role']}：第 {span(c['episodes'])} 集，{'、'.join(c['members'])}")
                         for c in cast[:12]], "s"),
              "<details><summary>每集出镜对照</summary><div class=tw><table><tr><th>角色</th><th>身份</th><th>出现</th><th>各集里的 id</th></tr>"
              + "".join(f"<tr><td>{esc(c['id'])} {esc(c['name'])}</td><td>{esc(c['role'])}</td><td class=n>第 {esc(span(c['episodes']))} 集</td>"
                        f"<td>{esc('、'.join(c['members']))}</td></tr>" for c in cast) + "</table></div></details>"]
    if r.get("arcs"):
        B.append("<h2>人物弧光</h2><ul>" + "".join(
            f"<li><b>{esc(a.get('id'))}</b>：{esc(a.get('arc'))}<br><span class=muted>"
            + esc("；".join(f"第{t.get('ep')}集 {t.get('what')}" for t in a.get("turns") or [])) + "</span></li>" for a in r["arcs"]) + "</ul>")
    if r.get("threads"):
        B.append("<h2>伏笔和回收</h2><div class=tw><table><tr><th>伏笔/悬念</th><th>埋</th><th>收</th><th>怎么埋、怎么收</th></tr>"
                 + "".join(f"<tr><td>{esc(t.get('what'))}</td><td class=n>第 {esc(t.get('planted'))} 集</td>"
                           f"<td class=n>{'第 ' + esc(t['paid']) + ' 集' if t.get('paid') else '<span class=warn>还没收</span>'}</td>"
                           f"<td>{esc(t.get('note'))}</td></tr>" for t in r["threads"]) + "</table></div>")
    if r.get("links"):
        B.append("<h2>集与集的钩子</h2><div class=tw><table><tr><th>集</th><th>结尾留的钩子</th><th>下一集怎么接</th></tr>"
                 + "".join(f"<tr><td class=n>{esc(x.get('ep'))}</td><td>{esc(x.get('end_hook'))}</td><td>{esc(x.get('picked_up'))}</td></tr>"
                           for x in r["links"]) + "</table></div>")
    if r.get("paywall"):
        B.append("<h2>付费卡点</h2><ul>" + "".join(f"<li><b>第 {esc(x.get('ep'))} 集</b>：{esc(x.get('why'))}</li>" for x in r["paywall"]) + "</ul>")
    for k, name in (("pacing", "跨集节奏"), ("tropes", "反复使用的套路")):
        if r.get(k):
            B.append(f"<h2>{name}</h2><ul>" + "".join(f"<li>{esc(x)}</li>" for x in r[k]) + "</ul>")
    if r.get("formula"):
        B.append("<h2>打法</h2><div class=tw><table><tr><th>阶段</th><th>打法</th></tr>"
                 + "".join(f"<tr><td class=n><b>{esc(x.get('stage'))}</b></td><td>{esc(x.get('what'))}</td></tr>" for x in r["formula"])
                 + "</table></div>")
    if r.get("remix"):
        B.append("<h2>复用方向</h2><ul>" + "".join(f"<li>{esc(x)}</li>" for x in r["remix"]) + "</ul>")
    return page(f"整部剧拆解 {ser['title']}", B)

