"""Shared look of the pages (breakdown.html, report.html, series.html) and the small SVG charts in them; the site
(index.html) that shows them. Pages sit in pages/ of a work dir (layout.py), one level under what they point at."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from . import layout

CSS = """
:root{--bg:#fafaf8;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e4e3de;--must:#c2410c;--high:#a16207;--err:#b91c1c;--warn:#a16207;--chip:#f1f0eb;--series:#2b6ef2;--grid:#ecebe6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161615;--card:#1f1f1d;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--must:#fb923c;--high:#facc15;--err:#f87171;--warn:#facc15;--chip:#2a2926;--series:#7aa5ff;--grid:#2c2b28}}
:root[data-theme=dark]{--bg:#161615;--card:#1f1f1d;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--must:#fb923c;--high:#facc15;--err:#f87171;--warn:#facc15;--chip:#2a2926;--series:#7aa5ff;--grid:#2c2b28}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,"PingFang SC","Noto Sans SC",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.muted{color:var(--muted)}.state{display:inline-block;padding:2px 10px;border-radius:99px;background:var(--chip);font-weight:600}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(260px,1fr))}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.card img{width:100%;border-radius:6px;display:block}.pics{display:flex;gap:6px}.pics img{width:50%}
.chip{display:inline-block;padding:1px 8px;margin:2px 4px 2px 0;border-radius:99px;background:var(--chip);font-size:12px}
.must{color:var(--must);font-weight:700}.high{color:var(--high);font-weight:600}.err{color:var(--err)}.warn{color:var(--warn)}
.shot{display:grid;grid-template-columns:200px 1fr;gap:12px;padding:10px 0;border-top:1px solid var(--line)}.shot img{width:100%;border-radius:6px}
.shot.must{border-left:3px solid var(--must);padding-left:10px}.lines div{margin:2px 0}.off{color:var(--muted)}
.scene{margin:18px 0 4px;font-weight:600}ul{margin:4px 0;padding-left:20px}
@media (max-width:640px){.shot{grid-template-columns:1fr}}
.stats{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.stat b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.stat span{color:var(--muted);font-size:12px}
.chart{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:8px 0}.chart svg{width:100%;height:auto;display:block;overflow:visible}
.chart text{fill:var(--muted);font-size:11px}.chart .ln{fill:none;stroke:var(--series);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.chart .gr{stroke:var(--grid);stroke-width:1}.chart .bar{fill:var(--series)}.chart .dot{fill:var(--series);stroke:var(--card);stroke-width:2}
.chart .hit{fill:transparent;cursor:default}.chart .hit:hover+.dot,.chart .bar:hover{opacity:.75}
#tip{position:fixed;pointer-events:none;background:var(--fg);color:var(--bg);padding:4px 8px;border-radius:6px;font-size:12px;max-width:320px;display:none;z-index:9}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-top:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:500;white-space:nowrap}td.n{white-space:nowrap}
.tw{overflow-x:auto}details summary{cursor:pointer;color:var(--muted);margin:6px 0}
table.heat{border-collapse:separate;border-spacing:2px;width:auto}table.heat th{font-weight:400;color:var(--muted);border:0;padding:4px 10px;text-align:center;white-space:nowrap}
.statline{color:var(--muted);margin:10px 0 0}.statline b{color:var(--fg);font-variant-numeric:tabular-nums}.statline span{white-space:nowrap;margin-right:16px}
.lead{font-size:18px;font-weight:700;margin:12px 0 6px}h3{font-size:14px;margin:18px 0 6px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:0 32px}@media (max-width:760px){.two{grid-template-columns:1fr}}
details.sec{border-top:1px solid var(--line)}details.sec:last-of-type{border-bottom:1px solid var(--line)}
details.sec>summary{list-style:none;display:flex;align-items:baseline;gap:12px;margin:0;padding:13px 2px;color:var(--fg)}details.sec>summary::-webkit-details-marker{display:none}
details.sec>summary::before{content:"+";width:14px;color:var(--muted);font-weight:600;flex:none}details.sec[open]>summary::before{content:"–"}
details.sec>summary b{font-size:15px;flex:none}details.sec>summary span{color:var(--muted);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
details.sec>summary:hover b{text-decoration:underline}details.sec[open]{padding-bottom:16px}.more{margin-top:36px}.more>p{margin:0 0 8px}
a.ref{color:var(--series);cursor:pointer;white-space:nowrap;text-decoration:none;border-bottom:1px dotted currentColor}a.ref:hover{border-bottom-style:solid}
.tl{display:grid;grid-template-columns:54px 1fr;gap:12px;padding:8px 0;border-top:1px solid var(--line);align-items:start}.tl:first-of-type{border-top:0}
.tl img{width:54px;height:72px;object-fit:cover;border-radius:5px;display:block;cursor:pointer;background:var(--chip)}.tl .muted{font-size:12px;margin-left:6px}
.mb{display:inline-block;width:70px;height:7px;border-radius:4px;background:var(--chip);margin-right:8px;vertical-align:middle;overflow:hidden}.mb i{display:block;height:100%;background:var(--series);border-radius:4px}
#pl{position:fixed;right:16px;bottom:16px;width:250px;background:var(--card);border:1px solid var(--line);border-radius:12px;box-shadow:0 8px 30px #0004;z-index:20;overflow:hidden;font-size:12px}
#pl[hidden]{display:none}#pl video,#pl img{width:100%;max-height:58vh;display:block;background:#000;object-fit:contain}
#pl .plh{display:flex;justify-content:space-between;align-items:center;padding:7px 10px}#pl .plh a{cursor:pointer;padding:2px 7px;font-size:15px;color:var(--muted);user-select:none}#pl .plh a:hover{color:var(--fg)}
#plc{padding:7px 10px 9px;color:var(--muted);line-height:1.45}
.roles{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(170px,1fr))}.roles>div{display:grid;grid-template-columns:48px 1fr;gap:9px;align-items:start;font-size:13px}
.roles img{width:48px;height:64px;object-fit:cover;border-radius:5px;cursor:pointer;background:var(--chip)}.roles .muted{font-size:12px}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 0;margin:8px 0 0;display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center;font-size:13px}
.bar button{border:0;background:var(--chip);color:inherit;font:inherit;padding:5px 12px;border-radius:99px;cursor:pointer}.bar button.on{background:var(--series);color:#fff}
.bar .jump{display:flex;gap:2px;overflow-x:auto;flex:1;min-width:0;scrollbar-width:none}.bar .jump a{color:var(--muted);text-decoration:none;padding:4px 7px;border-radius:6px;flex:none}.bar .jump a:hover{background:var(--chip);color:var(--fg)}
.bar label{color:var(--muted);cursor:pointer;white-space:nowrap}
.scene{scroll-margin-top:52px}.scene .muted{font-weight:400;font-size:13px;margin-left:6px}details.looks{margin:0 0 6px;font-size:13px}details.looks summary{margin:2px 0}
.shot{grid-template-columns:84px 1fr;font-size:13.5px}.shot.must,.shot.high{color:inherit;font-weight:inherit}.shot.high{border-left:3px solid var(--line);padding-left:10px}.shot img{cursor:pointer;aspect-ratio:3/4;object-fit:cover}.shot .hd{font-size:12px}
.x{display:none}main.all .x{display:block}main.f-key .shot:not(.must):not(.high),main.f-risk .shot:not(.risk){display:none}
main.f-key .scene.none,main.f-risk .scene.none,main.f-key details.looks,main.f-risk details.looks{display:none}
@media print{details.sec>summary::before{display:none}#pl,.bar{display:none!important}}
table.heat tr>th:first-child{text-align:right}table.heat td{border:0;border-radius:4px;min-width:56px;height:30px;line-height:30px;padding:0 8px;text-align:center;vertical-align:middle;font-variant-numeric:tabular-nums;color:var(--fg)}
"""

TIP_JS = """<div id=tip></div><script>(function(){var t=document.getElementById('tip');
document.addEventListener('mouseover',function(e){var x=e.target.closest('[data-tip]');if(!x){t.style.display='none';return}
t.textContent=x.getAttribute('data-tip');t.style.display='block'});
document.addEventListener('mousemove',function(e){t.style.left=Math.min(e.clientX+12,innerWidth-330)+'px';t.style.top=(e.clientY+14)+'px'});})();</script>"""


def point(x) -> str:
    """One list item as text: models sometimes answer a list of sentences with objects ({"risk", "evidence"})."""
    if isinstance(x, dict):
        return "：".join(str(v) for v in x.values() if v not in (None, "", [], {}))
    return str(x if x is not None else "")


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def page(title: str, body: list[str], tail: str = "") -> str:
    return ("<!doctype html><html lang=zh><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)}</title><style>{CSS}</style></head><body><main>" + "\n".join(body) + "</main>" + TIP_JS + tail + "</body></html>")


# ---- how a page is laid out: what it concludes is on the page, what backs it is one line to open ----

def fold(title: str, hint: str, body: str, open: bool = False) -> str:
    """A section that is one line until opened: the evidence behind a page's conclusions. hint says what is inside."""
    if not body:
        return ""
    return (f"<details class=sec{' open' if open else ''}><summary><b>{esc(title)}</b><span>{esc(hint)}</span></summary>"
            f"{body}</details>")


def statline(items: list[tuple[str, object]]) -> str:
    """The few numbers of a page on one line (not a row of cards)."""
    return "<p class=statline>" + "".join(f"<span><b>{esc(v)}</b> {esc(k)}</span>" for k, v in items) + "</p>"


def meter(v: float, top: float, text: str) -> str:
    """A number in a table cell with a small bar before it: a column read at a glance, no chart of its own."""
    return f"<span class=mb><i style='width:{100 * (v or 0) / (top or 1):.0f}%'></i></span>{esc(text)}"


def ref(shots, text: str | None = None) -> str:
    """A shot (or a run of shots) named on a page, as something to click: the player shows it."""
    try:
        xs = [int(i) for i in (shots if isinstance(shots, (list, tuple)) else [shots])]
    except (TypeError, ValueError):
        return esc(text if text is not None else shots)
    if not xs:
        return esc(text or "")
    a, b = min(xs), max(xs)
    label = text if text is not None else (str(a) if a == b else f"{a}–{b}")
    return f"<a class=ref data-shot={a}{'' if a == b else f' data-to={b}'}>{esc(label)}</a>"


_PLAYER = """<div id=pl hidden><div class=plh><b id=plt></b><span><a id=plp title="上一镜">‹</a><a id=pln title="下一镜">›</a><a id=plx title="关闭">×</a></span></div>
<video id=plv playsinline controls preload=none></video><img id=pli alt=""><div id=plc></div></div>
<script>(function(){var S=__SHOTS__,V=__VIDEO__,by={},d=document,cur=null,end=0,dead={};S.forEach(function(s){by[s[0]]=s});
var re=/镜\\s?(\\d+)(?!\\d|\\.\\d)(?:\\s?[–~-]\\s?(\\d+))?/g,root=d.querySelector('main'),w=d.createTreeWalker(root,NodeFilter.SHOW_TEXT,null),ns=[],n;
if(S.length)while(n=w.nextNode()){re.lastIndex=0;if(re.test(n.nodeValue)&&!n.parentNode.closest('a,button,script,style,svg,code'))ns.push(n)}
ns.forEach(function(n){var f=d.createDocumentFragment(),t=n.nodeValue,i=0,m;re.lastIndex=0;
 while(m=re.exec(t)){if(!by[+m[1]])continue;f.appendChild(d.createTextNode(t.slice(i,m.index)));var a=d.createElement('a');a.className='ref';
  a.setAttribute('data-shot',m[1]);if(m[2]&&by[+m[2]])a.setAttribute('data-to',m[2]);a.textContent=m[0];f.appendChild(a);i=m.index+m[0].length}
 f.appendChild(d.createTextNode(t.slice(i)));n.parentNode.replaceChild(f,n)});
var pl=d.getElementById('pl'),v=d.getElementById('plv'),im=d.getElementById('pli'),now=null;
function sec(x){return x.toFixed(1)}
function play(o){now=o;end=o.b;pl.hidden=false;d.getElementById('plt').textContent=o.title+' · '+sec(o.a)+'–'+sec(o.b)+'s';
 d.getElementById('plc').textContent=o.cap||'';
 if(o.v&&!dead[o.v]){im.style.display='none';v.style.display='';
  var go=function(){v.currentTime=o.a+0.02;var p=v.play();if(p&&p.catch)p.catch(function(){})};
  if(v.getAttribute('src')!=o.v){v.src=o.v;v.addEventListener('loadedmetadata',go,{once:true});v.load()}
  else if(v.readyState>0)go();else v.addEventListener('loadedmetadata',go,{once:true})}
 else{v.pause();v.style.display='none';im.style.display='';im.src=o.img||''}}
function shot(a,b){var s=by[a],e=by[b]||s;if(!s)return;cur={a:a,b:e[0]};
 play({v:V,a:s[1],b:e[2],title:'镜 '+a+(e[0]!=a?'–'+e[0]:''),cap:s[4],img:s[3]})}
function clip(x){cur={el:x};play({v:x.getAttribute('data-v'),a:+x.getAttribute('data-a'),b:+x.getAttribute('data-b'),
 title:x.getAttribute('data-title')||'',cap:x.getAttribute('data-cap'),img:x.getAttribute('src')})}
v.addEventListener('timeupdate',function(){if(end&&v.currentTime>=end){v.pause();end=0}});
v.addEventListener('error',function(){if(now&&now.v){dead[now.v]=1;play(now)}});
d.addEventListener('click',function(e){var x=e.target.closest('[data-shot],[data-a]');if(!x)return;e.preventDefault();
 if(x.hasAttribute('data-a'))clip(x);else shot(+x.getAttribute('data-shot'),+(x.getAttribute('data-to')||x.getAttribute('data-shot')))});
function step(k){if(!cur)return;if(cur.el){var all=[].slice.call(d.querySelectorAll('[data-a]')).filter(function(x){return x.offsetParent!==null}),
  x=all[all.indexOf(cur.el)+k];if(x){clip(x);x.scrollIntoView({block:'nearest'})}}
 else{var i=k<0?cur.a-1:cur.b+1;if(by[i])shot(i,i)}}
function shut(){v.pause();pl.hidden=true}
d.getElementById('plx').onclick=shut;d.getElementById('plp').onclick=function(){step(-1)};d.getElementById('pln').onclick=function(){step(1)};
d.addEventListener('keydown',function(e){if(pl.hidden)return;if(e.key=='Escape')shut();
 else if(e.key=='ArrowLeft'){e.preventDefault();step(-1)}else if(e.key=='ArrowRight'){e.preventDefault();step(1)}});})();</script>"""


def up(path) -> str:
    """A file named relative to the work dir (a frame in story.json, a character's picture), as a page reaches it."""
    return f"../{path}" if path else ""


def player(story: dict | None, video: str | None = None) -> str:
    """What makes every shot named on a page playable: 镜 45 (written by code or by the model, in any sentence)
    becomes a link, and a click plays that stretch of the source in a corner of the page (the shot's frame when the
    video is not next to the page). Without a story (a page of several episodes) it plays what the page marks itself:
    any element with data-v (video), data-a / data-b (seconds), data-title, data-cap. Goes in page(..., tail=)."""
    shots = (story or {}).get("shots") or []
    rows = []
    for s in shots:
        talk = " / ".join(str(x.get("text", "")) for x in s.get("subtitles") or [] if not x.get("card"))
        what = str((s.get("key") or {}).get("function") or (s.get("analysis") or {}).get("action_summary") or "")
        rows.append([s["index"], round(float(s["start"]), 2), round(float(s["end"]), 2), up((s.get("frames") or {}).get("mid")),
                     (what + ("　「" + talk + "」" if talk else ""))[:160]])
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    return _PLAYER.replace("__SHOTS__", data).replace("__VIDEO__", json.dumps(video))


# ---- links between the pages of a series ----

_NAV = re.compile(r"<nav class=sitenav>.*?</nav>(<div class=sitenav-sp></div>)?", re.S)
_DUP = re.compile(r"<a href='storyboard\.html'>分镜脚本 →</a>|<a href='script\.html'>← 剧本</a>")  # the site's bar has them
_EP = ("report.html", "breakdown.html")
P = layout.PAGES

# What every page of a series carries. Inside the site (index.html shows it in a frame) it adds nothing to see; opened
# on its own it shows one line back to the site. ?ep=N keeps one episode of the script / the shot script.
_INNER = ("<nav class=sitenav><style>.sitenav{display:none;font:13px/1 -apple-system,'PingFang SC',sans-serif;padding:10px 16px;"
          "border-bottom:1px solid #8884}.sitenav a{color:inherit}html.alone .sitenav{display:block}html.framed .toc{display:none!important}"
          "@media print{.sitenav{display:none!important}}</style><a href='%s'>← %s</a>"
          "<script>(function(){var d=document,m=/[?&]ep=(\\d+)/.exec(location.search);"
          "d.documentElement.classList.add(window.top!==window.self?'framed':'alone');"
          "if(m){var s=d.createElement('style');s.textContent='section.ep:not(#ep'+m[1]+'),.toc{display:none!important}section.ep{margin-top:0!important}section.ep>h2{font-size:22px;line-height:1.55;margin:0 0 4px;padding:0;border:0;text-align:left;letter-spacing:0}';d.head.appendChild(s);"
          "d.addEventListener('DOMContentLoaded',function(){var k=d.querySelector('section.ep');"  # one episode: no front matter
          "if(k)[].forEach.call(k.parentNode.children,function(x){if(!x.matches('section.ep,nav,style,script'))x.style.display='none'})})}"
          "if(window.top!==window.self)d.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a[href]'),"
          "k=a&&/^\\.\\.\\/ep0*(\\d+)\\/pages\\/report\\.html$/.exec(a.getAttribute('href'));"
          "if(k){e.preventDefault();parent.postMessage({rtGo:+k[1]},'*')}});"
          "})()</script></nav>")

_SITE = """<!doctype html><html lang=zh><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>__TITLE__ · rea-toon</title><style>
:root{--bg:#fafaf8;--bar:#fff;--fg:#1d1d1b;--muted:#77766f;--line:#e4e3de;--chip:#f1f0eb;--on:#2b6ef2;--soft:#2b6ef21c}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--bar:#1c1c1a;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--chip:#2a2926;--on:#7aa5ff;--soft:#7aa5ff2b}}
*{box-sizing:border-box}html,body{height:100%;margin:0}body{display:flex;flex-direction:column;background:var(--bg);color:var(--fg);font:14px/1 -apple-system,"PingFang SC","Noto Sans SC",sans-serif}
header{flex:none;background:var(--bar);border-bottom:1px solid var(--line)}
.row{max-width:1180px;margin:0 auto;padding:0 16px;display:flex;align-items:center}
.top{height:52px;gap:12px}.sub{height:42px;gap:14px}.hr{height:1px;background:var(--line)}
.brand{display:flex;align-items:center;gap:8px;flex:none;color:inherit;text-decoration:none}
.brand svg{width:30px;height:31px;display:block}.brand:hover b{color:var(--on)}.brand span{display:flex;flex-direction:column;gap:4px}
.brand b{font:800 18px/1 Manrope,'Segoe UI',system-ui,-apple-system,'Helvetica Neue',Arial,sans-serif;letter-spacing:-.04em}.brand i{font-style:normal;color:var(--on)}
.brand small{font-size:11px;font-weight:400;letter-spacing:.04em;color:var(--muted)}
.slash{color:var(--line);font-size:20px;font-weight:300;flex:none}
h1{font-size:15px;font-weight:600;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 1 auto}
.tabs{display:flex;align-self:stretch;gap:22px;margin-left:auto;flex:none;padding-left:16px}
.tabs button{border:0;background:none;color:var(--muted);font:inherit;font-size:14px;padding:2px 1px 0;cursor:pointer;border-bottom:2px solid transparent}
.tabs button:hover{color:var(--fg)}.tabs button.on{color:var(--fg);font-weight:600;border-bottom-color:var(--on)}
.gh{flex:none;color:var(--muted);display:flex;margin-left:6px}.gh:hover{color:var(--fg)}.gh svg{width:18px;height:18px;fill:currentColor}
.eps{display:flex;gap:4px;align-items:center;overflow-x:auto;scrollbar-width:none;flex:1;min-width:0}.eps::-webkit-scrollbar{display:none}
.eps button{flex:none;border:0;background:none;color:var(--muted);font:inherit;font-size:13px;font-variant-numeric:tabular-nums;height:28px;min-width:30px;padding:0 9px;border-radius:8px;cursor:pointer}
.eps button:hover{background:var(--chip);color:var(--fg)}.eps button.on{background:var(--soft);color:var(--on);font-weight:700}
.eps span{flex:none;width:1px;height:16px;background:var(--line);margin:0 8px}
.more{flex:none;display:flex;gap:12px;font-size:13px}.more a{color:var(--muted);cursor:pointer;text-decoration:none}.more a:hover{color:var(--fg)}
iframe{flex:1;width:100%;border:0;background:var(--bg)}
@media (max-width:640px){.slash,h1,.brand small{display:none}.tabs{gap:16px}}
</style></head><body>
<header><div class="row top"><a class=brand href="https://github.com/JuneYaooo/rea-toon" target=_blank rel=noopener title="rea-toon：AI 拆解学习短剧">
<svg viewBox="8 8 68 70" aria-hidden=true fill=none><path d="M27 16h34a8 8 0 0 1 8 8v30" stroke="var(--on)" stroke-width=6 stroke-linecap=round /><path d="M20 27h39a8 8 0 0 1 8 8v18a8 8 0 0 1-8 8H28a8 8 0 0 1-8-8V27Z" fill="var(--on)"/><path d="M20 50v24l17-14H20Z" fill="var(--on)"/><g fill="var(--bar)"><rect x=32 y=37 width=7 height=14 rx=3.5 /><rect x=50 y=37 width=7 height=14 rx=3.5 /></g></svg><span><b>rea-toon<i>.</i></b><small>AI 拆解学习短剧</small></span></a>
<span class=slash>/</span><h1 title="__TITLE__">__TITLE__</h1>
<nav class=tabs id=views></nav>
<a class=gh href="https://github.com/JuneYaooo/rea-toon" target=_blank rel=noopener title="GitHub：JuneYaooo/rea-toon" aria-label=GitHub><svg viewBox="0 0 16 16"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg></a></div>
<div class=hr id=hr></div><div class="row sub" id=sub><div class=eps id=eps></div><div class=more id=more></div></div></header>
<iframe id=f title="内容"></iframe>
<script>
var EPS=__EPS__,HAS=__HAS__,P="__PAGES__/",VIEWS=[["breakdown","学习分析"],["script","剧本"],["board","分镜拆解"]];
var st={ep:0,view:"breakdown"};
function src(){var e=EPS.filter(function(x){return x.n==st.ep})[0];
 if(!EPS.length&&st.view!="script"&&st.view!="board")return P+(HAS.report?"report.html":"breakdown.html");
 if(st.view=="audit"&&e)return e.dir+"/"+P+"breakdown.html";
 if(st.view=="script")return P+"script.html"+(e?"?ep="+e.n:"");
 if(st.view=="board")return P+"storyboard.html"+(e?"?ep="+e.n:"");
 return e?e.dir+"/"+P+"report.html":P+"series.html"}
function btn(text,on,fn,tip){var b=document.createElement("button");b.textContent=text;if(on)b.className="on";if(tip)b.title=tip;b.onclick=fn;return b}
function draw(){var v=document.getElementById("views"),p=document.getElementById("eps"),m=document.getElementById("more");
 v.innerHTML="";VIEWS.forEach(function(x){if(x[0]=="script"&&!HAS.script)return;if(x[0]=="board"&&!HAS.board&&!st.ep)return;
  v.appendChild(btn(x[1],st.view==x[0]||(x[0]=="board"&&st.view=="audit"),function(){go(st.ep,x[0])}))});
 p.innerHTML="";if(EPS.length)p.appendChild(btn("全剧",st.ep==0,function(){go(0,st.view=="audit"?"breakdown":st.view)}));
 if(EPS.length)p.appendChild(document.createElement("span"));
 EPS.forEach(function(e){p.appendChild(btn(e.n,st.ep==e.n,function(){go(e.n,st.view)},"第 "+e.n+" 集"))});
 m.innerHTML="";function link(t,fn,href){var a=document.createElement("a");a.textContent=t;if(href){a.href=href;a.target="_blank"}else a.onclick=fn;m.appendChild(a)}
 if(!st.ep&&st.view=="script"&&HAS.script_pdf)link("下载 PDF",null,P+"script.pdf");
 if(!st.ep&&st.view=="board"&&HAS.board_pdf)link("下载 PDF",null,P+"storyboard.pdf");
 document.getElementById("sub").style.display=document.getElementById("hr").style.display=EPS.length||m.children.length?"":"none";
 var on=p.querySelector(".on");if(on&&on.scrollIntoView)on.scrollIntoView({block:"nearest",inline:"nearest"})}
function go(ep,view,quiet){st={ep:ep,view:view};if(view=="audit")st.view=HAS.board?"board":ep?"audit":"breakdown";
 if(ep&&view=="board"&&!HAS.board)st.view="audit";if(st.view=="script"&&!HAS.script)st.view="breakdown";
 document.getElementById("f").src=src();draw();if(!quiet)history.replaceState(null,"","#"+(st.ep||"all")+"/"+st.view)}
addEventListener("message",function(e){var n=e.data&&e.data.rtGo;if(n&&EPS.some(function(x){return x.n==n}))go(n,"breakdown")});
(function(){var m=/^#(all|\\d+)\\/(\\w+)$/.exec(location.hash);go(m&&m[1]!="all"?+m[1]:0,m?m[2]:"breakdown",true)})();
</script></body></html>
"""


def _episodes(root: Path) -> list[Path]:
    return [d for d in layout.episodes(root) if any(layout.page(d, f).is_file() for f in _EP)]


def _series_title(root: Path) -> str:
    try:
        return str(json.loads(layout.series(root).read_text(encoding="utf-8")).get("title") or root.name)
    except (OSError, ValueError):
        pass
    try:  # one video: its own title, without the post's hashtags
        title = (json.loads(layout.story(root).read_text(encoding="utf-8")).get("source") or {}).get("title") or ""
        return re.split(r"\s*#", title)[0].strip() or root.name
    except (OSError, ValueError):
        return root.name


def _single(d: Path) -> bool:
    """A video broken down on its own (not an episode of a series): it gets the same site, without the episode row."""
    return layout.story(d).is_file() and not layout.series(d).is_file() and not layout.series_of(d)


def cast_names(work: Path) -> dict[str, str]:
    """What the series calls each character of this episode (id in the episode -> the name settled across episodes):
    an episode page then says 容遇, not the long role the episode's own cast table gave. Empty for a single video or
    before the series has merged its cast."""
    root = layout.series_of(work)
    try:
        cast = json.loads(layout.series(root).read_text(encoding="utf-8")).get("characters") or [] if root else []
    except (OSError, ValueError):
        return {}
    tag = f"E{layout.episode_no(work)}:" if root else ""
    return {x[len(tag):]: str(c["name"]) for c in cast if c.get("name") for x in c.get("members") or [] if x.startswith(tag)}


def _root(file: Path) -> tuple[Path, str, int] | None:
    """(folder of the site, path up to it, episode number or 0) for a page of a series or of one video's breakdown;
    None for a page that belongs to no site."""
    d = file.parent.parent  # the page is in pages/ of d
    inside = layout.is_episode_name(d.name) and len(_episodes(d.parent)) > 1
    root = d.parent if inside else d
    if _episodes(root):
        return root, "../../" if inside else "../", layout.episode_no(d) if inside else 0
    return (d, "../", 0) if _single(d) else None


def stamp(file: Path) -> None:
    """Mark a page already written as one of a series' site (again: replaces the old mark). The site itself is
    index.html (site()): one bar that never reloads, with this page shown under it."""
    if not file.is_file():
        return
    text = _NAV.sub("", file.read_text(encoding="utf-8"))
    at = _root(file)
    if at:
        root, home, ep = at
        view = {"script.html": "script", "storyboard.html": "board", "breakdown.html": "audit"}.get(file.name, "breakdown")
        bar = _INNER % (esc(f"{home}{layout.SITE}#{ep or 'all'}/{view}"), esc(_series_title(root)))
        text = _DUP.sub("", text)
        m = re.search(r"<body[^>]*>", text)
        text = text[:m.end()] + bar + text[m.end():] if m else bar + text
    file.write_text(text, encoding="utf-8")


def site(root: Path) -> Path | None:
    """index.html: the one page to open. Two choices on one bar, which episode (or the whole drama) and what of it
    (the study / the script / the shot script), and the page they pick shown underneath. No page leads to another.
    One video on its own gets the same site without the episode row."""
    eps = _episodes(root)
    if not eps and not (_single(root) and any(layout.page(root, f).is_file() for f in _EP)):
        return None
    has = {k: layout.page(root, f).is_file() for k, f in (("report", "report.html"), ("script", "script.html"), ("board", "storyboard.html"),
                                                          ("script_pdf", "script.pdf"), ("board_pdf", "storyboard.pdf"))}
    out = layout.site(root)
    out.write_text(_SITE.replace("__TITLE__", esc(_series_title(root))).replace("__PAGES__", P)
                   .replace("__EPS__", json.dumps([{"n": int(e.name[2:]), "dir": e.name} for e in eps]))
                   .replace("__HAS__", json.dumps(has)), encoding="utf-8")
    return out


def link_up(root: Path) -> Path | None:
    """Mark every page of a series (or of one video's breakdown) and (re)write its index.html."""
    for f in [layout.page(d, f) for d, fs in [(root, ("series.html", "script.html", "storyboard.html", *_EP))] + [(e, _EP) for e in _episodes(root)] for f in fs]:
        stamp(f)
    return site(root)


def heatmap(rows: list[tuple[str, list[float]]], cols: list[str], unit: str = "", note=None) -> str:
    """A table whose cells are shaded by their value (one hue, light to dark, one scale for the whole table): where
    the weight sits is seen before a number is read. rows: (label, values per column); empty cells stay blank.
    note(row index, column index, value) -> hover text."""
    top = max((v or 0 for _, vs in rows for v in vs), default=0) or 1
    out = ["<div class=tw><table class=heat><tr><th></th>" + "".join(f"<th>{esc(c)}</th>" for c in cols) + "</tr>"]
    for i, (label, vs) in enumerate(rows):
        cells = []
        for j, v in enumerate(vs):
            if not v:
                cells.append("<td></td>")
                continue
            tip = note(i, j, v) if note else f"{label} · {cols[j]}：{v:g}{unit}"
            # 12%..62% of the series hue over the surface: the ink on top stays readable in both themes
            cells.append(f"<td style='background:color-mix(in srgb,var(--series) {12 + 50 * v / top:.0f}%,var(--card))' "
                         f"data-tip='{esc(tip)}'>{v:g}</td>")
        out.append(f"<tr><th>{label}</th>" + "".join(cells) + "</tr>")
    return "".join(out) + "</table></div>"


def line_chart(points: list[tuple[float, float]], marks: list[tuple[float, float, str]], x_label: str, y_max: float,
               tips: list[str] | None = None, w: int = 900, h: int = 180) -> str:
    """One series over time (2px line), markers for events, hover tips; x in seconds."""
    if not points:
        return ""
    pad_l, pad_b, pad_t = 28, 22, 8
    x1 = max(p[0] for p in points) or 1
    X = lambda x: pad_l + (w - pad_l - 8) * x / x1
    Y = lambda y: pad_t + (h - pad_t - pad_b) * (1 - min(y, y_max) / y_max)
    grid = "".join(f"<line class=gr x1={pad_l} x2={w - 8} y1={Y(v):.1f} y2={Y(v):.1f} />"
                   f"<text x=0 y={Y(v) + 4:.1f}>{v:g}</text>" for v in (0, y_max / 2, y_max))
    step = 10 if x1 <= 120 else 30 if x1 <= 360 else 60
    ticks = "".join(f"<text x={X(t):.1f} y={h - 4} text-anchor=middle>{int(t)}s</text>" for t in range(0, int(x1) + 1, step))
    path = "M" + " L".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in points)
    hits = "".join(f"<rect class=hit x={X(x) - 6:.1f} y={pad_t} width=12 height={h - pad_t - pad_b} data-tip='{esc(tip)}' />"
                   for (x, _), tip in zip(points, tips or [])) if tips else ""
    dots = "".join(f"<g><circle class=hit cx={X(x):.1f} cy={Y(y):.1f} r=9 data-tip='{esc(tip)}' />"
                   f"<circle class=dot cx={X(x):.1f} cy={Y(y):.1f} r=4.5 /></g>" for x, y, tip in marks)
    return (f"<div class=chart><svg viewBox='0 0 {w} {h}' role=img aria-label='{esc(x_label)}'>{grid}{ticks}"
            f"<path class=ln d='{path}' />{hits}{dots}</svg></div>")


def bar_chart(rows: list[tuple[str, float, str]], unit: str = "", w: int = 900, bar: int = 18) -> str:
    """Horizontal bars of one measure (label, value, tip), largest first as given."""
    if not rows:
        return ""
    top = max(v for _, v, _ in rows) or 1
    lab = 150
    h = len(rows) * (bar + 10) + 4
    out = []
    for k, (name, v, tip) in enumerate(rows):
        y = 2 + k * (bar + 10)
        bw = max(6.0, (w - lab - 70) * v / top)
        out.append(f"<text x={lab - 8} y={y + bar * 0.72:.1f} text-anchor=end>{esc(name[:14])}</text>"
                   f"<path class=bar data-tip='{esc(tip)}' d='M{lab},{y} h{bw - 4:.1f} q4,0 4,4 v{bar - 8} q0,4 -4,4 h-{bw - 4:.1f} z' />"
                   f"<text x={lab + bw + 6:.1f} y={y + bar * 0.72:.1f}>{v:g}{esc(unit)}</text>")
    return f"<div class=chart><svg viewBox='0 0 {w} {h}' role=img>{''.join(out)}</svg></div>"
