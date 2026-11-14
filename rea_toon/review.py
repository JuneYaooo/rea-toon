"""The review page of one episode: pages/breakdown.html, every shot with what the breakdown says of it. The same
breakdown as text is the VIDEO.md package (videomd.py)."""

from __future__ import annotations

import html
import re
from pathlib import Path

from . import layout
from .breakdown.motion import MOTION_CN
from .breakdown.screen_text import TEXT_CN
from .breakdown.staging import ANGLE_CN, FACING_CN
from .checks import RISK_CN
from .page import cast_names, fold, page, player, stamp, up
from .story import PRIORITY_CN, ROLE_CN, day_tag, gap_line as _gap_line, lines_of, lock_state, look_line, portraits, said_tag
from .subtitles import line_mode


# ---- breakdown.html ----

def _staging_text(s: dict) -> str:
    st = s.get("staging") or {}
    ppl = "；".join(f"{k} 朝{FACING_CN.get(v.get('facing'), '?')}、看{v.get('looking_at') or '?'}"
                   for k, v in (st.get("people") or {}).items())
    return "，".join(x for x in (ANGLE_CN.get(st.get("angle"), ""), ppl, st.get("power", "")) if x)


def _extras(s: dict) -> str:
    """Comic-drama elements of a shot for the review: how it moves, effects, manga marks, panels."""
    st = s.get("staging") or {}
    out = [MOTION_CN.get((s.get("motion") or {}).get("kind"), "")]
    out += [f"特效 {e['what']}（{e.get('color') or '?'}，{e.get('from') or '?'} 发出）" for e in st.get("effects") or []]
    out += ["漫符 " + "、".join(st["marks"])] if st.get("marks") else []
    out += [f"{st['panels']} 宫格"] if st.get("panels") else []
    return "；".join(x for x in out if x)


def _texts(s: dict) -> str:
    return "；".join(f"{TEXT_CN.get(t.get('kind'), '待定')}「{t['text']}」" for t in s.get("screen_text") or [])


def _risk_cn(r: str) -> str:
    return RISK_CN.get(r, r).split("：")[0]


_STYLE_CN = {"kind": "类型", "rendering": "质感", "palette": "色调", "lighting": "光线", "character_design": "人物造型", "fx": "特效和剪辑",
             "line": "线条", "texture": "材质", "aspect": "画幅"}
_RANK_CN = {"lead": "主角", "supporting": "配角", "extra": "龙套", "minor": "龙套"}


def _label(c: dict) -> str:
    """What to call a character on a page people read: the name in the role (姐姐（温月） -> 温月), else the role."""
    role = str(c.get("role") or "")
    inside = re.findall(r"[（(]([^）)]+)[）)]", role)
    return (inside[0] if inside else re.split(r"[/，,、]", role)[0]).strip() or c["id"]


def _span(shots) -> str:
    """[1, 2, 3, 5] -> 1–3、5."""
    try:
        xs = sorted({int(i) for i in shots})
    except (TypeError, ValueError):
        return str(shots or "")
    runs: list[list[int]] = []
    for i in xs:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return "、".join(str(r[0]) if len(r) == 1 else f"{r[0]}–{r[-1]}" for r in runs)


_FILTER_JS = """<script>(function(){var m=document.querySelector('main'),bs=document.querySelectorAll('.bar button');
function mark(){[].forEach.call(document.querySelectorAll('.scene'),function(h){var n=h.nextElementSibling,any=false;
 while(n&&!n.classList.contains('scene')){if(n.classList.contains('shot')&&n.offsetParent!==null)any=true;n=n.nextElementSibling}
 h.classList.toggle('none',!any)})}
[].forEach.call(bs,function(b){b.onclick=function(){[].forEach.call(bs,function(x){x.classList.toggle('on',x===b)});
 m.classList.remove('f-key','f-risk');[].forEach.call(document.querySelectorAll('.scene'),function(h){h.classList.remove('none')});
 if(b.value){m.classList.add('f-'+b.value);mark()}}});
var c=document.getElementById('all');try{c.checked=localStorage.getItem('rt-all')=='1'}catch(e){}
function all(){m.classList.toggle('all',c.checked);try{localStorage.setItem('rt-all',c.checked?'1':'0')}catch(e){}}c.onchange=all;all();})();</script>"""


