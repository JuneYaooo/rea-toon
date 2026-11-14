"""A whole series: every episode broken down on its own, then read together.

    work/<剧名>/
      index.html           the one page to open: which episode, and what of it
      series.json          episodes, the series cast (one id per person across episodes), the cross-episode read
      pages/               series.html / .md: arcs, setups and payoffs, how each ending hooks the next episode, paywall spots
      ep01/ ep02/ …        ordinary work dirs, each with its own VIDEO.md package and pages (layout.py)

What makes a series more than many videos is that the episodes hang together, and three rules keep that:
- an episode's number is its place among the sources (第2集 before 第10集) and is kept even when its import fails;
- episodes are broken down in order, each with what the earlier ones settled (`prior_of`: plots, cast, faces), and a
  run stops at the first episode it cannot finish, because every later one needs its context. `"skip": true` on an
  episode in series.json takes one that cannot be broken down out of the chain;
- each episode records which earlier episodes it was told about (`prior.eps`); one broken down without some of them
  is broken down again with them on the next run (a locked one only gets a warning).
Episodes done are skipped, so a long series resumes after any stop. Each episode keeps its own character ids (its
story.json may already be reviewed and locked); the series cast maps them.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import layout
from .genre import FORMATS, format_of
from .media import face_sheet, font_path
from .page import esc, fold, heatmap, link_up, meter, page, point, statline
from .prompts import SERIES_CAST_PROMPT, SERIES_PROMPT
from .report import _paras, metrics, span
from .story import lock_state, names_of, portraits
from .vlm import VLM

LOG = logging.getLogger(__name__)
VIDEO = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".flv")


def _natural(p: Path):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p.name)]


def expand(sources: list[str]) -> list[str]:
    """A folder stands for its videos in episode order (第2集 before 第10集); anything else is one episode."""
    out = []
    for s in sources:
        d = Path(s).expanduser()
        if d.is_dir():
            out += [str(p) for p in sorted((p for p in d.iterdir() if p.suffix.lower() in VIDEO), key=_natural)]
        else:
            out.append(s)
    return out


def load(root: Path) -> dict:
    p = layout.series(root)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"schema": "rea_toon.series.v1", "title": root.name, "episodes": []}


def save(root: Path, ser: dict) -> None:
    layout.put(layout.series(root), json.dumps(ser, ensure_ascii=False, indent=1))


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
        entry = {"ep": ep, "dir": f"ep{ep:02d}", "source": src, "title": ""}
        ser["episodes"].append(entry)  # the number is its place in the series, imported or not
        have.add(_same_source(src))
        LOG.info("第 %d 集 ← %s", ep, src[:80])
        try:
            entry["title"] = ingest(src, root / entry["dir"]).get("title") or ""
        except Exception as exc:  # the next episode does not take its number; the import is tried again on the next run
            LOG.warning("第 %d 集导入失败（集号留着，重跑时再导入）：%s：%s", ep, src[:80], exc)
        save(root, ser)
    return ser


def _imported(root: Path, ser: dict, e: dict) -> None:
    """The episode's video is in its directory: an import that failed when it was added is tried again."""
    work = root / e["dir"]
    if layout.source(work).is_file():
        return
    from .ingest import ingest
    e["title"] = e.get("title") or ingest(e["source"], work).get("title") or ""
    save(root, ser)


def _story(root: Path, e: dict) -> dict | None:
    p = layout.story(root / e["dir"])
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


PRIOR_CAST = 24  # people a later episode is told about (and shown the faces of): one sheet a model can still read


def _chain(ser: dict) -> list[dict]:
    """The episodes that hang together, in order: all but the ones marked "skip"."""
    return sorted((e for e in ser["episodes"] if not e.get("skip")), key=lambda e: e["ep"])


def _perceived(story: dict | None) -> bool:
    return bool(story) and any(s.get("analysis") for s in story.get("shots") or [])


def context_missing(ser: dict, e: dict, story: dict | None) -> list[int]:
    """Earlier episodes of the chain that this episode was broken down without (it records which it was told about
    in prior.eps). A story that records nothing (one video, or the first episode) misses nothing."""
    told = ((story or {}).get("prior") or {}).get("eps")
    if told is None:
        return []
    return [x["ep"] for x in _chain(ser) if x["ep"] < e["ep"] and x["ep"] not in told]


