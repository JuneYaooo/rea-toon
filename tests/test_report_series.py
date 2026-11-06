import json
from pathlib import Path

from PIL import Image


def _shot(i, start, dur, role="normal", priority="normal", chars=(), subs=(), **extra):
    return {"id": f"shot_{i:03d}", "index": i, "start": start, "end": start + dur, "duration": dur,
            "frames": {"mid": f"frames/shot_{i:03d}_mid.jpg"},
            "analysis": {"characters": [{"id": c, "visibility": "face"} for c in chars], "shot_size": "近景",
                         "camera_move": "缓慢推近，然后固定"},
            "key": {"role": role, "priority": priority}, "subtitles": list(subs), **extra}


def _line(t0, t1, text, speaker, mode="on"):
    return {"start": t0, "end": t1, "text": text, "speaker": speaker, "mode": mode, "on_screen": mode == "on"}


def _episode(kind="真人感AI短剧", hero="hero", boss="boss"):
    shots = [
        _shot(1, 0, 3, "hook", "must", [hero], [_line(0.2, 2.5, "你被开除了", boss, "off")]),
        _shot(2, 3, 5, "setup", chars=[hero, boss], subs=[_line(3.2, 5, "凭什么", hero), _line(5.5, 7.5, "就凭我是老板", boss)]),
        _shot(3, 8, 4, "conflict", "high", [hero, boss]),
        _shot(4, 12, 40, "normal", chars=[boss]),
        _shot(5, 52, 3, "reversal", "must", [hero, boss], [_line(52.2, 54, "我才是董事长", hero)]),
        _shot(6, 55, 2, "cliffhanger", "must", [hero]),
    ]
    return {"source": {"title": "测试剧"}, "summary": "小职员被开除后亮出董事长身份", "duration": 57.0, "style": kind,
            "characters": [{"id": hero, "role": "男主（林舟）", "importance": "lead", "shots": [1, 2, 3, 5, 6], "appearance": "灰西装"},
                           {"id": boss, "role": "老板", "importance": "supporting", "shots": [2, 3, 4, 5], "appearance": "秃顶"},
                           {"id": "crowd", "role": "群众", "importance": "extra", "shots": [4]}],
            "shots": shots,
            "breakdown": {"style_bible": {"kind": kind}, "scenes": [{"id": "S1", "shots": [1, 6]}],
                          "beats": [{"type": "hook", "shots": [1], "what": "当众开除"},
                                    {"type": "conflict", "shots": [2, 3], "what": "顶嘴"},
                                    {"type": "reversal", "shots": [5], "what": "亮出身份"},
                                    {"type": "cliffhanger", "shots": [6], "what": "董事会来电"}]}}


def test_format_of_reads_narration_motion_and_style():
    from rea_toon.genre import format_of
    story = _episode()
    assert format_of(story)["id"] == "ai_live"
    assert format_of({**story, "breakdown": {"style_bible": {"kind": "2D动漫"}}})["id"] == "anime_2d"
    assert format_of({**story, "breakdown": {"style_bible": {"kind": "动态漫画"}}})["id"] == "motion_comic"
    narrated = json.loads(json.dumps(story))
    for s in narrated["shots"]:
        for x in s["subtitles"]:
            x["mode"] = "narration"
    assert format_of(narrated)["id"] == "commentary"
    stills = json.loads(json.dumps(story))
    for s in stills["shots"]:
        s["motion"] = {"kind": "pan"}
    assert format_of(stills)["id"] == "motion_comic"
    assert format_of({**story, "format": "live_action"})["why"].startswith("story.json")


def test_report_metrics():
    from rea_toon.report import curve, metrics, span
    m = metrics(_episode())
    assert m["shots"] == 6 and m["cuts_per_min"] == round(60 * 6 / 57, 1)
    assert m["first_conflict"] == 3 and m["first_peak"] == 52 and m["peaks"] == 1 and m["cliffhanger"]
    # the 52 s before the first peak is setup, not a lull; nothing after it is long enough to flag
    assert m["peak_gaps"][0] == {"from": 0.0, "to": 52, "len": 52} and m["dead_zones"] == []
    assert m["screen_time"] == {"boss": 52.0, "hero": 17.0} and list(m["screen_time"]) == ["boss", "hero"]
    assert m["talk_chars"]["boss"] == len("你被开除了") + len("就凭我是老板")
    assert m["pairs"][0]["a"] == "boss" and m["pairs"][0]["b"] == "hero" and m["pairs"][0]["exchanges"] >= 2
    assert m["camera_moves"] == {"缓慢推近": 6} and m["talk_modes"]["画外"] == 1
    cv = curve(_episode())
    assert cv[0]["t0"] == 0 and abs(cv[-1]["t1"] - 57) < 1e-6
    peak = max(cv, key=lambda c: c["intensity"])
    assert 5 in peak["shots"] or 6 in peak["shots"]
    assert span([1, 2, 3, 5, 7, 8]) == "1–3, 5, 7–8"


