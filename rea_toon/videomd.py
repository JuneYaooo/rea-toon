"""VIDEO.md: the breakdown of one episode laid out for models and downstream tools (the open format rea-video also
writes: https://github.com/videomd/videomd). A reader starts at the card, then the map, and opens only what it needs.

    L0  VIDEO.md front matter   the card: what the episode is, which series it is part of, how reviewed
    L1  VIDEO.md body           the selling line, the dramatic spine, what still needs a person, the map
    L2  video/scenes.md         one line per scene
        video/timeline.md       one line per shot: its job in the drama, what is seen, who says what
        video/content.md        plot, beats, information gaps
        video/cast.md           style bible, characters, what each wears in each scene
        video/x-report.md       the viral breakdown: emotion ledger, tropes, the reusable formula (report.py)
    L3  video/shots/07.md       everything about one shot
    L4  video/transcript.vtt    who says what and when (WebVTT); story.json, data/, frames/, characters/ …

Code only, from story.json and data/report.json: written again whenever either changes, never edited by hand (the
source to edit is story.json). The drama's own keys are extensions of the format: `x-feel`, `x-say` on the timeline,
`x-drama` in a shot file, `x-report` as a topic. `check` validates a directory with the reference `videomd` package.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from . import __version__, layout
from .breakdown.screen_text import TEXT_CN
from .checks import RISK_CN
from .genre import format_of
from .page import cast_names
from .review import _extras, _staging_text, _texts
from .story import (PRIORITY_CN, ROLE_CN, day_tag, gap_line, lines_of, lock_state, look_in, look_line, prior_text,
                    said_tag, scene_of)
from .subtitles import MODE_CN, line_mode, unique_lines

FORMAT, VERSION = "videomd", 1
DIR = layout.PACKAGE
STATE_CN = {"locked": "已锁定（审过）", "stale": "锁定后改过，要重新 lock", "unlocked": "待审核（模型拆的，还没人审）"}
# story.json's transition names -> the ids of the format; a plain cut is not written
TRANSITION_ID = {"white_flash": "flash", "fade": "fade_through", "dissolve": "dissolve", "whip": "whip", "match": "match_cut"}
TRANSITION_CN = {"white_flash": "闪白", "fade": "黑场/白场过渡", "dissolve": "叠化", "whip": "甩镜", "match": "匹配剪辑"}
# kinds of screen text -> overlay ids (text that belongs to the scene is not an overlay)
OVERLAY_ID = {"overlay": "styled_text", "aigc": "label", "watermark": "x-watermark", "bubble": "x-bubble"}
OVERLAY_CN = {"subtitle": "字幕", "title": "人名卡", "styled_text": "花字", "label": "AI 标识", "x-watermark": "水印", "x-bubble": "对话气泡"}


# ---- text helpers ----

def tokens(text: str) -> int:
    """Rough size, the way the spec counts without a tokenizer: ASCII / 4 + everything else * 0.75."""
    ascii_n = sum(c.isascii() for c in text)
    return int(ascii_n / 4 + (len(text) - ascii_n) * 0.75) + 1


def _one(s, limit: int | None = None) -> str:
    """One line that can sit between the `|` of a timeline or a map."""
    s = re.sub(r"\s+", " ", str(s or "")).replace("|", "/").strip()
    return s[:limit] + "…" if limit and len(s) > limit else s


def _front(meta: dict, body: str) -> str:
    """Front matter + body. Each value is written as JSON, which YAML reads as it is."""
    fm = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
    return f"---\n{fm}\n---\n\n{body.strip()}\n"


def _sized(meta: dict, body: str) -> str:
    return _front({**meta, "tokens": tokens(body)}, body)


def slug(text: str) -> str:
    """A card name: lowercase letters, digits and single hyphens, at most 64 characters ("" when nothing is left)."""
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:64].strip("-")


def _span(xs) -> str:
    xs = sorted({int(x) for x in xs or [] if str(x).lstrip("-").isdigit()})
    return f"{xs[0]}–{xs[-1]}" if len(xs) > 1 and xs[-1] - xs[0] == len(xs) - 1 else "、".join(map(str, xs))


def _title(story: dict, work: Path) -> str:
    return re.split(r"\s*#", (story.get("source") or {}).get("title") or "")[0].strip() or work.name


# ---- what the story says, shot by shot ----

def _scenes(story: dict) -> list[dict]:
    """The scenes of the breakdown when they cover every shot once, in order (what the format asks of scenes.md);
    else none: a hand edit can leave a gap, and a package with wrong scenes is worse than one without."""
    scenes = (story.get("breakdown") or {}).get("scenes") or []
    idx = [s["index"] for s in story["shots"]]
    want = idx[0] if idx else None
    for sc in scenes:
        a, b = (sc.get("shots") or [None, None])[:2]
        if a != want or b is None or b < a or b not in idx:
            return []
        want = idx[idx.index(b) + 1] if b != idx[-1] else None
    return scenes if scenes and want is None else []


def _overlays(s: dict) -> list[str]:
    out = []
    if lines_of(s):
        out.append("subtitle")
    if any(x.get("card") or x.get("speaker") == "title" for x in s.get("subtitles") or []):
        out.append("title")
    out += [OVERLAY_ID[t["kind"]] for t in s.get("screen_text") or [] if t.get("kind") in OVERLAY_ID]
    return list(dict.fromkeys(out))


def _said(s: dict, told: set | None = None) -> list[str]:
    """The lines of a shot. told: lines already given to an earlier shot (one that runs across a cut sits in both);
    those are left out and the rest added, so a timeline says each line once."""
    out = []
    for x in lines_of(s):
        k = (x.get("start"), x.get("text"))
        if told is not None and k in told:
            continue
        if told is not None:
            told.add(k)
        out.append(f"{x.get('speaker') or '?'}{said_tag(x)}：{x['text']}" + ("（说话人待确认）" if x.get("unsure") else ""))
    return out


def _flags(s: dict, lint_shots: set) -> list[str]:
    out = ["说话人待确认"] if any(x.get("unsure") for x in lines_of(s)) else []
    return out + (["有要处理的问题"] if s["index"] in lint_shots and not out else [])


def timeline_line(s: dict, lint_shots: set, told: set | None = None) -> str:
    a, k = s.get("analysis") or {}, s.get("key") or {}
    f = {}
    role = ROLE_CN.get(k.get("role"), "") if k.get("role") != "normal" else ""
    head = "".join(x for x in (role, "（必保）" if k.get("priority") == "must" else "") if x)
    if head:
        f["story"] = head  # why the shot is there is one click down (shots/NN.md): a timeline that says it all is not read
    if a.get("action_summary") or k.get("function"):
        f["seen"] = _one(a.get("action_summary") or k["function"], 44)
    cam = " ".join(x for x in (a.get("shot_size"), a.get("camera_move")) if x)
    if cam:
        f["camera"] = _one(cam, 24)
    if k.get("transition_in") in TRANSITION_CN:
        f["transition"] = TRANSITION_CN[k["transition_in"]]
    over = [OVERLAY_CN[o] for o in _overlays(s) if o != "subtitle"]  # the lines themselves are under x-say
    if over:
        f["overlay"] = "、".join(over)
    if _flags(s, lint_shots):
        f["flag"] = "，".join(_flags(s, lint_shots))
    if k.get("feel"):
        f["x-feel"] = k["feel"]
    said = _said(s, told)
    if said:
        f["x-say"] = _one(" / ".join(said), 60)
    return " | ".join([f"#{s['index']:02d} {s['start']:.2f}-{s['end']:.2f}"] + [f"{key} {v}" for key, v in f.items()])


LEGEND = [
    "每行一个镜头：`#镜号 起-止(秒) | 键 值 | …`，没内容的键不写。",
    "`story` 这一镜的戏剧作用（钩子、反转、爽点…，带「必保」的是全片最要紧的一两成；普通镜不写）· `seen` 画面里发生的事 · "
    "`camera` 景别和运镜 · `transition` 和上一镜怎么接（硬切不写）· `overlay` 花字、人名卡等叠加层（字幕不列）· `flag` 要人确认的地方 · "
    "`x-feel` 观众此刻的情绪（爽 / 憋 / 虐 / 甜 / 笑 / 悬 / 惊 / 燃）· `x-say` 这一镜开始说的台词（说话人 id：台词；画外、内心独白、旁白会标出；跨到下一镜的一句只记在开始的那一镜）",
    f"某一镜为什么这么拍（剧情功能、必须保住什么）和全部细节在 `{DIR}/shots/镜号.md`（如 `{DIR}/shots/07.md`）；角色 id 对应谁，看 `{DIR}/cast.md`。",
]


def _shot_file(story: dict, s: dict, work: Path, scene_no: dict, lint: list[dict]) -> str:
    a, k, i = s.get("analysis") or {}, s.get("key") or {}, s["index"]
    frames = {n: p for n, p in (s.get("frames") or {}).items() if p and (work / p).is_file()}
    meta = {FORMAT: VERSION, "part": "shot", "shot": i, "time": [round(s["start"], 3), round(s["end"], 3)]}
    if i in scene_no:
        meta["scene"] = scene_no[i]
    if _overlays(s):
        meta["overlays"] = _overlays(s)
    if any(x.get("unsure") for x in lines_of(s)):
        meta["flags"] = ["uncertain"]
    if k.get("transition_in") in TRANSITION_ID:
        meta["transition_in"] = {"type": TRANSITION_ID[k["transition_in"]]}
    if frames:
        meta["frames"] = frames
    drama = {n: k[n] for n in ("role", "feel", "priority") if k.get(n)}
    if drama:
        meta["x-drama"] = drama

    L = [f"# 镜 {i} · {s['start']:.2f}–{s['end']:.2f}s", "", "## 看到的", ""]
    sc = scene_of(story, i)
    if sc or a.get("scene"):
        L.append("- 场景：" + "；".join(x for x in (f"{sc['id']} {sc.get('location', '')}{day_tag(sc)}" if sc else "", a.get("scene", "")) if x))
    if a.get("shot_size") or a.get("camera_move"):
        L.append(f"- 景别 / 运镜：{a.get('shot_size', '')} / {a.get('camera_move', '')}")
    if a.get("action_summary"):
        L.append(f"- 发生了什么：{a['action_summary']}")
    for c in a.get("characters") or []:
        look = look_in(story, c.get("id"), i) if c.get("id") else ""
        L.append(f"- 人物 `{c.get('id')}`（{c.get('where', '')}）：{c.get('action', '')}"
                 + (f"；表情：{c['expression']}" if c.get("expression") else "") + (f"；造型：{look}" if look else ""))
    L += [f"- 台词 {x}" for x in _said(s)]
    for name, text in (("画面文字", _texts(s)), ("机位和视线", _staging_text(s)), ("画面", _extras(s))):
        if text:
            L.append(f"- {name}：{text}")
    L += [f"- 身体接触 {c['t']:.2f}s：{c['action']}{'（音轨上有音效）' if c.get('sound') else ''}" for c in s.get("contacts") or []]
    if s.get("freeze"):
        L.append("- 定格：" + "、".join(f"{r[0]:.2f}–{r[1]:.2f}s" for r in s["freeze"] if len(r) >= 2))
    if k:
        L += ["", "## 戏剧作用（拆解的判断）", ""]
        tag = " · ".join(x for x in (ROLE_CN.get(k.get("role"), ""), k.get("feel", ""), PRIORITY_CN.get(k.get("priority"), "")) if x)
        L += [f"- {tag}"] if tag else []
        L += [f"- 剧情功能：{k['function']}"] if k.get("function") else []
        fx = [TRANSITION_CN[t] for t in [k.get("transition_in")] if t in TRANSITION_CN] + list(k.get("fx") or [])
        L += [f"- 转场 / 特效：{'、'.join(fx)}"] if fx else []
    notes = ([f"必须保住：{'；'.join(k['keep'])}"] if k.get("keep") else []) + [RISK_CN.get(r, r) for r in s.get("risks") or []]
    notes += [f"要确认：{x['msg']}" for x in lint if x.get("shot") == i]
    if notes:
        L += ["", "## 这一镜要留意", ""] + [f"- {x}" for x in notes]
    return _sized(meta, "\n".join(L))


# ---- topic files ----

def _content(story: dict) -> str:
    bd = story.get("breakdown") or {}
    L = ["# 内容与结构", "", story.get("summary", "")]
    if (story.get("prior") or {}).get("recap"):
        L += ["", "## 前情", "", prior_text(story)]
    if bd.get("beats"):
        L += ["", "## 戏剧节拍", "", "模型概括的骨架，一集五到八条；数情绪节点看 `x-feel`（逐镜），不看这里。", ""]
        L += [f"- **{ROLE_CN.get(b['type'], b['type'])}**{'（' + b['feel'] + '）' if b.get('feel') else ''} 镜 {_span(b.get('shots'))}：{b.get('what', '')}"
              + (f"；必须保住：{b['must_keep']}" if b.get("must_keep") else "") + (f"；兑现：{b['pays']}" if b.get("pays") else "") for b in bd["beats"]]
    if bd.get("info_gaps"):
        L += ["", "## 信息差", ""] + [f"- {gap_line(g)}" for g in bd["info_gaps"]]
    return "\n".join(L)


def _cast(story: dict, called: dict) -> str:
    bd = story.get("breakdown") or {}
    L = ["# 角色、造型与画风", ""]
    if story.get("style") or bd.get("style_bible"):
        L += ["## 画风档案", ""] + ([f"- 画风：{story['style']}"] if story.get("style") else [])
        L += [f"- {k}：{v if not isinstance(v, list) else '、'.join(map(str, v))}" for k, v in (bd.get("style_bible") or {}).items()] + [""]
    L += ["## 角色", ""]
    for c in story["characters"]:
        name = f"（{called[c['id']]}）" if called.get(c["id"]) else ""
        L.append(f"- `{c['id']}`{name} {c.get('role', '')} · {c.get('importance', '')} · 镜 {_span(c.get('shots'))}：{c.get('appearance', '')}")
        if c.get("expressions"):
            L.append("  - 表情：" + "；".join(f"{x['name']}（镜 {_span(x.get('shots'))}）" for x in c["expressions"]))
        L += [f"  - 另一套造型（镜 {_span(o.get('shots'))}，原先认成 `{o.get('was')}`）：{o.get('appearance', '')}" for o in c.get("outfits") or []]
    if bd.get("scenes"):
        L += ["", "## 分场造型", "", "每个人在这一场穿什么、拿什么；场内换了造型的记在「镜 N 起」。", ""]
        for sc in bd["scenes"]:
            L.append(f"### {sc['id']} 镜 {sc['shots'][0]}–{sc['shots'][1]}：{sc.get('location', '')}（{sc.get('time', '')}）{day_tag(sc)}")
            L += [f"- `{k}`：{look_line(v)}" for k, v in (sc.get("looks") or {}).items()]
            L += [f"- 镜 {c['shot']} 起 `{c['who']}`：{look_line(c)}" for c in sc.get("changes") or []] + [""]
    return "\n".join(L)


def _scene_lines(story: dict, scenes: list[dict]) -> list[str]:
    at = {s["index"]: s for s in story["shots"]}
    out = []
    for n, sc in enumerate(scenes, 1):
        a, b = sc["shots"][:2]
        f = {"seen": _one(f"{sc.get('location', '')}（{sc.get('time', '')}）{day_tag(sc)}", 60)}
        if sc.get("present"):
            f["x-cast"] = _one("、".join(map(str, sc["present"])), 60)
        out.append(" | ".join([f"#S{n:02d} {at[a]['start']:.2f}-{at[b]['end']:.2f}", f"shots {a}-{b}"] + [f"{k} {v}" for k, v in f.items()]))
    return out


def _vtt(story: dict, called: dict) -> str:
    def ts(x: float) -> str:
        ms = int(round(float(x) * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"

    cues = []
    for x in unique_lines(story):
        who = str(x.get("speaker") or "")
        if who == "title" or not x.get("text"):
            continue
        name = re.sub(r"[<>\s]+", " ", called.get(who) or who).strip()
        mode = MODE_CN.get(line_mode(x))
        voice = f"<v {name}{'（' + mode + '）' if mode else ''}>" if name else ""
        cues.append(f"{ts(x['start'])} --> {ts(max(x['end'], x['start'] + 0.01))}\n{voice}{x['text']}")
    return "WEBVTT\n\n" + "\n\n".join(cues) + "\n" if cues else ""


# ---- the entry: card + overview + map ----

def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def name_of(work: Path) -> str:
    """The card name of a work dir: its folder name, with the series' in front for an episode."""
    root = layout.series_of(work)
    return f"{_named(root)}-{work.name}" if root else _named(work)


