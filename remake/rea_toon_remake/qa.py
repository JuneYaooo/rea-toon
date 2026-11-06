"""Delivery self-check of an assembled remake: what to look at before telling the user it is done.

Every item here is a failure that actually reached a "finished" cut once:
- timeline: the final is shorter/longer than the source (picture drifts off the original voice);
- coverage: a regenerated shot was assembled from the original footage (partial re-runs);
- leaks: the original people "grow back" mid-clip (only visible between the keyframes);
- lip sync: H3 voices the line itself and the mouths follow its speech, not the original voice;
- subtitles: H3 burns in its own subtitles under the reburned ones.
Outputs check.md + check_frames.jpg (original | remake at 1/3 and 2/3 of every regenerated shot)
+ check_subs.jpg (the subtitle band of the final at every line).
"""
from __future__ import annotations

import json
from pathlib import Path

from .media import probe, SYNC_MIN, count_frames as _frames, font_path, run


def _font() -> str:
    f = font_path()
    return f"fontfile='{f}':" if f else ""


def _meta(clip: str | None) -> dict:
    p = Path(f"{clip}.meta.json") if clip else None
    return json.loads(p.read_text(encoding="utf-8")) if p and p.is_file() else {}


def _sheet(work: Path, final: Path, shots: list[dict], out: Path) -> Path | None:
    """original | remake at 1/3 and 2/3 of each shot, taken from the cuts themselves (not the keyframes)."""
    tmp = out.parent / "check_tmp"
    tmp.mkdir(exist_ok=True)
    rows = []
    for s in shots:
        tiles = []
        for f in (1 / 3, 2 / 3):
            t = s["start"] + s["duration"] * f
            for src, tag in ((work / "source.mp4", "o"), (final, "r")):
                p = tmp / f"{s['index']:03d}_{f:.2f}_{tag}.jpg"
                label = f"#{s['index']} {'原片' if tag == 'o' else '复刻'}"
                run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-vf",
                     f"scale=320:-2,drawtext={_font()}text='{label}':x=4:y=4:fontsize=16:fontcolor=yellow:box=1", str(p)])
                if p.is_file():
                    tiles.append(p)
        if len(tiles) == 4:
            row = tmp / f"row_{s['index']:03d}.jpg"
            run(["ffmpeg", "-v", "error", "-y", *sum((["-i", str(x)] for x in tiles), []),
                 "-filter_complex", "[0][1][2][3]hstack=4", str(row)])
            rows.append(row)
    if not rows:
        return None
    lst = tmp / "rows.txt"
    lst.write_text("".join(f"file '{r.resolve()}'\nduration 1\n" for r in rows), encoding="utf-8")
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", f"tile=1x{len(rows)}",
         "-frames:v", "1", str(out)])
    return out


def _hits_sheet(work: Path, final: Path, shots: list[dict], out: Path) -> Path | None:
    """Each contact (breakdown): original and remake just before, at and just after the moment the hit lands.
    The original mix plays the hit there, so the remake's hand / splash has to be there too."""
    tmp = out.parent / "check_tmp"
    tmp.mkdir(exist_ok=True)
    rows = []
    for s in shots:
        for k, c in enumerate(s.get("contacts") or []):
            tiles = []
            for src, tag in ((work / "source.mp4", "o"), (final, "r")):
                for dt in (-0.15, 0.0, 0.15):
                    t = min(max(s["start"] + c["t"] + dt, s["start"]), s["end"] - 0.04)
                    p = tmp / f"hit_{s['index']:03d}_{k}_{tag}_{dt:+.2f}.jpg"
                    label = f"#{s['index']} {'原片' if tag == 'o' else '复刻'} {c['t'] + dt:.2f}s" + (" 音效点" if dt == 0 and c.get("sound") else "")
                    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-vf",
                         f"scale=240:-2,drawtext={_font()}text='{label}':x=4:y=4:fontsize=14:fontcolor=yellow:box=1", str(p)])
                    if p.is_file():
                        tiles.append(p)
            if len(tiles) == 6:
                row = tmp / f"hitrow_{s['index']:03d}_{k}.jpg"
                run(["ffmpeg", "-v", "error", "-y", *sum((["-i", str(x)] for x in tiles), []),
                     "-filter_complex", "[0][1][2][3][4][5]hstack=6", str(row)])
                rows.append(row)
    if not rows:
        return None
    lst = tmp / "hits.txt"
    lst.write_text("".join(f"file '{r.resolve()}'\nduration 1\n" for r in rows), encoding="utf-8")
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", f"tile=1x{len(rows)}",
         "-frames:v", "1", str(out)])
    return out