def test_report_with_model_writes_pages(tmp_path: Path):
    from rea_toon import report
    (tmp_path / "story.json").write_text(json.dumps(_episode(), ensure_ascii=False), encoding="utf-8")
    seen = {}

    class FakeVLM:
        def ask_json(self, prompt, images, **kw):
            seen["prompt"] = prompt
            return {"logline": "被开除的小职员是董事长", "tags": {"themes": ["身份反转"], "audience": "男频", "pleasure": ["打脸"]},
                    "hook": "镜1 画外一句开除", "structure": [{"part": "钩子", "shots": [1, 1], "what": "开除"}],
                    "formula": [{"step": "当众羞辱", "share": 0.2, "what": "主角被压到最低"}], "why_it_works": ["镜5 反转"]}
    rep = report.report(tmp_path, vlm=FakeVLM())
    assert "AI 真人感短剧" in seen["prompt"] and "第一个爽点/反转在 52" in seen["prompt"] and "5|52.0|3.0|反转|必保" in seen["prompt"]
    assert rep["read"]["logline"].startswith("被开除")
    page = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "被开除的小职员是董事长" in page and "<svg" in page and "当众羞辱" in page and "未审版本" in page
    assert "可复用公式" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "lock" not in json.loads((tmp_path / "story.json").read_text(encoding="utf-8"))  # story untouched


def _make_series(root: Path, eps):
    from rea_toon import series
    ser = {"schema": "rea_toon.series.v1", "title": "测试剧", "episodes": []}
    for k, story in enumerate(eps, 1):
        d = root / f"ep{k:02d}"
        (d / "frames").mkdir(parents=True)
        for s in story["shots"]:
            Image.new("RGB", (64, 36), (k * 40, 80, 120)).save(d / s["frames"]["mid"])
        (d / "story.json").write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
        ser["episodes"].append({"ep": k, "dir": d.name, "source": f"{k}.mp4"})
    series.save(root, ser)


def test_series_expand_and_clean_groups(tmp_path: Path):
    from rea_toon.series import _clean_groups, expand
    for n in ("第10集.mp4", "第2集.mp4", "第1集.mp4", "封面.jpg"):
        (tmp_path / n).write_bytes(b"x")
    assert [Path(p).name for p in expand([str(tmp_path)])] == ["第1集.mp4", "第2集.mp4", "第10集.mp4"]
    entries = [{"label": "E1:hero", "id": "hero", "role": "男主"}, {"label": "E2:lin", "id": "lin", "role": "男主"},
               {"label": "E2:boss", "id": "boss", "role": "老板"}]
    g = _clean_groups([{"id": "Lin Zhou", "members": ["E1:hero", "E2:lin", "E9:ghost"]}, {"id": "lin_zhou", "members": ["E2:lin"]}], entries)
    assert g[0] == {"id": "lin_zhou", "name": "", "role": "男主", "members": ["E1:hero", "E2:lin"]}
    assert g[1]["members"] == ["E2:boss"] and len(g) == 2  # the duplicate label is dropped, the forgotten one stands alone


def test_series_build_offline_and_with_model(tmp_path: Path, monkeypatch):
    from rea_toon import series
    _make_series(tmp_path, [_episode(), _episode(hero="linzhou")])
    ser = series.build(tmp_path, use_vlm=False)  # without a model the same id is the same person
    ids = {c["id"]: c for c in ser["characters"]}
    assert ids["boss"]["episodes"] == [1, 2] and ids["boss"]["seconds"] == 104.0 and "linzhou" in ids
    assert (tmp_path / "series.html").is_file() and "第 1–2 集" in (tmp_path / "series.md").read_text(encoding="utf-8")

    calls = []

    class FakeVLM:
        def __init__(self, **kw):
            pass

        def ask_json(self, prompt, images, **kw):
            calls.append((prompt, images))
            if "头像表" in prompt:
                assert "E2:linzhou|男主（林舟）" in prompt and images and all(Path(p).is_file() for p in images)
                return {"characters": [{"id": "linzhou", "name": "林舟", "members": ["E1:hero", "E2:linzhou"]},
                                       {"id": "boss", "members": ["E1:boss", "E2:boss"]}]}
            return {"logline": "董事长卧底", "threads": [{"what": "董事会来电", "planted": 1, "paid": None}],
                    "links": [{"ep": 1, "end_hook": "董事会来电", "picked_up": "没接"}], "paywall": [{"ep": 2, "why": "身份将曝光"}]}
    monkeypatch.setattr(series, "VLM", FakeVLM)
    ser = series.build(tmp_path)
    hero = next(c for c in ser["characters"] if c["id"] == "linzhou")
    assert hero["members"] == ["E1:hero", "E2:linzhou"] and hero["episodes"] == [1, 2] and hero["seconds"] == 34.0
    assert "2|57s" in calls[-1][0] and ser["read"]["logline"] == "董事长卧底"
    page = (tmp_path / "series.html").read_text(encoding="utf-8")
    assert "还没收" in page and "付费卡点" in page and "ep01/report.html" in page


def test_series_keeps_hand_fixed_cast(tmp_path: Path):
    from rea_toon import series
    _make_series(tmp_path, [_episode(), _episode(hero="linzhou")])
    ser = series.build(tmp_path, use_vlm=False)
    ser["characters"] = [{"id": "lin", "name": "林舟", "role": "男主", "members": ["E1:hero", "E2:linzhou"]}]
    ser["cast_fixed"] = True
    series.save(tmp_path, ser)
    ser = series.build(tmp_path, use_vlm=False)
    lin = next(c for c in ser["characters"] if c["id"] == "lin")
    assert lin["episodes"] == [1, 2] and lin["seconds"] == 34.0
    assert any(c["members"] == ["E1:boss"] for c in ser["characters"])  # not in the hand-made list: stands alone