def _named(d: Path) -> str:
    """A folder's name as a card name; one that has no letters or digits to keep (a Chinese title) by a short hash of
    it, so two such folders are not both called `video`."""
    return slug(d.name) or "v-" + hashlib.sha1(d.name.encode()).hexdigest()[:8]


def _card(story: dict, work: Path, rep: dict | None, fmt: dict, has: dict) -> dict:
    src = story.get("source") or {}
    read = (rep or {}).get("read") or {}
    root, ep = layout.series_of(work), layout.episode_no(work)
    about = _one(read.get("logline") or story.get("summary") or _title(story, work), 600)
    where = f"《{_series_title(root)}》第 {ep} 集。" if root else ""
    made = f"{fmt['cn']}，{story.get('duration', 0):.0f} 秒 {len(story['shots'])} 镜。" if fmt else ""
    source = {"file": layout.source(work).name}
    if str(src.get("input", "")).startswith(("http://", "https://")):
        source["url"] = src["input"]
    if (digest := _sha256(layout.source(work))):
        source["sha256"] = digest
    source["duration"] = round(float(story.get("duration") or 0), 3)
    if story.get("width") and story.get("height"):
        source.update(size=f"{story['width']}x{story['height']}", fps=story.get("fps"), aspect=story.get("aspect"))
    about += "" if not about or about[-1] in "。！？.!?…" else "。"
    card = {FORMAT: VERSION, "name": name_of(work), "description": _one(f"{where}{about}{made}", 1000) or work.name,
            "source": {k: v for k, v in source.items() if v not in (None, "")}, "shots": len(story["shots"])}
    if has.get("scenes"):
        card["scenes"] = has["scenes"]
    card["lang"] = "zh"
    readers = ["tools"]
    if any(s.get("analysis") for s in story["shots"]):
        readers.append("agent" if any(layout.analysis(work, "agent").glob("*.answer.json")) else "model")
    if story.get("lock"):
        readers.append("human")
    card.update(readers=readers, open_questions=len([x for x in story.get("lint") or [] if x.get("for") != "deepen"]), generator={"name": "rea-toon", "version": __version__})
    if has.get("transcript"):
        card["tracks"] = {"transcript": f"{DIR}/transcript.vtt"}
    if root:
        related = [{"rel": "part_of", "name": _named(root)}]
        before = root / f"ep{ep - 1:02d}"
        if ep > 1 and layout.story(before).is_file():
            related.append({"rel": "sequel_of", "name": name_of(before)})
        card["related"] = related
    if fmt:
        card["x-format"] = fmt["id"]
    card["x-review"] = lock_state(story)
    return card


