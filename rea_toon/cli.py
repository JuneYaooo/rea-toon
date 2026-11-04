"""rea-toon: remake a short drama with new characters or a new art style, keeping plot, shots and emotion."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .env import load_env


def _work(p: str) -> Path:
    w = Path(p).expanduser().resolve()
    return w


def cmd_ingest(a):
    from .ingest import ingest
    meta = ingest(a.source, _work(a.work))
    print(json.dumps(meta, ensure_ascii=False, indent=1))


def cmd_analyze(a):
    from .analyze import analyze
    w = _work(a.work)
    if a.source:
        from .ingest import ingest
        ingest(a.source, w)
    cuts = [float(x) for x in a.cuts.replace("，", ",").split(",") if x.strip()] if a.cuts else None
    story = analyze(w, use_vlm=not a.no_vlm, workers=a.workers, cuts=cuts)
    print(f"{len(story['shots'])} 镜，{len(story['characters'])} 个角色 → {w / 'story.md'}")
    for c in story["characters"]:
        print(f"  {c['id']:<18} {c.get('role', ''):<8} {c.get('importance', ''):<10} 镜头 {c.get('shots')}")


def cmd_init(a):
    w = _work(a.work)
    story = json.loads((w / "story.json").read_text(encoding="utf-8"))
    lines = [f"name: {a.name}", "# 全局画风；留空 = 保持原片画风（只换角色）。例：\"3D 皮克斯动画电影风格\"", "style:",
             "image_channel: cyber      # gpt-image-2 渠道：cyber | krill | juling | openai",
             "video_engine: h3local     # h3local（自部署 H3 ComfyUI，推荐）| h3（AutoDL 托管 API）| seedance（火山方舟 Seedance 2.0）",
             "frames: first_last        # first_last = 首尾帧约束（更贴原镜头）| first = 只用首帧",
             "lipsync: true             # H3：被换角色的对白镜头用原人声驱动口型", "subtitles: reburn",
             "shots: all                # 只复刻这些镜头，其余用原片；例 \"1,28-30\"",
             "workers: 1                # 单卡自部署 H3 用 1；托管 API 可 4", "",
             "# 下面列出了全部角色（含群众 crowd）。要换的取消注释并填写；没换的会保持原片的样子，`plan` 会列出来。",
             "# “换成某某 IP 的全员”时，配角和群众也要写，否则成片里会站满原片的人。", "cast:"]
    for c in story["characters"]:
        lines += [f"  # {c['id']}: {c.get('role')}，出现在 {c.get('shots')}", f"  #   原外貌：{c.get('appearance')}",
                  f"  # {c['id']}:", "  #   name: 新角色名", "  #   look: 新角色的长相/体型/发型", "  #   costume: keep   # keep = 穿原角色的衣服",
                  "  #   image: ./ref.png  # 可选：新角色参考图", ""]
    out = w / "remakes" / a.name / "remake.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not a.overwrite:
        sys.exit(f"{out} 已存在（--overwrite 覆盖）")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


def _remake(a):
    from .remake import Remake
    from .spec import RemakeSpec
    w = _work(a.work)
    spec = RemakeSpec.load(a.spec)
    if getattr(a, "engine", None):
        spec.video_engine = a.engine
    return Remake(w, spec, Path(a.out).resolve() if getattr(a, "out", None) else None)


def _only(a, r) -> list[int] | None:
    from .spec import parse_shots
    expr = getattr(a, "redo", None) or getattr(a, "shots", None)
    return parse_shots(expr, len(r.story["shots"])) if expr else None


def cmd_plan(a):
    r = _remake(a)
    p = r.plan()
    only = _only(a, r)
    print(f"重做 {p['regen_shots']} 镜 / 复用原片 {p['reuse_shots']} 镜；图片调用 ≈{p['image_calls']}，"
          f"视频 {p['video_calls']} 段（原片时长 {p['video_seconds']}s）")
    for s in p["shots"]:
        if s["route"] == "regen" and (not only or s["index"] in only):
            print(f"  #{s['index']:<3} 换 {','.join(s['swapped']) or '-':<14} 帧 {s['frames']}  模式 {s['mode']}")
    if p["kept_roles"]:
        print("保持原片样子的角色（确认这是用户要的；要换就写进 cast）：")
        for k in p["kept_roles"]:
            print(f"  {k['id']:<18} {k['role']:<8} {k['importance']:<10} 镜头 {k['shots']}")
    print(f"试跑镜头（run --pilot）：{p['pilot']}")
    print(f"→ {r.out / 'plan.json'}")


def _check(r, only=None) -> dict:
    from .qa import check
    rep = check(r.work, r.out, r.plan()["shots"], only=only)
    if rep["problems"]:
        print(f"自查发现 {len(rep['problems'])} 个问题（{rep['md']}）：")
        for x in rep["problems"]:
            print(f"  - {x}")
    else:
        print(f"自查没发现问题；还要人工看 check_frames.jpg、check_subs.jpg 和对白镜头（{rep['md']}）")
    return rep


def cmd_run(a):
    from .assemble import assemble
    r = _remake(a)
    only = _only(a, r)
    if a.pilot:
        only = r.plan()["pilot"]
        print(f"试跑镜头：{only}（覆盖对白、群众、大运镜；跑完看自查结果再决定是否跑全片）")
    force = set(a.force.split(",")) if a.force else set()
    r.keep = set(a.keep.split(",")) if a.keep else set()
    res = r.run(until="clips" if a.pilot else a.until, force=force, only=only)
    failed = sorted(i for i, v in res.get("results", {}).items() if v.get("error"))
    if (a.pilot or a.until == "clips") and not a.no_assemble:
        out = assemble(r.work, r.out, res["plan"]["shots"], reburn=r.spec.subtitles == "reburn")
        print(json.dumps({k: out[k] for k in ("final", "compare", "missing") if k in out}, ensure_ascii=False, indent=1))
        _check(r, only=only if a.pilot or a.shots or a.redo else None)
    if failed:
        print(f"失败镜头：{failed}（重跑同一命令即可续跑，已完成的不会重复计费）")


def cmd_assemble(a):
    from .assemble import assemble
    r = _remake(a)
    print(json.dumps(assemble(r.work, r.out, r.plan()["shots"], reburn=r.spec.subtitles == "reburn"), ensure_ascii=False, indent=1))


def cmd_check(a):
    r = _remake(a)
    _check(r, only=_only(a, r))


def cmd_proofread(a):
    from .analyze import proofread_story
    print(f"改了 {proofread_story(Path(a.work))} 条字幕（原 OCR 文本留在 ocr 字段）")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rea-toon", description=__doc__)
    ap.add_argument("--env-file", help=".env with API keys (default: ./.env, then capsule-cinema's)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="下载/导入参考视频（本地文件、直链、抖音分享文本）")
    p.add_argument("source"); p.add_argument("work"); p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("analyze", help="拆剧本：切镜、字幕、角色表、逐镜情绪/运镜 → story.json / story.md")
    p.add_argument("work"); p.add_argument("--source", help="先导入这个视频")
    p.add_argument("--no-vlm", action="store_true"); p.add_argument("--workers", type=int, default=6)
    p.add_argument("--cuts", help="强制在这些时间点（秒）切镜头，如 6.65,9.92：溶解/穿门/遮挡转场切不出来、或一镜超过 15s 时用")
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("proofread", help="校对 OCR 字幕的形近错字（analyze 会自动做；给旧的工作目录补做）")
    p.add_argument("work"); p.set_defaults(fn=cmd_proofread)

    p = sub.add_parser("init", help="按角色表生成 remake.yaml 模板")
    p.add_argument("work"); p.add_argument("name"); p.add_argument("--overwrite", action="store_true")
    p.set_defaults(fn=cmd_init)

    for name, fn, hlp in (("plan", cmd_plan, "看重做哪些镜头、调用多少次模型（不花钱）"),
                          ("run", cmd_run, "生成：角色设定图 → 关键帧 → 视频 → 拼回成片"),
                          ("assemble", cmd_assemble, "只重新拼接成片"),
                          ("check", cmd_check, "交付前自查：时间线、漏换镜头、口型同步、中段抽帧、字幕")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("work"); p.add_argument("spec", help="remake.yaml")
        p.add_argument("--out", help="输出目录（默认 WORK/remakes/<name>）")
        p.add_argument("--shots", help="这次只处理/只看这些镜头，如 1,28-30（成片仍是整部片）")
        p.add_argument("--engine", help="覆盖 video_engine：h3 | h3local | seedance")
        if name == "run":
            p.add_argument("--until", choices=["cast", "frames", "clips"], default="clips",
                           help="先停在设定图/关键帧审一下再出视频")
            p.add_argument("--redo", help="只重做这些镜头（配合 --force）")
            p.add_argument("--force", help="强制重做的阶段：cast,frames,clips")
            p.add_argument("--keep", help="已有产物即使输入变了也保留（已审过的）：cast,frames")
            p.add_argument("--no-assemble", action="store_true")
            p.add_argument("--pilot", action="store_true", help="先试跑 plan 选出的 3 个代表镜头，出视频、拼片、自查")
        p.set_defaults(fn=fn)

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "httpx", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_env(a.env_file)
    a.fn(a)


if __name__ == "__main__":
    main()
