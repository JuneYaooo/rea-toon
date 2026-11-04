import json
from pathlib import Path

from rea_toon import cache
from rea_toon.engines.video import H3AutoDL, SeedanceArk
from rea_toon.spec import RemakeSpec, parse_shots


def test_parse_shots():
    assert parse_shots("all", 5) == [1, 2, 3, 4, 5]
    assert parse_shots("1,3-4，9", 5) == [1, 3, 4]


def test_clamp():
    h3 = H3AutoDL.__new__(H3AutoDL)
    sd = SeedanceArk.__new__(SeedanceArk)
    assert h3.clamp(0.33) == 1 and h3.clamp(2.03) == 2 and h3.clamp(2.2) == 3 and h3.clamp(2.0) == 2 and h3.clamp(30) == 15
    assert sd.clamp(1.2) == 4


def test_cache_roundtrip(tmp_path: Path):
    src = tmp_path / "a.txt"; src.write_text("x")
    out = tmp_path / "o.bin"; out.write_text("y")
    k = cache.input_key(prompt="p", src=src)
    assert not cache.fresh(out, k)
    cache.stamp(out, k)
    assert cache.fresh(out, k)
    src.write_text("changed")
    assert not cache.fresh(out, cache.input_key(prompt="p", src=src))


def test_spec_load(tmp_path: Path):
    p = tmp_path / "remake.yaml"
    p.write_text("name: t\ncast:\n  groom: {name: 孙悟空, look: 猴脸}\n  bride: 白晶晶\n", encoding="utf-8")
    s = RemakeSpec.load(p)
    assert s.cast["groom"].name == "孙悟空" and s.cast["bride"].name == "白晶晶" and s.cast["bride"].costume == "keep"


def _story(tmp_path: Path) -> Path:
    work = tmp_path / "w"; work.mkdir()
    story = {"aspect": "16:9", "width": 1280, "height": 720, "fps": 30, "style": "写实", "audio": {},
             "characters": [{"id": "groom", "role": "新郎", "appearance": "黑西装", "shots": [1]},
                            {"id": "chef", "role": "厨师", "appearance": "白衣", "shots": [2]}],
             "shots": [{"id": "shot_001", "index": 1, "start": 0, "end": 3, "duration": 3, "subtitles": [],
                        "analysis": {"characters": [{"id": "groom"}], "in_first": ["groom"], "in_last": []}},
                       {"id": "shot_002", "index": 2, "start": 3, "end": 5, "duration": 2, "subtitles": [],
                        "analysis": {"characters": [{"id": "chef"}]}}]}
    (work / "story.json").write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
    return work


def test_plan_routes(tmp_path: Path):
    from rea_toon.remake import Remake
    work = _story(tmp_path)
    spec = RemakeSpec(name="t")
    from rea_toon.spec import CastSwap
    spec.cast = {"groom": CastSwap(id="groom", name="孙悟空")}
    plan = Remake(work, spec).plan()
    r1, r2 = plan["shots"]
    assert r1["route"] == "regen" and r1["frames"] == {"first": "edit", "last": "clean"} and r1["mode"] == "first_last"
    assert r2["route"] == "reuse"
    spec.style = "3D 动画"
    plan = Remake(work, spec).plan()
    assert all(p["route"] == "regen" for p in plan["shots"])


def _remake_with(tmp_path: Path, analysis: dict, subtitles=None, performance=None):
    from rea_toon.remake import Remake
    from rea_toon.spec import CastSwap
    work = _story(tmp_path)
    story = json.loads((work / "story.json").read_text(encoding="utf-8"))
    shot = story["shots"][0]
    shot["analysis"].update(analysis)
    shot["subtitles"] = subtitles or []
    if performance:
        shot["performance"] = performance
    (work / "story.json").write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
    spec = RemakeSpec(name="t")
    spec.cast = {"groom": CastSwap(id="groom", name="孙悟空")}
    r = Remake(work, spec)
    return r, r.story["shots"][0]


