"""What kind of short drama this is, and what a breakdown of that kind has to look at.

The format is worked out from the analysis (no model call): how much of the talk is narration, how much of the picture
is a still image being pushed around, what the style bible says. Each format comes with a lens: the things a writer or
producer studies in that kind of show, handed to the report prompt and shown in the report.
"""

from __future__ import annotations

from .subtitles import line_mode, unique_lines

FORMATS = {
    "live_action": {
        "cn": "真人短剧",
        "lens": ["演员表演和台词冲突：每场戏谁压谁、靠哪句台词翻盘",
                 "打脸/反转的节奏：铺垫几镜、爆点几秒、反应镜头给多长",
                 "场景和道具成本：几个景、几套衣服撑起一集"],
    },
    "ai_live": {
        "cn": "AI 真人感短剧",
        "lens": ["人设靠什么一眼立住：造型、名字卡、第一句台词",
                 "画面奇观在哪：AI 才拍得出的场面（排场、特效、动物/非人角色）占多少",
                 "镜头短、口型少的段落怎么靠剪辑和配乐维持情绪"],
    },
    "anime_2d": {
        "cn": "2D 漫剧",
        "lens": ["漫画语言：分格、漫符、特效、速度线在哪些镜头放大情绪",
                 "人设和变脸：主角几种表情撑起整集",
                 "旁白/内心独白和画面怎么分工"],
    },
    "anime_3d": {
        "cn": "3D 动画短剧",
        "lens": ["动作和特效奇观的位置和时长", "角色设计记忆点（体型、配色、标志性道具）",
                 "镜头运动比真人剧大多少、靠运镜制造的爽感"],
    },
    "motion_comic": {
        "cn": "动态漫画",
        "lens": ["一张图撑多久：静图推拉的节奏和切图频率",
                 "旁白/对话气泡承担了多少叙事",
                 "哪些格子做了局部动或特效，钱花在哪"],
    },
    "shadiao": {
        "cn": "沙雕漫",
        "lens": ["反差和梗的密度：多久一个笑点、靠台词梗还是画面梗",
                 "简笔/Q 版人设靠什么一眼认出：配色、标志物、口头禅",
                 "配音和音效怎么撑起笑点（变声、拟声、鬼畜重复）"],
    },
    "commentary_static": {
        "cn": "静态解说漫",
        "lens": ["解说词结构：开头钩子句、悬念句、反问、金句各在哪",
                 "一张图配多少句解说、多久换图，换图是不是落在信息点上",
                 "语速和信息密度：每秒几个字、多久抛一个新信息"],
    },
    "commentary": {
        "cn": "动态解说漫",
        "lens": ["解说词结构：开头钩子句、悬念句、反问、金句各在哪",
                 "画面和解说的配合：说到哪切到哪、有没有画不对词",
                 "语速和信息密度：每秒几个字、多久抛一个新信息"],
    },
}


def _narration_share(story: dict) -> float:
    lines = unique_lines(story)
    if not lines:
        return 0.0
    chars = lambda xs: sum(len(str(x.get("text", ""))) for x in xs)
    return chars([x for x in lines if line_mode(x) == "narration"]) / max(1, chars(lines))


def _still_share(story: dict) -> float:
    shots = story.get("shots") or []
    total = sum(float(s.get("duration") or 0) for s in shots)
    still = sum(float(s.get("duration") or 0) for s in shots if (s.get("motion") or {}).get("kind") in ("still", "pan"))
    return still / total if total else 0.0


def format_of(story: dict, series: str | None = None) -> dict:
    """{"id", "cn", "why", "lens"} for the drama. A hand-set story["format"] (one of FORMATS) wins; then `series`,
    the format the whole series settled on (one drama is one format: judged episode by episode, a live-action series
    had two episodes come out as AI drama)."""
    given = story.get("format")
    if given in FORMATS:
        return {"id": given, **FORMATS[given], "why": "story.json 里手动指定"}
    if series in FORMATS:
        return {"id": series, **FORMATS[series], "why": "按整部剧定的类型"}
    kind = str(((story.get("breakdown") or {}).get("style_bible") or {}).get("kind") or story.get("style") or "")
    narr, still = _narration_share(story), _still_share(story)
    style = f"{kind} {story.get('style') or ''}"
    if narr >= 0.6:
        fid, why = ("commentary_static", f"{narr:.0%} 的台词是旁白，{still:.0%} 的时长是静图") if still >= 0.5 else \
                   ("commentary", f"{narr:.0%} 的台词是旁白，画面是动的")
    elif "沙雕" in style:
        fid, why = "shadiao", f"画风：{kind or story.get('style')}"
    elif still >= 0.5:
        fid, why = "motion_comic", f"{still:.0%} 的时长是静图或静图推拉"
    elif "动态漫" in kind:
        fid, why = "motion_comic", f"画风：{kind}"
    elif "3D" in kind.upper():
        fid, why = "anime_3d", f"画风：{kind}"
    elif any(w in kind for w in ("2D", "2d", "动漫", "漫画", "国漫", "漫剧", "赛璐璐")):
        fid, why = "anime_2d", f"画风：{kind}"
    elif "实拍" in kind or "真人短剧" in kind:
        fid, why = "live_action", f"画风：{kind}"
    elif "AI" in kind.upper():
        fid, why = "ai_live", f"画风：{kind}"
    else:
        fid, why = "ai_live", f"画风：{kind or '未知'}（默认按 AI 真人感短剧）"
    return {"id": fid, **FORMATS[fid], "why": why}
