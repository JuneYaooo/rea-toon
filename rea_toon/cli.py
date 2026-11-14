"""rea-toon：逆向拆解短剧（真人 / AI / 漫剧 / 动态漫 / 解说，单集或整部剧）。

拆成可复查的剧本源码（story.json）。每个目录顶上三样东西：index.html 给人看（学习分析 / 剧本 / 分镜拆解），
VIDEO.md 给模型和下游读（拆片包，正文在 video/），story.json 是唯一手改的文件；其余按用途放在 pages/ data/ frames/ …
整部剧用 series 跨集合并角色、看主线、伏笔和集与集的钩子。

流程：analyze（感知 → 核对 → 深度拆解 → 报告）→ 人审 pages/breakdown.html → 要细拆再 deepen → lock。
analyze 重跑只补没做完的步骤，不会覆盖改过的 story.json；改过 story.json 后只重做某一步：redo <步骤>。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import layout
from .env import load_env

REDO = {
    "subtitles": "重读字幕轨（只认字幕行上的字，衣服/招牌/车身上的字不再当台词），同步进 story.json，已标的说话人保留",
    "proofread": "校对 OCR 字幕的形近错字，同步进 story.json（analyze 会自动做）",
    "refine": "重标逐句说话人、合并被拆成两个的同一角色（换装/乔装）",
    "breakdown": "重做深度拆解（改了角色、字幕、说话人之后；只改了看点/造型就别重做，直接 lock）",
    "report": "重出爆款拆解报告（不动 story.json，不会让锁失效）",
    "pages": "只重出页面和拆片包 VIDEO.md（不调模型，不动 story.json / report.json；单集目录或整部剧目录都行）",
}


def _work(p: str) -> Path:
    return Path(p).expanduser().resolve()


def _cast_summary(story: dict, w: Path) -> None:
    print(f"{len(story['shots'])} 镜，{len(story['characters'])} 个角色 → {layout.entry(w)}（给模型读的拆片包）")
    for c in story["characters"]:
        print(f"  {c['id']:<18} {c.get('role', ''):<8} {c.get('importance', ''):<10} 镜头 {c.get('shots')}")


def _review_summary(story: dict, w: Path) -> None:
    bd = story.get("breakdown") or {}
    must = [s["index"] for s in story["shots"] if (s.get("key") or {}).get("priority") == "must"]
    print(f"{len(bd.get('scenes') or [])} 场，{len(bd.get('beats') or [])} 个戏剧节拍，必保镜头 {must}")
    errors = [x for x in story.get("lint") or [] if x["level"] == "error"]
    warns = [x for x in story.get("lint") or [] if x["level"] != "error"]
    for x in errors + warns:
        print(f"  {'✗' if x['level'] == 'error' else '!'} {'镜' + str(x['shot']) + '：' if x['shot'] else ''}{x['msg']}")
    print(f"审核：{layout.page(w, 'breakdown.html')}（要处理的问题也列在 {layout.entry(w)}）；改了 story.json 后 "
          f"rea-toon redo report {w} 刷新报告")


def _report_summary(rep: dict, w: Path) -> None:
    m, r = rep["metrics"], rep.get("read") or {}
    print(f"爆款拆解（{rep['format']['cn']}）：{r.get('logline') or ''}")
    print(f"  {m['duration']:.0f}s · {m['shots']} 镜 · 每分钟 {m['cuts_per_min']} 刀 · 第一个情绪节点 {m['first_peak']}s · "
          f"情绪节点 {m['peaks']} 个 · {'有' if m['cliffhanger'] else '没有'}结尾钩子")
    for g in m["dead_zones"]:
        print(f"  ! {g['from']}–{g['to']}s 连续 {g['len']}s 没有情绪节点")
    print(f"  → {layout.page(w, 'report.html')}")


def _script_summary(root: Path) -> None:
    from .script import write
    sc = write(root)
    pics = sum(1 for c in sc["characters"] if c.get("pics"))
    print(f"剧本：{len(sc['episodes'])} 集 · {sum(len(e['scenes']) for e in sc['episodes'])} 场 · "
          f"{len(sc['characters'])} 个角色（{pics} 个有图）→ " + "、".join(p.name for p in sc["files"])
          + f"（在 {root / layout.PAGES}；角色图和配音样本在 characters/）")


def cmd_script(a):
    _script_summary(_work(a.work))


def cmd_ingest(a):
    from .ingest import ingest
    print(json.dumps(ingest(a.source, _work(a.work)), ensure_ascii=False, indent=1))


def cmd_analyze(a):
    from .pipeline import episode
    w = _work(a.work)
    if a.source:
        from .ingest import ingest
        ingest(a.source, w, title=a.title, long=a.long)
    cuts = [float(x) for x in a.cuts.replace("，", ",").split(",") if x.strip()] if a.cuts else None
    out = episode(w, use_vlm=not a.no_vlm, workers=a.workers, cuts=cuts, full=a.full)
    _cast_summary(out["story"], w)
    if out["story"].get("breakdown"):
        _review_summary(out["story"], w)
    if out["report"]:
        _report_summary(out["report"], w)
    if out["story"].get("breakdown"):
        _script_summary(w)
        print(f"  → {layout.site(w)}（给人看只开这一个：学习分析 / 剧本 / 分镜拆解）")


def cmd_deepen(a):
    from .pipeline import deepen
    w = _work(a.work)
    _review_summary(deepen(w, workers=a.workers), w)


def cmd_lock(a):
    from .pipeline import lock
    lk = lock(_work(a.work), force=a.force)
    print(f"已锁定 {lk['hash']}（{lk['at']}）{'，带着未修的问题' if lk['forced'] else ''}")


def cmd_redo(a):
    from . import pipeline
    w = _work(a.work)
    if a.stage == "proofread":
        print(f"改了 {pipeline.proofread(w)} 条字幕（原 OCR 文本留在 ocr 字段）")
    elif a.stage == "subtitles":
        r = pipeline.subtitles(w)
        print(f"字幕 {r['lines']} 条")
        if r["dropped"]:
            print(f"  去掉 {len(r['dropped'])} 条不是字幕的字：" + "、".join(r["dropped"][:20]))
        for x in r["added"]:
            print(f"  新读到：镜{x['shot']} {x['start']:.1f}s {x['text']}")
        if r["added"]:
            print(f"  新读到的台词还没有说话人：在 story.json 里补，或 rea-toon redo refine {a.work}")
        print(f"旧字幕轨存在 data/subtitles.prev.json；报告里的台词数据用 rea-toon redo report {a.work} --no-vlm 刷新")
    elif a.stage == "refine":
        r = pipeline.refine(w)
        print(f"{r['lines']} 句台词标了说话人")
        for m in r["merges"]:
            print(f"  合并角色：{m['drop']} → {m['keep']}（{m.get('reason', '')}）")
        print(f"台词和角色变了：重跑 rea-toon redo breakdown {a.work}，审完再 lock")
    elif a.stage == "breakdown":
        _review_summary(pipeline.breakdown(w, workers=a.workers), w)
    elif a.stage == "pages":
        for p in pipeline.pages(w):
            print(f"  → {p}")
    elif a.stage == "report":
        _report_summary(pipeline.report(w, use_vlm=not a.no_vlm), w)


def cmd_series(a):
    from . import series
    root = _work(a.work)
    if a.source:
        ser = series.add(root, a.source, title=a.title, first=a.first)
        print(f"共 {len(ser['episodes'])} 集")
    elif a.title:
        ser = series.load(root); ser["title"] = a.title; series.save(root, ser)
    if not series.load(root)["episodes"]:
        raise SystemExit("还没有集：rea-toon series WORK --source 第1集.mp4 第2集.mp4 …（或一个放着各集视频的文件夹）")
    if not a.only_series:
        for f in series.analyze_all(root, workers=a.workers, use_vlm=not a.no_vlm, full=a.full):
            print(f"  ✗ 第 {f['ep']} 集：{f['error']}")
    ser = series.build(root, use_vlm=not a.no_vlm)
    rows = [x for x in ser["rows"] if not x.get("missing")]
    print(f"{len(rows)}/{len(ser['rows'])} 集拆完，{len(ser['characters'])} 个角色（跨集合并后）")
    if (ser.get("read") or {}).get("logline"):
        print(f"  {ser['read']['logline']}")
    for x in ser["rows"]:
        if x.get("context_missing"):
            print(f"  ! 第 {x['ep']} 集拆的时候没带上第 {'、'.join(map(str, x['context_missing']))} 集的前情"
                  + ("（已锁定，没自动重拆）" if x.get("locked") else "（重跑 rea-toon series 会带着前情重拆）"))
    print(f"  → {layout.site(root)}（整部剧的入口：选哪一集、看拆解 / 剧本 / 分镜；每集的拆片包是 epNN/VIDEO.md）")
    if rows:
        _script_summary(root)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rea-toon", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", help=".env with API keys (default: ./.env, then the repo's .env)")
    ap.add_argument("--backend", choices=("agent", "api"),
                    help="谁来看图作答：agent（默认，当前 agent 会话自己看）| api（配置的看图模型；配了 REA_TOON_VLM_* 时默认用它）")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="{analyze,series,script,deepen,lock,redo,ingest}")

    p = sub.add_parser("analyze", help="拆一集：切镜、字幕、角色表、逐镜拆解 → 说话人和角色合并 → 深度拆解 → 爆款拆解 → index.html（给人看）+ VIDEO.md（给模型读）")
    p.add_argument("work"); p.add_argument("--source", help="先导入这个视频（本地视频文件）")
    p.add_argument("--title", help="这条视频叫什么（页面和拆片包里用；不给就用文件名）")
    p.add_argument("--no-vlm", action="store_true"); p.add_argument("--workers", type=int, default=6)
    p.add_argument("--cuts", help="强制在这些时间点（秒）切镜头，如 6.65,9.92：溶解/穿门/遮挡转场切不出来、或一镜超过 15s 时用")
    p.add_argument("--full", action="store_true", help="逐镜单独调用 + 逐秒表演拆解（贵约 5 倍）；默认快拆，之后也可以 deepen 补")
    p.add_argument("--long", action="store_true", help="片源超过 20 分钟也当一集拆（默认拒绝：那多半是合集，要先切成一集一个文件）")
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("series", help="整部剧：逐集拆解（可续跑）→ 跨集合并角色 → 主线、伏笔回收、集间钩子、付费卡点 → index.html")
    p.add_argument("work", help="整部剧的目录，每集放在 ep01/ ep02/ …")
    p.add_argument("--source", nargs="+", help="追加这些集：本地视频文件，或一个放着各集视频的文件夹（按文件名里的数字排序）")
    p.add_argument("--title", help="剧名")
    p.add_argument("--first", type=int, help="只导入前 N 集（合集、文件夹里集数很多时先拆几集看看）")
    p.add_argument("--only-series", action="store_true", help="不补拆单集，只重做跨集部分")
    p.add_argument("--full", action="store_true", help="每集都做完整拆解（逐镜单独调用 + 逐秒表演），默认快拆")
    p.add_argument("--no-vlm", action="store_true"); p.add_argument("--workers", type=int, default=6)
    p.set_defaults(fn=cmd_series)

    p = sub.add_parser("script", help="完整剧本 + 角色图：人物表（头像、造型图）和逐集逐场的剧本（动作、台词、说话人）→ pages/script.html"
                                      "（analyze / series 会自动做；单集目录或整部剧目录都行，不调模型）")
    p.add_argument("work"); p.set_defaults(fn=cmd_script)

    p = sub.add_parser("deepen", help="补拆：给快拆的目录补逐秒表演和身体接触（只补这两项，不重拆别的）")
    p.add_argument("work"); p.add_argument("--workers", type=int, default=6); p.set_defaults(fn=cmd_deepen)

    p = sub.add_parser("lock", help="审完拆解后锁定（审定版）；之后改了 story.json 要重新 lock")
    p.add_argument("work"); p.add_argument("--force", action="store_true", help="带着未修的 error 也锁定")
    p.set_defaults(fn=cmd_lock)

    p = sub.add_parser("redo", help="只重做某一步：" + "、".join(REDO), formatter_class=argparse.RawDescriptionHelpFormatter,
                       description="\n".join(f"  {k:<10} {v}" for k, v in REDO.items()))
    p.add_argument("stage", choices=list(REDO)); p.add_argument("work")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--no-vlm", action="store_true", help="report 用：只出代码量出来的数据，不调模型")
    p.set_defaults(fn=cmd_redo)

    p = sub.add_parser("ingest", help="只导入参考视频（本地视频文件）")
    p.add_argument("source"); p.add_argument("work"); p.set_defaults(fn=cmd_ingest)

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "httpx", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_env(a.env_file)
    if a.backend:
        os.environ["REA_TOON_BACKEND"] = a.backend
    from .vlm import NeedAgent, pending
    try:
        a.fn(a)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:  # a message for the user, not a traceback
        if a.verbose:
            raise
        print(f"出错：{exc}（加 -v 看完整报错）", file=sys.stderr)
        sys.exit(1)
    except NeedAgent as stop:
        todo = pending(stop.folder)
        print(f"\n停在这里：{len(todo)} 道题等 agent 作答（{stop.folder}）")
        for q in todo:
            print(f"  {q['key']}.json  {len(q['images'])} 张图  {q['prompt'][:36].strip()}…")
        print("每道题：读 <key>.json 里的 prompt，用看图工具看 images 里的每一张图，把提示词要求的 JSON 写到 <key>.answer.json；\n"
              "全部答完后原样重跑同一条命令，会接着往下走（答过的不会再问）。要改用接口模型：--backend api。")
        sys.exit(3)


if __name__ == "__main__":
    main()
