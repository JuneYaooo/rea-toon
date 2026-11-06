"""rea-toon：逆向拆解短剧（真人 / AI / 漫剧 / 动态漫 / 解说，单集或整部剧）。

拆成可复查的剧本源码（story.json + breakdown.html），再出给编剧和制片看的爆款拆解报告（report.html）；
整部剧用 series 跨集合并角色、看主线、伏笔和集与集的钩子（series.html）。复刻在 rea-toon-remake。"""

from __future__ import annotations

import argparse
import json
import logging
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
    if not a.no_vlm:
        from .breakdown import breakdown
        _review_summary(breakdown(w, workers=a.workers), w)
        _report(w)


def _report(w: Path, use_vlm: bool = True) -> None:
    from .report import report
    rep = report(w, use_vlm=use_vlm)
    m, r = rep["metrics"], rep.get("read") or {}
    print(f"爆款拆解（{rep['format']['cn']}）：{r.get('logline') or ''}")
    print(f"  {m['duration']:.0f}s · {m['shots']} 镜 · 每分钟 {m['cuts_per_min']} 刀 · 第一个爽点 {m['first_peak']}s · "
          f"爽点/反转 {m['peaks']} 个 · {'有' if m['cliffhanger'] else '没有'}结尾钩子")
    for g in m["dead_zones"]:
        print(f"  ! {g['from']}–{g['to']}s 连续 {g['len']}s 没有爽点或反转")
    print(f"  → {w / 'report.html'}")


def cmd_report(a):
    _report(_work(a.work), use_vlm=not a.no_vlm)


def cmd_series(a):
    from . import series
    root = _work(a.work)
    if a.source:
        ser = series.add(root, a.source, title=a.title)
        print(f"共 {len(ser['episodes'])} 集")
    elif a.title:
        ser = series.load(root); ser["title"] = a.title; series.save(root, ser)
    if not series.load(root)["episodes"]:
        raise SystemExit("还没有集：rea-toon series WORK --source 第1集.mp4 第2集.mp4 …（或一个放着各集视频的文件夹）")
    if not a.only_series:
        failed = series.analyze_all(root, workers=a.workers, use_vlm=not a.no_vlm)
        for f in failed:
            print(f"  ✗ 第 {f['ep']} 集：{f['error']}")
    ser = series.build(root, use_vlm=not a.no_vlm)
    rows = [x for x in ser["rows"] if not x.get("missing")]
    print(f"{len(rows)}/{len(ser['rows'])} 集拆完，{len(ser['characters'])} 个角色（跨集合并后）")
    if (ser.get("read") or {}).get("logline"):
        print(f"  {ser['read']['logline']}")
    print(f"  → {root / 'series.html'}")


def cmd_proofread(a):
    from .analyze import proofread_story
    print(f"改了 {proofread_story(Path(a.work))} 条字幕（原 OCR 文本留在 ocr 字段）")


def cmd_breakdown(a):
    from .breakdown import breakdown
    w = _work(a.work)
    story = breakdown(w, workers=a.workers)
    _review_summary(story, w)


def _review_summary(story: dict, w: Path) -> None:
    bd = story.get("breakdown") or {}
    must = [s["index"] for s in story["shots"] if (s.get("key") or {}).get("priority") == "must"]
    print(f"{len(bd.get('scenes') or [])} 场，{len(bd.get('beats') or [])} 个戏剧节拍，必保镜头 {must}")
    errors = [x for x in story.get("lint") or [] if x["level"] == "error"]
    warns = [x for x in story.get("lint") or [] if x["level"] != "error"]
    for x in errors + warns:
        print(f"  {'✗' if x['level'] == 'error' else '!'} {'镜' + str(x['shot']) + '：' if x['shot'] else ''}{x['msg']}")
    print(f"审核：{w / 'breakdown.html'}（同内容 {w / 'breakdown.md'}）；改好后 rea-toon lock {w}，再 rea-toon report {w} 刷新报告")