def prior_of(root: Path, ser: dict, ep: int, recap_eps: int = 3) -> dict | None:
    """What the episodes before `ep` settled, for analysing it: the last few plots and the cast so far. A person seen
    in several episodes is listed once (by the cross-episode id when series.json has one; its guessed name is left out,
    the plots spell names as the subtitles do), with the role from the
    episode that introduced them (later episodes guessed it with less to go by) and the latest look.
    A long series has met more people than fit: past PRIOR_CAST, those seen most recently stay (then most often)."""
    seen = {m: c for c in ser.get("characters") or [] for m in c.get("members") or []}
    earlier = [e for e in _chain(ser) if e["ep"] < ep]
    if not earlier:
        return None
    recap, cast, hook, faces, eps = [], {}, "", {}, []
    for e in earlier:
        story = _story(root, e)
        if not story or not (story.get("summary") or _perceived(story)):
            continue
        eps.append(e["ep"])
        if not story.get("summary"):
            continue
        recap.append(f"第 {e['ep']} 集：{story['summary']}")
        beats = (story.get("breakdown") or {}).get("beats") or []
        end = next((b for b in reversed(beats) if b.get("type") == "cliffhanger"), beats[-1] if beats else {})
        hook = str(end.get("what") or "") if e["ep"] == ep - 1 else ""  # only the episode right before leaves a hook
        for c in story.get("characters") or []:
            if c.get("importance") == "extra" or "crowd" in str(c.get("id")):
                continue
            u = seen.get(_label(e["ep"], c["id"])) or {}
            cid = u.get("id") or c["id"]
            row = cast.setdefault(cid, {"role": c.get("role", ""), "n": 0})
            row.update(look=str(c.get("appearance", ""))[:80], last=e["ep"], n=row["n"] + 1)
            if cid not in faces and (pic := portraits(story, root / e["dir"], c["id"], 1)):
                faces[cid] = pic[0]  # the episode that introduced them: before any disguise
    if len(cast) > PRIOR_CAST:
        keep = set(sorted(cast, key=lambda k: (-cast[k]["last"], -cast[k]["n"]))[:PRIOR_CAST])
        cast = {k: v for k, v in cast.items() if k in keep}  # still in the order they came into the drama
    lines = [f"- {cid}：{r['role']}；外貌：{r['look']}" for cid, r in cast.items()]
    sheet = face_sheet([(cid, faces[cid]) for cid in cast if cid in faces], layout.analysis(root, f"cast_before_ep{ep:02d}.jpg"))
    return {"ep": ep, "eps": eps, "recap": "\n".join(recap[-recap_eps:]), "cast": "\n".join(lines), "hook": hook,
            "roles": {cid: r["role"] for cid, r in cast.items()}, "sheet": str(sheet) if sheet else "",
            "faces": {cid: str(faces[cid]) for cid in cast if cid in faces}}


def analyze_all(root: Path, workers: int = 6, use_vlm: bool = True, full: bool = False) -> list[dict]:
    """Bring every episode, in order, up to analysis + breakdown + report, each with the context of the ones before.
    Done episodes are skipped; one done without some earlier episode's context is done again with it (unless it is
    locked). With the model on, the run stops at the first episode it cannot finish: every later one would be
    broken down without it. Without the model nothing is read across episodes, so a failure does not stop the rest."""
    from .pipeline import done, episode
    ser, failed = load(root), []
    chain = _chain(ser)
    for k, e in enumerate(chain):
        work = root / e["dir"]
        try:
            _imported(root, ser, e)
            again = False
            if done(work, use_vlm):  # a long series resumed: nothing is read again for what is finished
                story = _story(root, e)
                gap = context_missing(ser, e, story) if use_vlm else []
                if not gap:
                    continue
                if story.get("lock"):
                    LOG.warning("第 %d 集拆的时候没带上第 %s 集的前情，但已经锁定：不自动重拆", e["ep"], gap)
                    continue
                LOG.info("第 %d 集拆的时候没带上第 %s 集的前情：带着前情重拆（原来的存在 data/story.prev.json）", e["ep"], gap)
                again = True
            LOG.info("第 %d 集 …", e["ep"])
            episode(work, use_vlm=use_vlm, workers=workers, full=full, prior=prior_of(root, ser, e["ep"]), resume=not again)
        except Exception as exc:
            rest = len(chain) - k - 1
            msg = str(exc)
            if use_vlm and rest:
                msg += (f"。后面 {rest} 集要带着这一集的前情拆，先停在这里：修好后重跑同一条命令；"
                        "这一集确实拆不了，就在 series.json 里给它加 \"skip\": true")
            LOG.warning("第 %d 集失败：%s", e["ep"], msg)
            failed.append({"ep": e["ep"], "error": msg})
            if use_vlm:
                break
    return failed