def _subs_sheet(story: dict, final: Path, out: Path) -> Path | None:
    tmp = out.parent / "check_tmp"
    tmp.mkdir(exist_ok=True)
    tiles, seen = [], set()
    for s in story["shots"]:
        for x in s.get("subtitles") or []:
            t = round((max(x["start"], s["start"]) + min(x["end"], s["end"])) / 2, 2)
            if t in seen:
                continue
            seen.add(t)
            p = tmp / f"sub_{t:07.2f}.jpg"
            y = max(0.0, x.get("y_center", 0.9) - 0.07)
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(final), "-frames:v", "1", "-vf",
                 f"crop=iw*0.7:ih*0.14:iw*0.15:ih*{y:.3f},scale=480:-2,"
                 f"drawtext=text='#{s['index']}':x=4:y=4:fontsize=16:fontcolor=yellow:box=1", str(p)])
            if p.is_file():
                tiles.append(p)
    if not tiles:
        return None
    lst = tmp / "subs.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\nduration 1\n" for p in tiles), encoding="utf-8")
    cols = 3
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf",
         f"tile={cols}x{-(-len(tiles) // cols)}", "-frames:v", "1", str(out)])
    return out


def _frame_metas(out_dir: Path, shot: dict) -> dict:
    out = {}
    for f in sorted((out_dir / "frames").glob(f"{shot['id']}_*.png.meta.json")):
        try:
            out[f.name.split("_")[-1].split(".")[0]] = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return out