def cmd_lock(a):
    from .breakdown import lock
    lk = lock(_work(a.work), force=a.force)
    print(f"已锁定 {lk['hash']}（{lk['at']}）{'，带着未修的问题' if lk['forced'] else ''}：可以交给 rea-toon-remake 复刻了")


def cmd_refine(a):
    from .analyze import refine
    r = refine(Path(a.work))
    print(f"{r['lines']} 句台词标了说话人")
    for m in r["merges"]:
        print(f"  合并角色：{m['drop']} → {m['keep']}（{m.get('reason', '')}）")
    if r["merges"]:
        print("  remake.yaml 里被合并掉的角色 id 要删掉（它现在是同一个角色的另一套衣服）")
    print(f"台词和角色变了：重跑 rea-toon breakdown {a.work}，审完再 lock")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rea-toon", description=__doc__)
    ap.add_argument("--env-file", help=".env with API keys (default: ./.env, then capsule-cinema's)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="下载/导入参考视频（本地文件、直链、抖音分享文本）")
    p.add_argument("source"); p.add_argument("work"); p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("analyze", help="拆一集：切镜、字幕、角色表、逐镜拆解 → 深度拆解 breakdown.html → 爆款拆解 report.html")
    p.add_argument("work"); p.add_argument("--source", help="先导入这个视频")
    p.add_argument("--no-vlm", action="store_true"); p.add_argument("--workers", type=int, default=6)
    p.add_argument("--cuts", help="强制在这些时间点（秒）切镜头，如 6.65,9.92：溶解/穿门/遮挡转场切不出来、或一镜超过 15s 时用")
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("proofread", help="校对 OCR 字幕的形近错字（analyze 会自动做；给旧的工作目录补做）")
    p.add_argument("work"); p.set_defaults(fn=cmd_proofread)

    p = sub.add_parser("refine", help="逐句标说话人、合并被拆成两个的同一角色（换装/乔装）；analyze 会自动做，给旧的工作目录补做")
    p.add_argument("work"); p.set_defaults(fn=cmd_refine)

    p = sub.add_parser("breakdown", help="深度拆解：画风档案、分场造型、戏剧节拍和看点、风险、一致性检查 → breakdown.html 待审"
                                         "（analyze 会自动做；改过 story.json 或给旧目录补做时单独跑）")
    p.add_argument("work"); p.add_argument("--workers", type=int, default=6); p.set_defaults(fn=cmd_breakdown)

    p = sub.add_parser("report", help="爆款拆解报告：节奏曲线、爽点分布、人物戏份、套路和可复用公式 → report.html（analyze 会自动做）")
    p.add_argument("work"); p.add_argument("--no-vlm", action="store_true", help="只出代码量出来的数据，不调模型")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("series", help="整部剧：逐集拆解（可续跑）→ 跨集合并角色 → 主线、伏笔回收、集间钩子、付费卡点 → series.html")
    p.add_argument("work", help="整部剧的目录，每集放在 ep01/ ep02/ …")
    p.add_argument("--source", nargs="+", help="追加这些集：视频文件、链接，或一个放着各集视频的文件夹（按文件名里的数字排序）")
    p.add_argument("--title", help="剧名")
    p.add_argument("--only-series", action="store_true", help="不补拆单集，只重做跨集部分")
    p.add_argument("--no-vlm", action="store_true"); p.add_argument("--workers", type=int, default=6)
    p.set_defaults(fn=cmd_series)

    p = sub.add_parser("lock", help="审完拆解后锁定（审定版）：复刻只在锁定的拆解上运行；之后改了 story.json 要重新 lock")
    p.add_argument("work"); p.add_argument("--force", action="store_true", help="带着未修的 error 也锁定")
    p.set_defaults(fn=cmd_lock)

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "httpx", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_env(a.env_file)
    a.fn(a)


if __name__ == "__main__":
    main()