# ---- one id per person across episodes ----

def _label(ep: int, cid: str) -> str:
    return f"E{ep}:{cid}"


def cast_entries(root: Path, ser: dict) -> list[dict]:
    out = []
    for e in ser["episodes"]:
        story = _story(root, e)
        if not story:
            continue
        for c in story.get("characters") or []:
            if c.get("importance") == "extra" or "crowd" in c["id"]:
                continue
            pic = portraits(story, root / e["dir"], c["id"], 1)
            out.append({"label": _label(e["ep"], c["id"]), "ep": e["ep"], "id": c["id"], "role": c.get("role", ""),
                        "importance": c.get("importance", ""), "shots": len(c.get("shots") or []),
                        "appearance": str(c.get("appearance", ""))[:120], "names": sorted(names_of(c) - {c["id"]}),
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


UNIFY_ALL = 64  # entries (a character of an episode) one merge call can take: four sheets of faces


def unify(root: Path, ser: dict, vlm: VLM | None) -> list[dict]:
    entries = cast_entries(root, ser)
    if not entries:
        return []
    if vlm is None:  # same id in every episode = same person; the model pass is what catches renamed ids
        groups = defaultdict(list)
        for x in entries:
            groups[x["id"]].append(x["label"])
        return _clean_groups([{"id": k, "members": v} for k, v in groups.items()], entries)
    # A long series: every episode after the first was analysed knowing the cast so far, so one id is one person.
    # The model is then asked about each id once (the episode that introduced it), not about every episode's copy.
    asked, same = entries, {}
    if len(entries) > UNIFY_ALL:
        first, shots = {}, Counter()
        for x in entries:
            same.setdefault(first.setdefault(x["id"], x)["label"], []).append(x["label"])
            shots[x["id"]] += x["shots"]
        # Still more ids than one call can take: the leads and the most seen are asked, the rest stay one id one person.
        rank = {"lead": 0, "supporting": 1}
        top = sorted(first.values(), key=lambda x: (rank.get(x["importance"], 2), -shots[x["id"]]))[:UNIFY_ALL]
        asked = [x for x in first.values() if x in top]
    rows = "\n".join(f"{x['label']}|{x['role']}|{x['importance']}|{x['shots']}|{x['appearance']}|{'、'.join(x['names']) or '-'}"
                     for x in asked)
    sheets = portrait_sheets(asked, layout.analysis(root))
    try:
        r = vlm.ask_json(SERIES_CAST_PROMPT.format(rows=rows), sheets, max_tokens=8000, timeout=300)
    except Exception as exc:
        LOG.warning("跨集角色合并失败，先按 id 合并：%s", exc)
        return unify(root, ser, None)
    found = r.get("characters") or []
    if same:
        named = {rep for g in found for rep in g.get("members") or []}
        found = found + [{"members": [rep]} for rep in same if rep not in named]  # an id the model left out stays one person
        found = [{**g, "members": [m for rep in g.get("members") or [] for m in same.get(rep, [])]} for g in found]
    groups = _clean_groups(found, entries)
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
        rp = layout.data(root / e["dir"], "report.json")
        read = (json.loads(rp.read_text(encoding="utf-8")).get("read") or {}) if rp.is_file() else {}
        ending = next((b for b in reversed(beats) if b["type"] == "cliffhanger"), beats[-1] if beats else None)
        fmt = format_of(story, ser.get("format"))
        open_gaps = [g for g in (story.get("breakdown") or {}).get("info_gaps") or []]
        out.append({"ep": e["ep"], "dir": e["dir"], "title": e.get("title") or (story.get("source") or {}).get("title") or "",
                    "summary": story.get("summary", ""), "duration": m["duration"], "shots": m["shots"], "peaks": m["peaks"],
                    "first_peak": m["first_peak"], "avg_gap": m["avg_gap"], "node_kinds": m["node_kinds"],
                    "owed": m["pressure"]["owed"], "info_gaps": open_gaps,
                    "feels": {k: v["seconds"] for k, v in m["feels"].items()}, "longest_gap": m["longest_gap"],
                    "releases": len(m["pressure"]["pairs"]),
                    "likes": (story.get("source") or {}).get("likes"),
                    "cliffhanger": m["cliffhanger"], "screen_time": m["screen_time"],
                    "opening": read.get("hook") or (beats[0].get("what", "") if beats else ""),
                    "ending": (ending or {}).get("what", ""), "logline": read.get("logline", ""),
                    "beats": [b["type"] + (f"({b['feel']})" if b.get("feel") else "") for b in beats],
                    "format": fmt["cn"], "locked": lock_state(story) == "locked",
                    "context_missing": context_missing(ser, e, story)})
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


def settle_format(root: Path, ser: dict) -> str | None:
    """One format for the whole series: the one most of its running time was judged to be (each episode is judged
    from its own style notes, and a few come out different). `"format_fixed": true` in series.json keeps a hand-set
    `format`; an episode's own story["format"] still wins for that episode."""
    if ser.get("format_fixed") and ser.get("format") in FORMATS:
        return ser["format"]
    secs: Counter = Counter()
    for e in ser["episodes"]:
        story = _story(root, e)
        if story and (story.get("breakdown") or {}).get("beats"):
            secs[format_of(story)["id"]] += float(story.get("duration") or 0) or 1.0
    return secs.most_common(1)[0][0] if secs else None


def _align_reports(root: Path, ser: dict, use_vlm: bool) -> None:
    """Episode reports written under another format (or before the series had one) are redone under the series'."""
    from .report import report
    for e in ser["episodes"]:
        rp, story = layout.data(root / e["dir"], "report.json"), _story(root, e)
        if not rp.is_file() or not story or story.get("format") in FORMATS:
            continue
        if (json.loads(rp.read_text(encoding="utf-8")).get("format") or {}).get("id") != ser["format"]:
            LOG.info("第 %d 集的报告按整部剧的类型（%s）重出", e["ep"], FORMATS[ser["format"]]["cn"])
            try:
                report(root / e["dir"], use_vlm=use_vlm)
            except Exception as exc:
                LOG.warning("第 %d 集报告重出失败：%s", e["ep"], exc)


def build(root: Path, use_vlm: bool = True) -> dict:
    ser = load(root)
    vlm = VLM(cache_dir=layout.analysis(root, "vlm_cache")) if use_vlm else None
    fid = settle_format(root, ser)
    if fid:
        ser["format"] = fid
        save(root, ser)  # the episode reports read it from here
        _align_reports(root, ser, use_vlm)
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
    fmt = FORMATS[fid]["cn"] if fid else ""
    if vlm is not None and done:
        cast_txt = "\n".join(f"{c['id']}|{c['name']}|{c['role']}|{c['episodes']}|{c['seconds']}" for c in cast[:20])
        ep_txt = "\n".join(f"{r['ep']}|{r['duration']:.0f}s|{r['summary']}|{r['opening']}|{r['ending']}|{'→'.join(r['beats'])}" for r in done)
        dash = lambda x: "-" if x is None else x
        met = "\n".join(f"{r['ep']}|{r['duration']:.0f}s|{r['shots']}|{r['peaks']}|{dash(r['first_peak'])}|{dash(r['avg_gap'])}|"
                        f"{r['node_kinds']}|{dash(r['owed'])}|{'有' if r['cliffhanger'] else '无'}|{dash(r['likes'])}" for r in done)
        gaps = "\n".join(f"{r['ep']}|{g.get('what')}|{_names(g.get('blind'))}|{'镜' + str(g['to']) + '揭开' if g.get('to') else '没揭开'}"
                         for r in done for g in r["info_gaps"])
        met += "\n跨集情绪账（集号|主打情绪|各情绪秒数|这一集憋了多久|有没有放（爽）|片尾还欠）：\n" + (_ledger_text(done) or "（各集还没有逐镜情绪）")
        try:
            ser["read"] = vlm.ask_json(SERIES_PROMPT.format(title=ser["title"], n=len(done), format=fmt, cast=cast_txt,
                                                            episodes=ep_txt, metrics=met, gaps=gaps or "（无）"),
                                       [], max_tokens=12000, timeout=400)
            ser.pop("read_error", None)
        except Exception as exc:
            LOG.warning("跨集拆解失败：%s", exc)
            ser["read_error"] = str(exc)
    save(root, ser)
    layout.put(layout.page(root, "series.md"), to_md(ser))
    render(root, ser)
    return ser


def render(root: Path, ser: dict | None = None) -> None:
    """Every page of the series from what is on disk (no model call; story.json / report.json / series.json are
    only read): series.html, each episode's shot page and report (now with the names the series settled on), and
    the site that holds them."""
    from .report import render as report_page
    from .review import write_review
    from .videomd import write as package
    ser = ser or load(root)
    if ser.get("rows"):
        layout.put(layout.page(root, "series.html"), to_html(ser))
    from .checks import lint
    for e in ser["episodes"]:
        story = _story(root, e)
        if story and story.get("shots"):
            story["lint"] = lint(story)  # as the checks stand today; shown, not saved
        if story and story.get("breakdown"):
            write_review(story, root / e["dir"])
            report_page(root / e["dir"], story=story)
        if story:
            package(root / e["dir"], story)  # the episode's VIDEO.md says which series it is part of
    from .videomd import write_series
    write_series(root, ser)
    link_up(root)


# ---- pages ----

def _names(xs) -> str:
    return "、".join(str(x) for x in xs) if isinstance(xs, list) else str(xs or "")


def _likes(x) -> str:
    return "—" if x is None else f"{x / 10000:.1f}万" if x >= 10000 else str(x)


def ledger_rows(rows: list[dict]) -> list[dict]:
    """The emotion ledger across episodes: what each one hits with, what it presses and leaves owed, and for every
    episode that lets a 爽 go, how many episodes of 憋 were standing before it."""
    out, debt = [], 0.0
    for r in rows:
        feels = r.get("feels") or {}
        top = max(feels, key=feels.get) if feels else ""
        paid = bool(feels.get("爽"))
        out.append({"ep": r["ep"], "top": top, "feels": feels, "peaks": r["peaks"], "avg_gap": r.get("avg_gap"),
                    "longest_gap": r.get("longest_gap"), "pressed": round(feels.get("憋", 0), 1), "owed": r.get("owed") or 0,
                    "paid": paid, "debt_before": round(debt, 1),
                    "open_gaps": [g.get("what") for g in r.get("info_gaps") or [] if not g.get("to")],
                    "closed_gaps": [g.get("what") for g in r.get("info_gaps") or [] if g.get("to")]})
        debt = (r.get("owed") or 0) if paid else debt + feels.get("憋", 0)
    return out


def _ledger_text(rows: list[dict]) -> str:
    return "\n".join(f"{x['ep']}|主打{x['top'] or '-'}|{x['feels']}|憋 {x['pressed']}s|{'放了' if x['paid'] else '没放'}"
                     f"（这一集之前攒着 {x['debt_before']}s）|片尾欠 {x['owed']}s" for x in ledger_rows(rows))


def _context_lines(ser: dict) -> list[str]:
    """What breaks the chain of episodes: ones skipped, not broken down yet, or broken down without earlier context."""
    rows = ser.get("rows") or []
    L = [f"- 第 {x['ep']} 集拆的时候没带上第 {'、'.join(map(str, x['context_missing']))} 集的前情"
         + ("（已锁定，没自动重拆）" if x.get("locked") else "（重跑 rea-toon series 会带着前情重拆）") for x in rows if x.get("context_missing")]
    skipped = [e["ep"] for e in ser.get("episodes") or [] if e.get("skip")]
    if skipped:
        L.append(f"- 跳过了第 {'、'.join(map(str, skipped))} 集（series.json 里标了 skip），后面的集没有它的前情")
    missing = [x["ep"] for x in rows if x.get("missing") and x["ep"] not in skipped]
    if missing:
        L.append(f"- 还没拆完：第 {span(missing)} 集")
    return (["## 跨集关联要注意", ""] + L + [""]) if L else []


def to_md(ser: dict) -> str:
    r, rows = ser.get("read") or {}, [x for x in ser.get("rows") or [] if not x.get("missing")]
    L = [f"# 整部剧拆解：{ser['title']}", "", f"{len(rows)} 集，共 {sum(x['duration'] for x in rows) / 60:.1f} 分钟", ""]
    for k, name in (("logline", "一句话卖点"), ("main_conflict", "主冲突"), ("engine", "情绪打法")):
        if r.get(k):
            L += [f"**{name}**：{r[k]}", ""]
    L += _context_lines(ser)
    L += ["## 逐集", "", "| 集 | 时长 | 镜头 | 情绪节点 | 第一个节点 | 点赞 | 结尾钩子 | 剧情 |", "|---|---|---|---|---|---|---|---|"]
    L += [f"| {x['ep']} | {x['duration']:.0f}s | {x['shots']} | {x['peaks']} | {x['first_peak'] if x['first_peak'] is not None else '—'} | "
          f"{_likes(x.get('likes'))} | {x['ending'] if x['cliffhanger'] else '无'} | {x['summary']} |" for x in rows] + [""]
    led = ledger_rows(rows)
    if led:
        L += ["## 情绪账（跨集）", "", "| 集 | 主打 | 节点 | 平均空档 | 最长空档 | 憋 | 放了没有 | 片尾欠 | 没揭开的信息差 |", "|---|---|---|---|---|---|---|---|---|"]
        L += [f"| {x['ep']} | {x['top']} | {x['peaks']} | {x['avg_gap'] if x['avg_gap'] is not None else '—'}s | {x['longest_gap']}s | {x['pressed']}s | "
              f"{'放（之前攒了 ' + str(x['debt_before']) + 's）' if x['paid'] else '没放'} | {x['owed']}s | {'；'.join(x['open_gaps'])} |" for x in led] + [""]
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
    for k, name in (("pacing", "跨集节奏"), ("heat", "点赞和结构"), ("tropes", "反复使用的套路"), ("remix", "复用方向")):
        if r.get(k):
            L += [f"## {name}", ""] + [f"- {point(x)}" for x in r[k]] + [""]
    if r.get("formula"):
        L += ["## 打法", ""] + [f"- **{x.get('stage')}**：{x.get('what')}" for x in r["formula"]] + [""]
    return "\n".join(L) + "\n"


def to_html(ser: dict) -> str:
    """The page of the whole drama, in the order it is read: what it sells, how it is played (stage by stage, the
    tropes it keeps using, what to take from it), then the episodes as one table. What backs that up (the emotion
    ledger, who is on screen when, threads, hooks, paywalls) is one line each at the bottom, opened when wanted."""
    r = ser.get("read") or {}
    allrows = ser.get("rows") or []
    rows = [x for x in allrows if not x.get("missing")]
    total = sum(x["duration"] for x in rows)
    ul = lambda xs: "<ul>" + "".join(f"<li>{esc(point(x))}</li>" for x in xs) + "</ul>"
    B = [f"<h1>{esc(ser['title'])}</h1>",
         f"<p><span class=state>{esc(rows[0]['format'] if rows else '')}</span> <span class=muted>{len(rows)} 集"
         + (f" · 还有 {len(allrows) - len(rows)} 集没拆完" if len(allrows) > len(rows) else "") + "</span></p>"]
    if r.get("logline"):
        B.append(f"<p class=lead>{esc(r['logline'])}</p>")
    if r.get("main_conflict"):
        B.append(f"<p><b>主冲突</b>：{esc(r['main_conflict'])}</p>")
    warn = _context_lines(ser)[2:-1]
    if warn:
        B.append("<p class=warn>" + "<br>".join(esc(x[2:]) for x in warn) + "</p>")
    if ser.get("read_error"):
        B.append(f"<p class=err>跨集解读失败（只有数据部分）：{esc(ser['read_error'])}；重跑 rea-toon series</p>")
    B.append(statline([("集", len(rows)), ("分钟", f"{total / 60:.1f}"), ("平均每集", f"{total / max(1, len(rows)):.0f}s"),
                       ("集有结尾钩子", f"{sum(1 for x in rows if x['cliffhanger'])}/{len(rows)}"),
                       ("个情绪节点/集", f"{sum(x['peaks'] for x in rows) / max(1, len(rows)):.1f}")]))

    # ---- how it is played ----
    if r.get("engine"):
        B += ["<h2>情绪打法</h2>", _paras(r["engine"])]
    if r.get("formula"):
        B.append("<h2>打法</h2><div class=tw><table><tr><th>阶段</th><th>打法</th></tr>"
                 + "".join(f"<tr><td class=n><b>{esc(x.get('stage'))}</b></td><td>{esc(x.get('what'))}</td></tr>" for x in r["formula"])
                 + "</table></div>")
    two = [(name, r[k]) for k, name in (("tropes", "反复使用的套路"), ("remix", "复用方向")) if r.get(k)]
    if two:
        B.append(f"<div class={'two' if len(two) == 2 else ''}>" + "".join(f"<div><h2>{n}</h2>{ul(xs)}</div>" for n, xs in two) + "</div>")

    # ---- the episodes: one table, the node count drawn in its own column ----
    B.append("<h2>逐集</h2>")
    hooked = bool(r.get("links"))  # the hooks get their own table (how each one is picked up): not twice
    first = lambda t: re.split(r"(?<=[。！？])", str(t or ""))[0]
    liked = any(x.get("likes") for x in rows)  # a video handed over as a file has no platform numbers: no column for it
    top = max((x["peaks"] for x in rows), default=0)
    B.append("<div class=tw><table><tr><th></th><th>时长</th><th title='观众被打到的时刻：爽、虐、甜、笑、惊、燃'>情绪节点</th><th>第一个</th>" + ("<th>点赞</th>" if liked else "")
             + ("" if hooked else "<th>结尾钩子</th>") + "<th>剧情</th></tr>"
             + "".join(f"<tr><td class=n><a href='../{esc(x['dir'])}/pages/report.html'>第 {x['ep']} 集</a></td><td class=n>{x['duration']:.0f}s</td>"
                       f"<td class=n title='{esc(x.get('node_kinds') or '')}'>{meter(x['peaks'], top, str(x['peaks']))}</td>"
                       f"<td class=n>{'—' if x['first_peak'] is None else str(x['first_peak']) + 's'}</td>"
                       + (f"<td class=n>{_likes(x.get('likes'))}</td>" if liked else "")
                       + ("" if hooked else f"<td>{esc(x['ending']) if x['cliffhanger'] else '<span class=warn>没有</span>'}</td>")
                       + f"<td title='{esc(x['summary'])}'>{esc(first(x['summary']))}</td></tr>"
                       if not x.get("missing") else
                       f"<tr><td>{x['ep']}</td><td colspan=6 class=muted>还没拆</td></tr>" for x in allrows)
             + "</table></div>")

    # ---- what backs it up: one line each ----
    F = []
    led = ledger_rows(rows)
    if led:
        kinds = [k for k in ("爽", "燃", "甜", "笑", "虐", "惊", "憋", "悬") if any(x["feels"].get(k) for x in led)]
        E = ["<p class=muted>每集各种情绪占了多少秒，颜色越深占得越多。爽、燃、甜、笑、虐、惊是打到观众的，憋、悬是在攒"
             "</p>",
             heatmap([(f"第 {x['ep']} 集", [x["feels"].get(k, 0) for k in kinds]) for x in led], kinds, "s",
                     lambda i, j, v: f"第 {led[i]['ep']} 集 · {kinds[j]} {v:g} 秒，占这一集的 "
                                     f"{v / max(1e-9, sum(led[i]['feels'].values())):.0%}（这一集主打{led[i]['top']}）")]
        owed = [x for x in led if x["owed"]]
        if owed:
            E.append("<p class=muted>憋到片尾没放、留给后面的：" + "；".join(f"第 {x['ep']} 集 {x['owed']}s" for x in owed) + "</p>")
        if not r.get("threads"):  # the read's own table of threads says it better; without one, list what is still hidden
            still = list(dict.fromkeys(g for x in led[-1:] for g in x["open_gaps"]))
            if still:
                E.append("<p class=muted>到最后一集还没揭开的信息差</p><ul>" + "".join(f"<li>{esc(g)}</li>" for g in still) + "</ul>")
        tops = Counter(x["top"] for x in led if x["top"]).most_common(3)
        F.append(fold("情绪账（跨集）", "主打 " + "、".join(f"{k}（{n} 集）" for k, n in tops) if tops else "每集各种情绪占多少秒", "".join(E)))
    cast = ser.get("characters") or []
    called = {c["id"]: c.get("name") or c["id"] for c in cast}
    C = ""
    if cast:
        on = {(x["ep"], k): v for x in rows for k, v in (x.get("screen_time") or {}).items()}  # (episode, id in it) -> seconds
        sec = lambda c, x: round(sum(on.get((x["ep"], m.split(":", 1)[1]), 0) for m in c.get("members") or []
                                     if m.split(":", 1)[0] == f"E{x['ep']}"), 1)
        shown = cast[:12]
        C += ("<p class=muted>每个角色在每一集出镜多少秒，颜色越深戏份越重；空格是这一集没出场。名字后面是全剧合计</p>"
              + heatmap([(f"{esc(c['name'] or c['id'])} <span class=muted>{c['seconds']:.0f}s</span>", [sec(c, x) for x in rows]) for c in shown],
                        [f"第 {x['ep']} 集" for x in rows], "s",
                        lambda i, j, v: f"{shown[i]['name'] or shown[i]['id']}（{shown[i]['role']}）· 第 {rows[j]['ep']} 集出镜 {v:g} 秒，"
                                        f"占这一集的 {v / max(1e-9, rows[j]['duration']):.0%}"))
    if r.get("arcs"):
        C += ("<h3>人物弧光</h3><ul>" + "".join(
            f"<li><b>{esc(called.get(a.get('id'), a.get('id')))}</b>：{esc(a.get('arc'))}<br><span class=muted>"
            + esc("；".join(f"第{t.get('ep')}集 {t.get('what')}" for t in a.get("turns") or [])) + "</span></li>" for a in r["arcs"]) + "</ul>")
    F.append(fold("角色和人物弧光", "、".join(str(c.get("name") or c["id"]) for c in cast[:5]) + (" 等" if len(cast) > 5 else ""), C))
    if r.get("threads"):
        open_ = sum(1 for t in r["threads"] if not t.get("paid"))
        F.append(fold("伏笔和回收", f"{len(r['threads'])} 条" + (f" · {open_} 条还没收" if open_ else " · 都收了"),
                      "<div class=tw><table><tr><th>伏笔/悬念</th><th>埋</th><th>收</th><th>怎么埋、怎么收</th></tr>"
                      + "".join(f"<tr><td>{esc(t.get('what'))}</td><td class=n>第 {esc(t.get('planted'))} 集</td>"
                                f"<td class=n>{'第 ' + esc(t['paid']) + ' 集' if t.get('paid') else '<span class=warn>还没收</span>'}</td>"
                                f"<td>{esc(t.get('note'))}</td></tr>" for t in r["threads"]) + "</table></div>"))
    K = ""
    if r.get("links"):
        K += ("<div class=tw><table><tr><th>集</th><th>结尾留的钩子</th><th>下一集怎么接</th></tr>"
              + "".join(f"<tr><td class=n>{esc(x.get('ep'))}</td><td>{esc(x.get('end_hook'))}</td><td>{esc(x.get('picked_up'))}</td></tr>"
                        for x in r["links"]) + "</table></div>")
    if r.get("paywall"):
        K += "<h3>付费卡点</h3><ul>" + "".join(f"<li><b>第 {esc(x.get('ep'))} 集</b>：{esc(x.get('why'))}</li>" for x in r["paywall"]) + "</ul>"
    F.append(fold("集尾钩子和付费卡点" if r.get("links") else "付费卡点",
                  "每集结尾留什么、下一集怎么接" + (f" · 卡点在第 {'、'.join(str(x.get('ep')) for x in r['paywall'])} 集" if r.get("paywall") else ""), K))
    N = "".join(f"<h3>{name}</h3>{ul(r[k])}" for k, name in (("pacing", "跨集节奏"), ("heat", "点赞和结构")) if r.get(k) and (k != "heat" or liked))
    F.append(fold("跨集节奏" + ("和点赞" if r.get("heat") and liked else ""), "哪几集快、哪几集慢，热度跟着什么走", N))
    F = [x for x in F if x]
    if F:
        B.append("<div class=more><p class=muted>依据和细节，点开看</p>" + "".join(F) + "</div>")
    return page(f"整部剧拆解 {ser['title']}", B)