SUB = [{"start": 0.5, "end": 2.0, "text": "来者皆是客", "y_center": 0.9, "height": 0.05, "box": [0.4, 0.88, 0.6, 0.93]}]
PERF = {"overall": "惊慌", "camera": "快速拉远", "motion": "新郎惊慌地说话",
        "beats": [{"t0": 0, "t1": 3, "who": "groom", "face": "瞪眼皱眉", "mouth": "大幅说话", "body": "后退半步"}],
        "avoid": ["不要笑"]}


def test_dialogue_shot_uses_lipsync(tmp_path: Path):
    r, shot = _remake_with(tmp_path, {"speaker": "groom", "lip_visible": True, "change": "small"}, SUB, PERF)
    p = r.shot_plan(shot)
    assert p["mode"] == "lipsync"
    prompt = r._video_prompt(shot, p)
    # the audio carries the words; quoting them makes the model burn them in as subtitles
    assert "口型严格跟随音频" in prompt and "0.5–2.0s 说话" in prompt and "来者皆是客" not in prompt
    assert "瞪眼皱眉" in prompt and "不要笑" in prompt and "新郎惊慌地说话" in prompt


def test_null_speaker_and_wide_shot(tmp_path: Path):
    # lyrics over an aerial shot: no "X 在说话", no face details, camera leads
    r, shot = _remake_with(tmp_path, {"speaker": "null", "lip_visible": False, "change": "large",
                                      "shot_size": "航拍"}, SUB, PERF)
    p = r.shot_plan(shot)
    assert p["mode"] != "lipsync"
    prompt = r._video_prompt(shot, p)
    assert "null" not in prompt and "在说话" not in prompt
    assert "瞪眼皱眉" not in prompt and "后退半步" in prompt and "不要推近" in prompt


