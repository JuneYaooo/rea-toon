"""Shared look of the review pages (breakdown.html, report.html, series.html) and the small SVG charts in them."""

from __future__ import annotations

import html

CSS = """
:root{--bg:#fafaf8;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e4e3de;--must:#c2410c;--high:#a16207;--err:#b91c1c;--warn:#a16207;--chip:#f1f0eb;--series:#2a78d6;--grid:#ecebe6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161615;--card:#1f1f1d;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--must:#fb923c;--high:#facc15;--err:#f87171;--warn:#facc15;--chip:#2a2926;--series:#3987e5;--grid:#2c2b28}}
:root[data-theme=dark]{--bg:#161615;--card:#1f1f1d;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--must:#fb923c;--high:#facc15;--err:#f87171;--warn:#facc15;--chip:#2a2926;--series:#3987e5;--grid:#2c2b28}
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
"""

TIP_JS = """<div id=tip></div><script>(function(){var t=document.getElementById('tip');
document.addEventListener('mouseover',function(e){var x=e.target.closest('[data-tip]');if(!x){t.style.display='none';return}
t.textContent=x.getAttribute('data-tip');t.style.display='block'});
document.addEventListener('mousemove',function(e){t.style.left=Math.min(e.clientX+12,innerWidth-330)+'px';t.style.top=(e.clientY+14)+'px'});})();</script>"""


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def page(title: str, body: list[str]) -> str:
    return ("<!doctype html><html lang=zh><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)}</title><style>{CSS}</style></head><body><main>" + "\n".join(body) + "</main>" + TIP_JS + "</body></html>")


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