def _series_title(root: Path) -> str:
    try:
        return str(json.loads(layout.series(root).read_text(encoding="utf-8")).get("title") or root.name)
    except (OSError, ValueError):
        return root.name


def _overview(story: dict, work: Path, rep: dict | None, fmt: dict, files: list[tuple[str, int, str]]) -> str:
    bd = story.get("breakdown") or {}
    read, m = (rep or {}).get("read") or {}, (rep or {}).get("metrics") or {}
    L = [f"# {_title(story, work)}", ""]
    if read.get("logline"):
        L.append(f"**一句话卖点**：{_one(read['logline'], 200)}")
    if story.get("summary"):
        L.append(f"**讲什么**：{_one(story['summary'], 300)}")
    if fmt:
        L.append(f"**类型**：{fmt['cn']}（{fmt['why']}）")
    if bd.get("beats"):
        L.append("**戏剧骨架**：" + " → ".join(f"#{_span(b.get('shots'))} {ROLE_CN.get(b['type'], b['type'])}{'（' + b['feel'] + '）' if b.get('feel') else ''}"
                                               for b in bd["beats"]))
    if m:
        first = "—" if m.get("first_peak") is None else f"{m['first_peak']:.1f}s"
        L.append(f"**情绪**：{m.get('peaks', 0)} 个情绪节点，第一个在 {first}，{'有' if m.get('cliffhanger') else '没有'}结尾钩子（情绪账见 `{DIR}/x-report.md`）")
    L.append(f"**审核状态**：{STATE_CN[lock_state(story)]}")
    if not any(s.get("analysis") for s in story["shots"]):
        L.append("**还没拆**：只切了镜头、读了字幕，没有角色和逐镜拆解（`rea-toon analyze` 接着做）。")
    elif not bd:
        L.append("**还没做深度拆解**：没有分场造型、戏剧节拍和逐镜看点（`rea-toon redo breakdown`）。")
    lint = [x for x in story.get("lint") or [] if x.get("for") != "deepen"]
    L += ["", "## 要处理的问题", ""]
    L += [f"- {'必须修' if x['level'] == 'error' else '要确认'}：{'镜 ' + str(x['shot']) + '，' if x.get('shot') else ''}{_one(x['msg'], 160)}" for x in lint[:12]] or ["- 没有"]
    if len(lint) > 12:
        L.append(f"- 另有 {len(lint) - 12} 处，见 `{DIR}/timeline.md` 里带 `flag` 的行和对应的镜头文件")
    L += [f"- 要细拆时：{_one(x['msg'], 160)}" for x in story.get("lint") or [] if x.get("for") == "deepen"]
    L += ["", "## 地图", "", "先读本页；要细节时只打开需要的文件。每行是 `路径 | 约 token | 里面有什么 · 什么时候读`，路径相对本目录。", "", "```videomd-map"]
    L += [f"{p} | {n or '-'} | {_one(about)}" for p, n, about in files] + ["```"]
    L += ["", "## 读法", ""] + [f"- {x}" for x in LEGEND]
    L += ["- 「看到的」可以对着 `frames/` 里的帧核实；戏剧作用、情绪、必须保住什么是拆解的判断，两者对不上时以画面为准。",
          "- 这些 `.md` 是从 `story.json` 生成的，改了会被覆盖：要纠正拆解就改 `story.json`，再 `rea-toon redo pages <目录>` 重出。"]
    return "\n".join(L)