def test_lipsync_segment_not_retimed(tmp_path: Path, monkeypatch):
    from rea_toon import assemble
    calls = []
    monkeypatch.setattr(assemble, "run", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(assemble, "probe", lambda p: {"duration": 4.0})
    story = {"width": 1280, "height": 720, "fps": 24}
    shot = {"start": 0, "end": 3.3, "duration": 3.3, "subtitles": []}
    assemble.segment(tmp_path, story, shot, tmp_path / "c.mp4", tmp_path / "o.mp4", False, mode="lipsync")
    assemble.segment(tmp_path, story, shot, tmp_path / "c.mp4", tmp_path / "o2.mp4", False, mode="first_last")
    vf = [c[c.index("-vf") + 1] for c in calls]
    assert "setpts" not in vf[0] and "setpts" in vf[1]


def test_layout_similarity(tmp_path: Path):
    import numpy as np
    import cv2
    from rea_toon.media import layout_similarity
    rng = np.random.default_rng(0)
    base = (rng.random((180, 320)) * 255).astype(np.uint8)
    base = cv2.GaussianBlur(base, (31, 31), 0)
    a, b, c = tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"
    cv2.imwrite(str(a), base)
    noisy = np.clip(base.astype(int) + rng.integers(-10, 10, base.shape), 0, 255).astype(np.uint8)
    cv2.imwrite(str(b), noisy)
    cv2.imwrite(str(c), np.ascontiguousarray(base[::-1, ::-1]))
    assert layout_similarity(a, b) > 0.9 and layout_similarity(a, c) < 0.5


def test_h3local_frames_and_graph(tmp_path: Path):
    from rea_toon.engines.video import H3Local
    assert H3Local.frames_for(0.3) == 56 and H3Local.frames_for(5.1) == 124 and H3Local.frames_for(5.4) == 141
    assert H3Local.frames_for(30) == 362 and all((H3Local.frames_for(x) - 5) % 17 == 0 for x in (2.7, 3.3, 6.1))
    eng = H3Local.__new__(H3Local)
    eng.steps, eng.seed = 10, 1
    f, sheet, vid = (tmp_path / n for n in ("f.png", "s.png", "v.mp4"))
    g = eng.build("p", 2.7, "16:9", f, f, [sheet], None, "ref_video", vid, upload=lambda p: p.name)
    cond = g["5"]["inputs"]
    assert g["5"]["class_type"] == "MiniMaxH3ReferenceToVideo" and g["1"]["inputs"]["unet_name"] == H3Local.REF2VA
    assert (cond["width"], cond["height"], cond["length"]) == (1024, 576, 73)
    assert len([k for k in cond if k.startswith("ref_images.")]) == 3 and "ref_videos.ref_video_0" in cond
    g = eng.build("p", 1.0, "9:16", f, f, [], None, "first_last", None, upload=lambda p: p.name)
    assert g["5"]["class_type"] == "MiniMaxH3ImageToVideo" and {"first_frame", "last_frame"} <= set(g["5"]["inputs"])


def test_audio_sync_finds_lag(tmp_path):
    import shutil
    import numpy as np
    import pytest
    import wave
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg missing")
    from rea_toon.media import audio_sync
    rng = np.random.default_rng(0)
    sr, n = 8000, 8000 * 4
    env = np.repeat(rng.random(40) > 0.5, n // 40).astype(float)          # syllable-like on/off pattern
    voice = env * np.sin(np.arange(n) * 2 * np.pi * 220 / sr)
    late = np.concatenate([np.zeros(int(0.3 * sr)), voice])[:n]           # same speech 0.3s late

    def wav(p, a):
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes((a * 20000).astype(np.int16).tobytes())
        return p
    r = audio_sync(wav(tmp_path / "clip.wav", late), wav(tmp_path / "voice.wav", voice), 0.0, 4.0)
    assert r["corr"] > 0.9 and abs(r["lag"] - 0.3) <= 0.04


def test_plan_lists_kept_roles_and_pilot(tmp_path: Path):
    from rea_toon.remake import Remake
    from rea_toon.spec import CastSwap
    work = _story(tmp_path)
    spec = RemakeSpec(name="t")
    spec.cast = {"groom": CastSwap(id="groom", name="孙悟空")}
    plan = Remake(work, spec).plan()
    # the chef stays as in the original: plan must say so before anything is spent
    assert [k["id"] for k in plan["kept_roles"]] == ["chef"] and plan["kept_roles"][0]["shots"] == [2]
    assert plan["pilot"] == [1]


def test_plan_covers_whole_film_when_scoped(tmp_path: Path):
    from rea_toon.remake import Remake
    from rea_toon.spec import CastSwap
    work = _story(tmp_path)
    spec = RemakeSpec(name="t", shots="2")
    spec.cast = {"groom": CastSwap(id="groom", name="孙悟空")}
    plan = Remake(work, spec).plan()
    # shot 1 is out of scope: still in the plan (as original footage), so assembly and checks see the whole film
    assert [p["index"] for p in plan["shots"]] == [1, 2] and plan["shots"][0]["route"] == "reuse"


def test_shot_source_reads_disk_not_run_state(tmp_path: Path):
    from rea_toon.assemble import shot_source
    shot = {"id": "shot_001", "index": 1}
    regen = {"index": 1, "route": "regen", "mode": "ref_video"}
    assert shot_source(shot, {"index": 1, "route": "reuse"}, tmp_path)["status"] == "reuse"
    assert shot_source(shot, regen, tmp_path)["status"] == "missing"
    clips = tmp_path / "clips"; clips.mkdir()
    (clips / "shot_001.mp4").write_bytes(b"x")
    meta = clips / "shot_001.mp4.meta.json"
    meta.write_text(json.dumps({"mode": "lipsync", "sync": {"corr": 0.9, "lag": 0.3}}), encoding="utf-8")
    src = shot_source(shot, regen, tmp_path)
    assert src["status"] == "regen" and src["mode"] == "lipsync" and src["av_lag"] == 0.3
    meta.write_text(json.dumps({"mode": "first_last", "sync": {"corr": 0.2, "lag": 0.7}}), encoding="utf-8")
    assert shot_source(shot, regen, tmp_path)["av_lag"] is None  # lag measured on a poor match is noise


def test_anchors_add_mid_shot_keyframes(tmp_path: Path):
    from rea_toon.remake import Remake
    from rea_toon.spec import CastSwap
    work = _story(tmp_path)
    story = json.loads((work / "story.json").read_text(encoding="utf-8"))
    story["shots"][0]["performance"] = PERF
    (work / "story.json").write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
    p = tmp_path / "remake.yaml"
    p.write_text("name: t\nvideo_engine: h3local\nanchors: {1: [2.5, 1.0, 9]}\n", encoding="utf-8")
    spec = RemakeSpec.load(p)
    assert spec.anchors == {1: [1.0, 2.5, 9.0]}
    spec.cast = {"groom": CastSwap(id="groom", name="孙悟空")}
    r = Remake(work, spec)
    plan = r.shot_plan(r.story["shots"][0])
    # 9s is outside the 3s shot and is dropped
    assert plan["mode"] == "ref_video" and plan["anchors"] == {"a1": 1.0, "a2": 2.5}
    assert plan["frames"]["a1"] == "edit" and plan["frame_swaps"]["a2"] == ["groom"]
    prompt = r._video_prompt(r.story["shots"][0], plan)
    assert "<Picture 3> is the new frame at 1.0s" in prompt and "<Picture 5> is the character sheet" in prompt


def test_outfit_keeps_only_clothing():
    from rea_toon.remake import outfit
    got = outfit("年轻漂亮女性，棕黑色微卷长发，身材苗条，神色略带骄纵。身穿深色居家真丝睡袍或休闲服。")
    assert got == "身穿深色居家真丝睡袍或休闲服"
    assert "长发" not in outfit("年轻男生，留粉橘色短发（相亲时戴粉红色波浪假发），五官秀气。")


def test_split_shots_and_object_cast(tmp_path: Path):
    from rea_toon.analyze import split_shots
    from rea_toon.remake import Remake
    from rea_toon.spec import CastSwap
    shots = split_shots([{"index": 1, "start": 0, "end": 12}, {"index": 2, "start": 12, "end": 14}], [6.65, 9.92, 13.9])
    assert [(s["index"], s["start"], s["end"]) for s in shots] == [(1, 0, 6.65), (2, 6.65, 9.92), (3, 9.92, 12), (4, 12, 14)]
    work = _story(tmp_path)
    spec = RemakeSpec(name="t")
    spec.cast = {"cup": CastSwap(id="cup", name="新杯子", kind="object", original="便携榨汁杯：灰紫色")}
    plan = Remake(work, spec).plan()
    # the product is added to every shot and to both keyframes, even though in_first/in_last only list people
    assert all(p["route"] == "regen" and p["swapped"] == ["cup"] for p in plan["shots"])
    assert plan["shots"][0]["frame_swaps"] == {"first": ["cup"], "last": ["cup"]}


def test_reburn_size_follows_original_line_width():
    from rea_toon.assemble import _sub_size
    s = {"height": 0.0431, "text": "我老姐为了甩掉一个相亲对象", "box": [0.2698, 0.8389, 0.7333, 0.8917]}
    assert 32 <= _sub_size(s, 720, 960) <= 36          # 445px / 13 glyphs
    assert _sub_size({"height": 0.0431, "text": "x"}, 720) == 24  # no box: height fallback


def test_restyled_video_prompt_uses_rewritten_motion(tmp_path: Path, monkeypatch):
    from rea_toon.remake import Remake
    r, shot = _remake_with(tmp_path, {"change": "small"}, performance=PERF)
    r.spec.style = "森林换成海底，云端换成星空"
    monkeypatch.setattr(Remake, "_restyled_motion", lambda self, s, m: "新主角在海底走")
    p = r.shot_plan(shot)
    prompt = r._video_prompt(shot, p)
    # the per-shot rewrite replaces the original action text; the whole old->new table stays out of the prompt
    assert "新主角在海底走" in prompt and "新郎惊慌地说话" not in prompt and "云端换成星空" not in prompt