def check(work: Path, out_dir: Path, plan: list[dict], sheets: bool = True, only: list[int] | None = None) -> dict:
    """plan: the full-film shot plan; only: limit the per-shot checks (e.g. to the pilot shots)."""
    from .assemble import shot_source
    story = json.loads((work / "story.json").read_text(encoding="utf-8"))
    final = out_dir / "final.mp4"
    shots = {s["index"]: s for s in story["shots"]}
    regen = [p for p in plan if p["route"] == "regen" and (not only or p["index"] in only)]
    report: dict = {"problems": []}

    src_n, fin_n = _frames(work / "source.mp4"), _frames(final)
    fin_fps = probe(final).get("fps") or story["fps"]
    src_s, fin_s = src_n / story["fps"], fin_n / fin_fps  # the cut may run at the clips' rate (24) on a 30 fps source
    report["timeline"] = {"source_frames": src_n, "final_frames": fin_n, "source_s": round(src_s, 2),
                          "final_s": round(fin_s, 2), "final_fps": fin_fps}
    if abs(src_s - fin_s) > 0.12:
        report["problems"].append(f"成片 {fin_s:.2f}s，原片 {src_s:.2f}s：画面会和原声错位")

    rows = []
    for p in regen:
        s = shots[p["index"]]
        src = shot_source(s, p, out_dir)
        if src["status"] == "missing":
            report["problems"].append(f"#{p['index']} 计划重做，但还没有视频，成片里用的是原片")
            continue
        m = _meta(str(src["clip"]))
        if m.get("mode") == "kenburns":  # the new keyframe pushed / panned locally: nothing generated to check
            rows.append({"index": p["index"], "planned": p["mode"], "mode": "kenburns", "fallback_from": None,
                         "talks": False, "sync": None, "lag": None, "leak": ""})
            continue
        a = s.get("analysis") or {}
        lines = [x for x in s.get("subtitles") or [] if not x.get("card") and x.get("speaker") != "title"]
        if any("speaker" in x for x in lines):  # per-line speakers (analyze / refine)
            talks = any(x.get("on_screen") and x.get("speaker") in (p.get("swapped") or []) for x in lines)
        else:
            talks = bool(lines and a.get("lip_visible") and a.get("speaker") in (p.get("swapped") or []))
        sync = m.get("sync") or {}
        q = m.get("qa") or {}
        leak = q.get("leak") or {}
        ck, motion = q.get("check") or {}, q.get("motion") or {}
        if motion.get("frozen"):
            report["problems"].append(f"#{p['index']} 画面基本不动（静止帧 {motion.get('still')}，原片 {motion.get('orig_still')}），会看着卡")
        if ck and not ck.get("identity_ok", True):
            report["problems"].append(f"#{p['index']} 角色中途变样或出现了人：{ck.get('identity')}")
        if ck and not ck.get("mouth_ok", True):
            report["problems"].append(f"#{p['index']} 说话的嘴不对：{ck.get('mouth')}")
        if ck and not ck.get("gaze_ok", True):
            report["problems"].append(f"#{p['index']} 朝向或视线和原片不一样：{ck.get('gaze')}")
        for name, fm in _frame_metas(out_dir, s).items():
            lost = (fm.get("check") or {}).get("lost_text")
            if lost:
                report["problems"].append(f"#{p['index']} {name} 帧场景里的字没保住：{'、'.join(lost)}")
        over = [t["text"] for t in s.get("screen_text") or [] if t.get("kind") == "overlay"]
        if over:
            report.setdefault("overlays", []).append(f"#{p['index']}：{'、'.join(over)}")
        if "guard" not in m:
            report["problems"].append(f"#{p['index']} 这段视频还没经过成片检查（旧版本生成的），重跑 run 会补检查")
        row = {"index": p["index"], "planned": p["mode"], "mode": src.get("mode"),
               "fallback_from": m.get("fallback_from"), "talks": talks,
               "sync": sync.get("corr"), "lag": src.get("av_lag"), "leak": leak.get("problems") if leak.get("leak") else ""}
        rows.append(row)
        if talks and (sync.get("corr") is None or sync["corr"] < SYNC_MIN):
            report["problems"].append(f"#{p['index']} 口型可能对不上（同步 {sync.get('corr')}，模式 {row['mode']}）")
        if leak.get("leak") and row["mode"] == "ref_video":
            report["problems"].append(f"#{p['index']} 原片的人可能漏回来：{leak.get('problems')}")
    report["shots"] = rows
    for f in sorted((out_dir / "cast").glob("*.png.meta.json")):
        try:
            lk = json.loads(f.read_text(encoding="utf-8")).get("likeness") or {}
        except (OSError, json.JSONDecodeError):
            continue
        if lk.get("risk"):
            report["problems"].append(f"设定图 {f.name.split('.')[0]} 可能长得像真实明星（过审风险）：{lk.get('reason')}")

    if sheets and final.is_file():
        report["frames_sheet"] = str(_sheet(work, final, [shots[p["index"]] for p in regen], out_dir / "check_frames.jpg"))
        report["subs_sheet"] = str(_subs_sheet(story, final, out_dir / "check_subs.jpg"))
        hits = _hits_sheet(work, final, [shots[p["index"]] for p in regen], out_dir / "check_hits.jpg")
        report["hits_sheet"] = str(hits) if hits else None

    md = ["# 交付自查", "", f"- 时间线：原片 {src_n} 帧（{src_s:.2f}s）/ 成片 {fin_n} 帧（{fin_s:.2f}s，{fin_fps:g} fps）",
          f"- 检查 {len(regen)} 个重做镜头，其中降级 {sum(1 for r in rows if r['fallback_from'])} 镜", "",
          "## 需要处理", ""] + ([f"- {x}" for x in report["problems"]] or ["- 无"]) + [
          "", "## 还要人工看", "",
          "- `check_frames.jpg`：每个重做镜头中段的原片 | 复刻。被换的人是否全程都是新形象（群众、配角会在中途“长回来”）、表情方向是否一致",
          "- 同一个人换装后（乔装、女扮男装）是不是还是同一个新形象：看 story.md 的角色表有没有把一个人拆成两个",
          "- `check_subs.jpg`：每条字幕是否只有一层（H3 会自己烧字幕）",
          "- 抽几段对白镜头带声音看：嘴是否在台词处张合",
          *(["- `check_hits.jpg`：每个身体接触（扇、泼、抓、推、递）原片和复刻在接触前/时/后的画面：动作是不是落在同一时刻"
             "（原声那一刻有音效），手和身体有没有融在一起、换了人；带声音看一遍这几镜"] if report.get("hits_sheet") else []),
          *([f"- 重画的花字（位置、颜色、出现时间对不对；字体是通用字体）：{'；'.join(report['overlays'])}"]
            if report.get("overlays") else []), "",
          "## 逐镜", "", "| 镜 | 计划 | 实际 | 降级自 | 对白 | 同步 | 平移(s) |", "|---|---|---|---|---|---|---|"]
    md += [f"| {r['index']} | {r['planned']} | {r['mode']} | {r['fallback_from'] or ''} | {'是' if r['talks'] else ''} | "
           f"{'' if r['sync'] is None else r['sync']} | {'' if r['lag'] is None else r['lag']} |" for r in rows]
    (out_dir / "check.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    report["md"] = str(out_dir / "check.md")
    return report