# L4: what else is in the work dir, for the map (path, what it is · when to read it)
_RAW = (
    ("story.json", "剧本源码，全部字段 · 程序读；要改拆解时改它"),
    ("data/report.json", "爆款拆解的数据：节奏曲线、情绪账、戏份 · 程序读"),
    ("data/subtitles.json", "字幕轨：每句的时间、位置、OCR 原文 · 程序读"),
    ("frames/", "每镜的首 / 中 / 尾帧 · 看图核实"),
    ("characters/", "每个角色的头像、造型图、配音样本 · 做角色参考"),
    ("decomp/video/sound.md", "声音（rea-video 拆的）：人声、配乐、音效和节拍 · 听配音配乐"),
    ("decomp/", "rea-video 量出来的：切点、人声 / 配乐分轨 · 听声音"),
    ("pages/", "给人看的页面（从 index.html 进）· 模型不用读"),
    ("source.mp4", "原片"),
)


def write(work: Path, story: dict | None = None) -> Path:
    """Write the package of one episode's work dir (VIDEO.md + video/) from its story.json and report.json.
    Returns the entry file."""
    story = story if story is not None else json.loads(layout.story(work).read_text(encoding="utf-8"))
    rp = layout.data(work, "report.json")
    rep = json.loads(rp.read_text(encoding="utf-8")) if rp.is_file() else None
    from .report import series_format
    fmt = (rep or {}).get("format") or (format_of(story, series_format(work)) if (story.get("breakdown") or story.get("style")) else {})
    called = cast_names(work)
    d = layout.package(work)
    if d.is_dir():
        shutil.rmtree(d)  # generated: a re-run with another shot count must not leave stale shot files
    files: list[tuple[str, int, str]] = []

    def put(name: str, text: str, about: str) -> None:
        layout.put(d / name, text)
        files.append((f"{DIR}/{name}", tokens(text), about))

    scenes = _scenes(story)
    if scenes:
        lines = _scene_lines(story, scenes)
        body = "\n".join(["# 场景", "", "每行一场：`#S场号 起-止(秒) | shots 首镜-末镜 | seen 地点和时间 | x-cast 在场的角色 id`。", "", "```videomd-scenes"] + lines + ["```"])
        put("scenes.md", _sized({FORMAT: VERSION, "part": "scenes", "scenes": len(lines)}, body), "场景：一行一场 · 先看这一集分几场、每场在哪")
    scene_no = {i: n for n, sc in enumerate(scenes, 1) for i in range(sc["shots"][0], sc["shots"][1] + 1)}
    lint = story.get("lint") or []
    lint_shots = {x["shot"] for x in lint if x.get("shot")}
    told: set = set()
    lines = [timeline_line(s, lint_shots, told) for s in story["shots"]]
    body = "\n".join(["# 时间线", ""] + LEGEND + ["", "```videomd-timeline"] + lines + ["```"])
    put("timeline.md", _sized({FORMAT: VERSION, "part": "timeline", "shots": len(lines)}, body), "时间线：一行一个镜头 · 某段讲什么、谁说了什么、哪镜是看点、哪镜要确认")
    if story.get("summary") or story.get("breakdown"):
        put("content.md", _sized({FORMAT: VERSION, "part": "content"}, _content(story)), "内容与结构 · 剧情、前情、戏剧节拍、信息差")
    if story.get("characters"):
        put("cast.md", _sized({FORMAT: VERSION, "part": "cast"}, _cast(story, called)), "角色、造型与画风 · 谁是谁、每场穿什么、画风规则；做角色参考图时读")
    if rep and rep.get("metrics"):
        from .report import SCHEMA, to_md
        if rep.get("schema") == SCHEMA:
            put("x-report.md", _sized({FORMAT: VERSION, "part": "x-report"}, to_md(rep)), "爆款拆解 · 为什么火：卖点、情绪账、套路、人设、可复用公式、改编方向")
    shot_tokens = 0
    for s in story["shots"]:
        text = _shot_file(story, s, work, scene_no, lint)
        layout.put(d / "shots" / f"{s['index']:02d}.md", text)
        shot_tokens += tokens(text)
    if story["shots"]:
        files.append((f"{DIR}/shots/*.md", shot_tokens // len(story["shots"]), "逐镜细节，token 是每个文件的平均值 · 核实某一镜：画面、台词、造型、戏剧作用、这一镜不能丢什么"))
    vtt = _vtt(story, called)
    if vtt:
        layout.put(d / "transcript.vtt", vtt)
        files.append((f"{DIR}/transcript.vtt", tokens(vtt), "台词轨（WebVTT，带说话人和时间）· 查谁在什么时候说了什么"))
    for rel, about in _RAW:
        p = work / rel.rstrip("/")
        if p.exists():
            files.append((rel, tokens(p.read_text(encoding="utf-8")) if rel.endswith((".json", ".md")) else 0, about))
    has = {"scenes": len(scenes), "transcript": bool(vtt)}
    return layout.put(layout.entry(work), _front(_card(story, work, rep, fmt, has), _overview(story, work, rep, fmt, files)))


def write_series(root: Path, ser: dict) -> Path:
    """SERIES.md of a series root: what a model reads first about the whole drama. The conclusions across episodes,
    then one line per episode's package. (VIDEO.md describes one video; this file is rea-toon's own, not part of it.)"""
    r, rows = ser.get("read") or {}, [x for x in ser.get("rows") or [] if not x.get("missing")]
    from .genre import FORMATS
    L = [f"# {ser.get('title') or root.name}", ""]
    L += [f"**{name}**：{_one(r[k], 300)}" for k, name in (("logline", "一句话卖点"), ("main_conflict", "主冲突"), ("engine", "情绪打法")) if r.get(k)]
    L.append(f"**规模**：{len(ser.get('episodes') or [])} 集，拆完 {len(rows)} 集，共 {sum(x['duration'] for x in rows) / 60:.1f} 分钟"
             + (f"；类型：{FORMATS[ser['format']]['cn']}" if ser.get("format") in FORMATS else ""))
    cast = [c for c in ser.get("characters") or [] if c.get("name")][:12]
    if cast:
        L.append("**主要角色**：" + "、".join(f"{c['name']}（`{c['id']}`，{_one(c.get('role'), 16)}）" for c in cast))
    from .series import _context_lines
    L += ([""] + _context_lines(ser)[:-1]) if _context_lines(ser) else []
    L += ["", "## 地图", "", "每行是 `路径 | 约 token | 里面有什么 · 什么时候读`，路径相对本目录。每集是一个 VIDEO.md 拆片包，从它的 `VIDEO.md` 读起。", "", "```"]
    whole = layout.page(root, "series.md")
    if whole.is_file():
        L.append(f"{layout.rel(whole, root)} | {tokens(whole.read_text(encoding='utf-8'))} | 跨集拆解全文 · 分阶段打法、人物弧光、伏笔和回收、集尾钩子、付费卡点、跨集情绪账")
    script = layout.page(root, "script.md")
    if script.is_file():
        L.append(f"{layout.rel(script, root)} | {tokens(script.read_text(encoding='utf-8'))} | 全剧剧本（逐集逐场）· 要读原文台词和动作时")
    for x in rows:
        entry = layout.entry(root / x["dir"])
        if entry.is_file():
            about = _one(x.get("logline") or x.get("summary"), 60)
            L.append(f"{layout.rel(entry, root)} | {tokens(entry.read_text(encoding='utf-8'))} | 第 {x['ep']} 集（{x['duration']:.0f}s，{x['peaks']} 个情绪节点）：{about}")
    L += ["```", "", "## 读法", "", "- 问整部剧（套路、结构、人物）先读本页，不够再读跨集拆解全文；问某一集读那一集的 `VIDEO.md`，逐层往下。",
          "- 这页和各集的拆片包都是生成的：跨集的内容改 `series.json`，某一集改它的 `story.json`，再 `rea-toon redo pages <目录>`。"]
    meta = {"series": ser.get("title") or root.name, "name": _named(root), "episodes": len(ser.get("episodes") or []), "analysed": len(rows),
            "generator": {"name": "rea-toon", "version": __version__}}
    return layout.put(layout.series_entry(root), _front(meta, "\n".join(L)))


def check(work: Path) -> list[str]:
    """What keeps `work` from being a conforming VIDEO.md package (empty: it conforms), by the reference validator."""
    try:
        import videomd
    except ImportError as exc:
        raise RuntimeError("校验要用 videomd 包：pip install videomd") from exc
    return videomd.check(work)