def write_review(story: dict, work: Path) -> None:
    """The shot page: who is in it, then every shot in order. A shot shows what happens, what is said and what a
    shot cannot lose; the rest (staging, text on screen, how it moves) is there once 细节 is ticked. Two filters
    leave only the shots that carry the episode, or only the ones a person has to check before locking."""
    e = lambda x: html.escape(str(x or ""))
    bd = story.get("breakdown") or {}
    state = {"locked": "已锁定", "stale": "锁定后改过，需要重新 lock", "unlocked": "待审核"}[lock_state(story)]
    title = re.split(r"\s*#", (story.get("source") or {}).get("title") or "")[0] or "source"  # 3.10/3.11: no \ in f-string
    H = [f"<h1>{e(title)}</h1>",
         f"<p><span class=state>{state}</span> <span class=muted>逐镜拆解 · {len(story['shots'])} 镜 · {story.get('duration', 0):.0f}s</span></p>"]
    called = {**{c["id"]: _label(c) for c in story["characters"]}, **cast_names(work)}
    who = lambda x: called.get(x, x)
    named = lambda t: re.sub("|".join(sorted(map(re.escape, called), key=len, reverse=True)) or "$^", lambda m: called[m.group()], str(t or ""))
    lint_rows = story.get("lint") or []
    flagged = {x["shot"] for x in lint_rows if x.get("shot")}
    if lint_rows:
        H.append(f"<details><summary>审核用：{len(lint_rows)} 处要人确认（只看拆解可以跳过；审完改好后运行 "
                 f"<code>rea-toon lock {e(work.name)}</code>）</summary><ul>")
        H += [f"<li class={'err' if x['level'] == 'error' else 'warn'}>{'镜' + str(x['shot']) + '：' if x['shot'] else ''}{e(x['msg'])}</li>"
              for x in lint_rows]
        H.append("</ul></details>")
    H.append(f"<p>{e(story.get('summary'))}</p>")

    H.append("<h2>角色</h2><div class=roles>")
    for c in story["characters"]:
        pic = portraits(story, work, c["id"], 1)
        shot = next((s["index"] for s in story["shots"] if pic and work / (s.get("frames") or {}).get("mid", "") == pic[0]), None)
        outfits = "".join(f"<div class='x muted'>另一套造型（镜 {e(_span(o.get('shots')))}）：{e(o.get('appearance'))}</div>" for o in c.get("outfits") or [])
        H.append("<div>" + (f"<img loading=lazy src='{e(up(layout.rel(pic[0], work)))}' alt=''{f' data-shot={shot}' if shot else ''}>" if pic else "<span></span>")
                 + f"<div><b>{e(cast_names(work).get(c['id']) or c.get('role') or c['id'])}</b><div class=muted>{e(_RANK_CN.get(c.get('importance'), c.get('importance')))} · {e(c['id'])}</div>"
                 f"<div class='x muted'>{e(c.get('role'))}：{e(c.get('appearance'))}</div>{outfits}"
                 + (f"<div class='x muted'>表情：{e('；'.join(x['name'] for x in c['expressions']))}</div>" if c.get("expressions") else "")
                 + "</div></div>")
    H.append("</div>")

    F = []
    if bd.get("style_bible"):
        sb = bd["style_bible"]
        F.append(fold("画风档案", str(sb.get("kind") or sb.get("rendering") or "")[:60], "<ul>" + "".join(
            f"<li><b>{e(_STYLE_CN.get(k, k))}</b>：{e('、'.join(v) if isinstance(v, list) else v)}</li>" for k, v in sb.items()) + "</ul>"))
    beats = bd.get("beats") or []
    if beats:
        F.append(fold("戏剧节拍", f"{len(beats)} 拍 · 每一拍必须保住什么", "<ul>" + "".join(
            f"<li><b>{e(ROLE_CN.get(b['type'], b['type']))}</b>" + (f" <span class=chip>{e(b['feel'])}</span>" if b.get("feel") else "")
            + f" 镜 {e(_span(b.get('shots')))}：{e(b.get('what'))}<br><span class=muted>必须保住：{e(b.get('must_keep'))}"
            + (f"；兑现：{e(b['pays'])}" if b.get("pays") else "") + "</span></li>" for b in beats) + "</ul>"))
    if bd.get("info_gaps"):
        F.append(fold("信息差", f"{len(bd['info_gaps'])} 条", "<ul>" + "".join(f"<li>{e(named(_gap_line(g)))}</li>" for g in bd["info_gaps"]) + "</ul>"))
    if F:
        H.append("<div class=more>" + "".join(F) + "</div>")

    shots = {s["index"]: s for s in story["shots"]}
    scenes = bd.get("scenes") or [{"id": "", "shots": [1, len(story["shots"])]}]
    risky = lambda s: s["index"] in flagged or any(x.get("unsure") for x in lines_of(s))  # what a person has to settle before locking
    n_key = sum(1 for s in story["shots"] if (s.get("key") or {}).get("priority") in ("must", "high"))
    n_risk = sum(1 for s in story["shots"] if risky(s))
    H.append("<h2 style='border:0;margin-bottom:0'>逐镜</h2><div class=bar>"
             f"<button class=on value=''>全部 {len(story['shots'])}</button><button value=key>关键镜 {n_key}</button>"
             + (f"<button value=risk>要确认 {n_risk}</button>" if n_risk else "")
             + "<div class=jump>" + "".join(f"<a href='#sc{k}' title='{e(sc.get('location'))}'>{e(sc['id'] or '全片')}</a>" for k, sc in enumerate(scenes)) + "</div>"
             "<label><input type=checkbox id=all> 细节</label></div>")
    for k, sc in enumerate(scenes):
        H.append(f"<div class=scene id=sc{k}>{e(sc['id'])} {e(sc.get('location'))}<span class=muted>镜 {sc['shots'][0]}–{sc['shots'][1]} · "
                 f"{e(sc.get('time'))}{e(day_tag(sc))}</span></div>")
        looks = "".join(f"<li><b>{e(who(n))}</b>：{e(look_line(v))}</li>" for n, v in (sc.get("looks") or {}).items())
        looks += "".join(f"<li>镜{e(c['shot'])}起 <b>{e(who(c['who']))}</b>：{e(look_line(c))}</li>" for c in sc.get("changes") or [])
        if looks:
            H.append(f"<details class=looks><summary>这一场的造型（{len(sc.get('looks') or {})} 人"
                     + (f"，中途换 {len(sc['changes'])} 次" if sc.get("changes") else "") + f"）</summary><ul class=muted>{looks}</ul></details>")
        for i in range(sc["shots"][0], sc["shots"][1] + 1):
            s = shots.get(i)
            if not s:
                continue
            key = s.get("key") or {}
            a = s.get("analysis") or {}
            pr = key.get("priority", "normal")
            lines = "".join(f"<div class={'off' if line_mode(x) != 'on' else ''}>{e(who(x.get('speaker', '?')))}"
                            f"{e(said_tag(x))}：{e(x['text'])}{' <span class=warn>说话人待确认</span>' if x.get('unsure') else ''}</div>"
                            for x in lines_of(s))
            chips = [ROLE_CN.get(key.get("role"), ""), key.get("feel", "")] + ([key["transition_in"]] if key.get("transition_in", "cut") != "cut" else []) + list(key.get("fx") or [])
            vital = pr in ("must", "high")
            H.append(f"<div class='shot {pr}{' risk' if risky(s) else ''}'><img loading=lazy src='{e(up((s.get('frames') or {}).get('mid')))}' alt='' data-shot={i}><div>"
                     f"<div class=hd><b>#{i}</b> <span class=muted>{s['start']:.1f}–{s['end']:.1f}s · {e(a.get('shot_size'))} · {e(a.get('camera_move'))}</span> "
                     f"<span class={pr}>{PRIORITY_CN.get(pr, '')}</span> " + "".join(f"<span class=chip>{e(c)}</span>" for c in chips if c) + "</div>"
                     + f"<div>{e(a.get('action_summary'))}</div>"
                     + (f"<div class=lines>{lines}</div>" if lines else "")
                     + (f"<div{'' if vital else ' class=x'}><b>必须保住</b>：{e('；'.join(key['keep']))}</div>" if key.get("keep") else "")
                     + (f"<div class='x muted'>剧情功能：{e(key['function'])}</div>" if key.get("function") else "")
                     + (f"<div class='x muted'>画面文字：{e(_texts(s))}</div>" if s.get("screen_text") else "")
                     + (f"<div class='x muted'>机位/视线：{e(named(_staging_text(s)))}</div>" if s.get("staging") else "")
                     + (f"<div class='x muted'>画面：{e(_extras(s))}</div>" if _extras(s) else "")
                     + "".join(f"<div class=x><b>接触</b> {c['t']:.2f}s：{e(c['action'])}{'（有音效）' if c.get('sound') else ''}</div>"
                               for c in s.get("contacts") or [])
                     + (f"<div class='x muted'>留意：{e('、'.join(_risk_cn(r) for r in s.get('risks') or []))}</div>" if s.get("risks") else "")
                     + "".join(f"<div class={'err' if x['level'] == 'error' else 'warn'}>要确认：{e(x['msg'])}</div>" for x in lint_rows if x.get("shot") == i)
                     + "</div></div>")
    video = layout.from_page(work, layout.source(work)) if layout.source(work).is_file() else None
    out = layout.put(layout.page(work, "breakdown.html"), page("深度拆解", H, _FILTER_JS + player(story, video)))
    stamp(out)
