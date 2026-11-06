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


def _sub(t0, t1, text, box=(0.4, 0.84, 0.6, 0.88), **k):
    return {"start": t0, "end": t1, "text": text, "box": list(box), **k}


def test_name_card_and_disguise_pair():
    """A name drawn big while its owner is 'absent' points at the disguised person on screen (温月 in a suit)."""
    from rea_toon.analyze import identity_pairs, mark_cards
    subs = [_sub(i, i + 1, "这是一句普通的台词") for i in range(6)]
    card = _sub(9, 10, "温月", box=(0.32, 0.84, 0.53, 0.98))
    story = {"shots": [{"index": 1, "subtitles": subs, "analysis": {"characters": [{"id": "sister"}, {"id": "brother"}]}},
                       {"index": 2, "subtitles": [card], "analysis": {"characters": [{"id": "ex"}, {"id": "brother"}]}}],
             "characters": []}
    assert mark_cards(story) == 1 and card["card"] and card["speaker"] == "title"
    chars = [{"id": "sister", "role": "姐姐（温月）", "shots": [1, 3]}, {"id": "brother", "role": "弟弟", "shots": [1, 2, 3]},
             {"id": "ex", "role": "前男友", "shots": [2]}]
    pairs = identity_pairs(story, chars)
    assert ("sister", "ex") in pairs and "人名卡" in pairs[("sister", "ex")][0]
    assert not any("brother" in k for k in pairs)  # always on screen with both: never a suspect


def test_per_line_speakers_drive_mouths(tmp_path: Path):
    """Back-and-forth dialogue: each line names its own speaker; off-screen lines close every mouth on screen."""
    from rea_toon.remake import Remake
    shot = {"index": 1, "start": 10.0, "duration": 3.0, "analysis": {"speaker": "ceo"}, "subtitles": [
        {"start": 10.0, "end": 11.5, "text": "我这个人条件比较特别", "speaker": "bro", "on_screen": False},
        {"start": 11.5, "end": 12.4, "text": "说来听听", "speaker": "ceo", "on_screen": True},
        {"start": 12.4, "end": 12.9, "text": "温月", "card": True, "speaker": "title"}]}
    lines = Remake._lines(shot)
    assert [l["who"] for l in lines] == ["bro", "ceo"]  # the name card is not a line
    assert Remake._speaker(shot) == "ceo"
    r = Remake.__new__(Remake)
    r.chars = {"bro": {"role": "弟弟"}, "ceo": {"role": "总裁"}}
    from rea_toon.spec import CastSwap
    r.spec = RemakeSpec(name="t", cast={"ceo": CastSwap(id="ceo", name="猫总", look="猫")})
    text = r._speaking_text(shot, audio=True)
    assert "画外的弟弟在说话" in text and "1.5–2.4s 总裁（猫总）说话" in text and "其他角色嘴巴闭着" in text
    assert "说来听听" not in text  # with audio the words are not spelled out (they get burned in)
    assert r._talks(shot, {"swapped": ["ceo"]}) and not r._talks(shot, {"swapped": ["bro"]})


def test_frozen_rule_against_original():
    from rea_toon import media
    def fake(c, o):
        vals = {"clip": c, "orig": o}
        return lambda v, s=0, d=None: vals["clip" if str(v) == "clip" else "orig"]
    import unittest.mock as um
    with um.patch.object(media, "still_fraction", fake(0.72, 0.19)):
        assert media.frozen(Path("clip"), 3, Path("orig"), 0)["frozen"]
    with um.patch.object(media, "still_fraction", fake(0.30, 0.20)):
        assert not media.frozen(Path("clip"), 3, Path("orig"), 0)["frozen"]
    with um.patch.object(media, "still_fraction", fake(0.80, 0.70)):  # the original shot is still too
        assert not media.frozen(Path("clip"), 3, Path("orig"), 0)["frozen"]


def test_offscreen_lines_are_muted_for_lipsync():
    """An audio-driven clip moves the visible mouth to every voice it hears: off-camera lines are silenced."""
    from rea_toon.remake import Remake
    shot = {"index": 1, "start": 10.0, "duration": 3.0, "subtitles": [
        {"start": 10.0, "end": 11.6, "text": "a", "speaker": "bro", "on_screen": False},
        {"start": 11.5, "end": 12.4, "text": "b", "speaker": "ceo", "on_screen": True}]}
    r = Remake.__new__(Remake)
    assert r._offscreen(shot) == [(0.0, 1.5)]  # the overlap with the on-camera line stays audible
    assert r._offscreen({**shot, "subtitles": [{**x, "speaker": None} for x in shot["subtitles"]][:0]}) == []


def test_scenes_cover_all_shots_and_scene_looks():
    from rea_toon.breakdown import _fix_scenes, look_in
    sc = _fix_scenes([{"shots": [3, 5]}, {"shots": [1, 2]}, {"shots": [8, 20]}, {"shots": [4, 6]}], 10)
    assert [s["shots"] for s in sc] == [[1, 2], [3, 5], [6, 7], [8, 10]] and [s["id"] for s in sc] == ["S1", "S2", "S3", "S4"]
    story = {"breakdown": {"scenes": [{"id": "S1", "shots": [1, 4],
                                       "looks": {"bro": {"wear": "黄色卫衣", "hair": "粉色长假发", "face": "白面膜"},
                                                 "sis": {"wear": "睡袍", "hair": "棕色长卷发", "props": "鸡毛掸子"}},
                                       "changes": [{"shot": 3, "who": "bro", "wear": "黄色卫衣", "hair": "粉橘色短发"}]}]}}
    assert look_in(story, "bro", 2, dress=True) == "黄色卫衣；粉色长假发；白面膜"
    assert look_in(story, "bro", 4, dress=True) == "黄色卫衣"            # wig off, face mask gone
    assert look_in(story, "bro", 4) == "黄色卫衣；粉橘色短发"
    assert look_in(story, "sis", 1, dress=True) == "睡袍；鸡毛掸子"      # own hair is not carried to a new character
    assert look_in(story, "bro", 9) == "" and look_in(story, "crowd", 1) == ""


def test_risks_and_lint_and_lock(tmp_path: Path):
    from rea_toon.breakdown import lint, lock, lock_state, require_locked, risks
    work = _story(tmp_path)
    story = json.loads((work / "story.json").read_text(encoding="utf-8"))
    s1 = story["shots"][0]
    s1["analysis"].update(in_first=[], in_last=["groom"], characters=[{"id": "groom", "action": "举起酒杯"}])
    s1["subtitles"] = [dict(SUB[0], speaker="chef", on_screen=False), dict(SUB[0], text="好", speaker="groom")]
    s1["key"] = {"transition_in": "white_flash"}
    assert {"offscreen_line", "multi_speaker", "entrance", "flash_in", "hands"} <= set(risks(story, s1))
    msgs = [x["msg"] for x in lint(story) if x["level"] == "error"]
    assert any("深度拆解" in m for m in msgs) and any("表演拆解" in m for m in msgs)
    (work / "story.json").write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError):
        lock(work)
    lock(work, force=True)
    story = json.loads((work / "story.json").read_text(encoding="utf-8"))
    assert lock_state(story) == "locked"
    require_locked(story)
    story["shots"][0]["subtitles"][0]["speaker"] = "groom"  # edited after review
    assert lock_state(story) == "stale"
    with pytest.raises(RuntimeError):
        require_locked(story)


def test_remake_dresses_from_scene_and_keeps_the_beat(tmp_path: Path):
    r, shot = _remake_with(tmp_path, {"in_first": ["groom"], "in_last": ["groom"]}, performance=PERF)
    r.story["breakdown"] = {"scenes": [{"id": "S1", "shots": [1, 2], "looks": {"groom": "黄色卫衣，脸上敷白面膜"}}]}
    shot["key"] = {"priority": "must", "keep": ["新郎举杯的慢动作"]}
    prompt = r._frame_prompt(shot, ["groom"], first_ref=2)
    assert "黄色卫衣，脸上敷白面膜" in prompt and "黑西装" not in prompt.split("原外貌")[1].split("）")[1]
    assert "新郎举杯的慢动作" in prompt
    assert "新郎举杯的慢动作" in r._video_prompt(shot, r.shot_plan(shot))
    import pytest
    with pytest.raises(RuntimeError):
        r.run(until="cast")  # not locked


def test_inner_monologue_keeps_mouths_shut(tmp_path: Path):
    """A character's own thoughts: their voice, on camera, but nobody's mouth moves (and the voice is muted for lipsync)."""
    from rea_toon.remake import Remake
    from rea_toon.subtitles import line_mode
    from rea_toon.breakdown import risks
    assert line_mode({"on_screen": False}) == "off" and line_mode({}) == "on"  # older story.json
    assert line_mode({"mode": "inner", "on_screen": True}) == "inner"
    shot = {"index": 1, "start": 0.0, "duration": 3.0, "subtitles": [
        {"start": 0.0, "end": 1.4, "text": "这女人到底想干什么", "speaker": "ceo", "mode": "inner", "on_screen": False},
        {"start": 1.5, "end": 2.6, "text": "沈总请坐", "speaker": "bro", "mode": "on", "on_screen": True}]}
    r = Remake.__new__(Remake)
    r.chars = {"bro": {"role": "弟弟"}, "ceo": {"role": "总裁"}}
    from rea_toon.spec import CastSwap
    r.spec = RemakeSpec(name="t", cast={"ceo": CastSwap(id="ceo", name="猫总", look="猫")})
    text = r._speaking_text(shot, audio=True)
    assert "内心独白" in text and "嘴巴闭着" in text and "1.5–2.6s 弟弟说话" in text
    assert r._offscreen(shot) == [(0.0, 1.4)]  # muted in the lipsync voice
    assert Remake._speaker(shot) == "bro" and not r._talks(shot, {"swapped": ["ceo"]})
    story = {"characters": [{"id": "ceo"}, {"id": "bro"}], "shots": [shot]}
    rs = risks(story, {**shot, "analysis": {"characters": [{"id": "ceo"}, {"id": "bro"}]}})
    assert "inner_line" in rs and "offscreen_line" not in rs


def test_function_goes_into_prompts(tmp_path: Path):
    r, shot = _remake_with(tmp_path, {"in_first": ["groom"], "in_last": ["groom"]}, performance=PERF)
    shot["key"] = {"priority": "must", "function": "新郎当众出丑，观众觉得好笑", "keep": ["新郎举杯"]}
    assert "这一镜的作用是新郎当众出丑，观众觉得好笑" in r._frame_prompt(shot, ["groom"], first_ref=2)
    r.spec.style = "西游记天宫"
    assert "按改编换成对应的新东西" in r._keep_text(shot)


def test_two_people_frame_check_names_places(tmp_path: Path):
    from rea_toon.spec import CastSwap
    r, shot = _remake_with(tmp_path, {"characters": [{"id": "groom", "where": "画面左侧"},
                                                     {"id": "bride", "where": "画面右侧"}]})
    r.chars["bride"] = {"id": "bride", "role": "新娘"}
    r.spec.cast["bride"] = CastSwap(id="bride", name="白晶晶")
    text = r._places(shot, ["groom", "bride"])
    assert "「孙悟空」必须出现在图1里新郎的位置（画面左侧）" in text and "白晶晶" in text
    assert r._places(shot, ["groom"]) == ""  # one person: nothing to mix up


def test_scene_anchor_reference(tmp_path: Path):
    """Shots of one scene look at the scene's anchor (most faces, then must, then closer); a star, not a chain."""
    from rea_toon.remake import Remake
    from rea_toon.spec import CastSwap
    work = _story(tmp_path)
    story = json.loads((work / "story.json").read_text(encoding="utf-8"))
    mk = lambda i, size, vis: {"id": f"shot_{i:03d}", "index": i, "start": i, "end": i + 2, "duration": 2, "subtitles": [],
                               "analysis": {"characters": [{"id": "groom", "visibility": vis}], "shot_size": size,
                                            "in_first": ["groom"], "in_last": ["groom"]}}
    story["shots"] = [mk(1, "全景", "back"), mk(2, "近景", "face"), mk(3, "中景", "face"), mk(4, "近景", "face")]
    story["characters"][0]["shots"] = [1, 2, 3, 4]
    story["breakdown"] = {"scenes": [{"id": "S1", "shots": [1, 3]}, {"id": "S2", "shots": [4, 4]}]}
    (work / "story.json").write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
    r = Remake(work, RemakeSpec(name="t", cast={"groom": CastSwap(id="groom", name="孙悟空")}))
    plan = {p["index"]: p for p in r.plan()["shots"]}
    assert "scene_ref" not in plan[2] and plan[1]["scene_ref"] == {"shot": 2, "who": ["groom"]}
    assert plan[3]["scene_ref"]["shot"] == 2 and "scene_ref" not in plan[4]  # alone in its scene
    assert r._scene_ref(story["shots"][0], plan[1]["scene_ref"]) is None  # anchor not made yet
    f = r.out / "frames" / "shot_002_first.png"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"x")
    path, hint = r._scene_ref(r.story["shots"][0], plan[1]["scene_ref"])
    assert path == f and "第 2 镜" in hint and "服装和配饰" in hint
    r.spec.scene_ref = False
    assert not any(p.get("scene_ref") for p in r.plan()["shots"])


def test_story_day_carries_looks_and_flags_outfit_jumps():
    from rea_toon.breakdown import look_in, lint
    story = {"characters": [{"id": "sis", "importance": "lead"}], "shots": [], "breakdown": {"scenes": [
        {"id": "S1", "shots": [1, 3], "day": "D1", "looks": {"sis": {"wear": "红色连衣裙", "props": "右手拿着半杯红酒"}},
         "changes": [{"shot": 3, "who": "sis", "wear": "红色连衣裙", "props": "空酒杯"}]},
        {"id": "S2", "shots": [4, 5], "day": "D1", "looks": {}},                       # only her hand in frame
        {"id": "S3", "shots": [6, 7], "day": "D1", "flashback": True, "looks": {}},    # a memory: not the same day
        {"id": "S4", "shots": [8, 9], "day": "D1", "looks": {"sis": {"wear": "灰色运动卫衣和牛仔裤"}}}]}}
    assert look_in(story, "sis", 4) == "红色连衣裙；空酒杯"  # end-of-S1 state, prop state included
    assert look_in(story, "sis", 6) == ""
    msgs = [x["msg"] for x in lint(story)]
    assert any("同一剧情日 D1" in m and "S4" in m for m in msgs)


def test_screen_text_ocr_and_keyframe_lines(tmp_path: Path, monkeypatch):
    from rea_toon import subtitles, breakdown
    work = tmp_path
    for n in ("first", "mid", "last"):
        (work / f"{n}.jpg").write_bytes(b"x")
    shot = {"index": 1, "start": 0, "end": 2, "duration": 2, "frames": {n: f"{n}.jpg" for n in ("first", "mid", "last")},
            "subtitles": [{"text": "你好", "box": [0.4, 0.88, 0.6, 0.93]}]}
    reads = {"first.jpg": [{"text": "你好", "box": [0.4, 0.88, 0.6, 0.93]},          # the subtitle itself
                           {"text": "云顶餐厅", "box": [0.1, 0.1, 0.3, 0.15]}],
             "mid.jpg": [{"text": "云顶餐厅", "box": [0.1, 0.1, 0.3, 0.15]}, {"text": "震惊!", "box": [0.6, 0.3, 0.9, 0.4]}],
             "last.jpg": []}
    monkeypatch.setattr(subtitles, "read_text", lambda f, *a: reads[Path(f).name])
    found = breakdown._ocr_shot(work, shot)
    assert [(x["text"], x["frames"]) for x in found] == [("云顶餐厅", ["first", "mid"]), ("震惊!", ["mid"])]
    from rea_toon.remake import Remake
    r = Remake.__new__(Remake)
    r.spec = RemakeSpec(name="t")
    shot["screen_text"] = [{**found[0], "kind": "scene"}, {**found[1], "kind": "overlay"}]
    assert "「云顶餐厅」原样保留" in r._text_lines(shot, "first") and "震惊" not in r._text_lines(shot, "first")
    assert "「震惊!」不要画" in r._text_lines(shot, "a1")  # anchors go by the middle frame
    monkeypatch.setattr(subtitles, "read_text", lambda f, *a: [{"text": "云项餐厅", "box": [0, 0, 1, 1]}])
    assert r._missing_text(shot, "first", work / "first.jpg") == []      # one OCR slip is fine
    monkeypatch.setattr(subtitles, "read_text", lambda f, *a: [{"text": "乱码", "box": [0, 0, 1, 1]}])
    assert r._missing_text(shot, "first", work / "first.jpg") == ["云顶餐厅"]
    from rea_toon.breakdown import risks
    rs = risks({"characters": [], "shots": [shot]}, {**shot, "analysis": {}})
    assert "scene_text" in rs and "overlay_text" in rs


def test_overlay_redrawn_on_regenerated_shot(monkeypatch):
    from rea_toon import assemble
    monkeypatch.setattr(assemble, "font_path", lambda: "/f.ttf")
    shot = {"start": 10.0, "end": 13.0, "duration": 3.0, "screen_text": [
        {"text": "震惊", "kind": "overlay", "box": [0.6, 0.3, 0.9, 0.4], "t0": 10.5, "t1": 12.0, "color": "#FFD000"},
        {"text": "云顶餐厅", "kind": "scene", "box": [0.1, 0.1, 0.3, 0.15]}]}
    f = assemble._overlay_filter(shot, 1280, 720)
    assert "震惊" in f and "0xFFD000" in f and "between(t,0.500,2.000)" in f and "云顶餐厅" not in f


def test_likeness_skips_animals_and_seed_budget(tmp_path: Path):
    from rea_toon.remake import Remake
    from rea_toon.spec import CastSwap
    r, _ = _remake_with(tmp_path, {})
    assert r._human("groom")
    r.spec.cast["groom"] = CastSwap(id="groom", name="橘子（博美犬）", look="一只真实的博美犬")
    assert not r._human("groom")
    assert Remake.SYNC_TRIES == 2


def test_freeze_and_transient_detection(tmp_path: Path):
    """A held last second is a freeze, motion before it is not; clicks over quiet noise are transients."""
    import subprocess
    from rea_toon.media import still_runs, transients
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=1",
                    "-vf", "tpad=stop_mode=clone:stop_duration=1", "-pix_fmt", "yuv420p", str(v)], check=True)
    runs = still_runs(v, 0, 2.0)
    assert len(runs) == 1 and abs(runs[0][0] - 1.0) < 0.1 and runs[0][1] > 1.8
    a = tmp_path / "a.wav"
    expr = "0.01*random(0)+if(between(t,0.8,0.82)+between(t,2.3,2.32),0.9*sin(2*PI*900*t),0)"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=16000:d=4", str(a)], check=True)
    hits = [t for t, z in transients(a)]
    assert any(abs(t - 0.8) < 0.05 for t in hits) and any(abs(t - 2.3) < 0.05 for t in hits)


def test_contacts_snap_to_the_hit(tmp_path: Path, monkeypatch):
    from rea_toon import breakdown, media
    shot = {"id": "shot_072", "index": 72, "start": 193.5, "end": 195.6, "duration": 2.1,
            "analysis": {"characters": [{"id": "sister", "action": "把红酒泼向沈冠宇"}]}}
    calm = {"id": "shot_001", "index": 1, "start": 0, "end": 2, "duration": 2, "analysis": {"characters": [{"id": "a", "action": "看着窗外"}]}}
    story = {"audio": {"music": "m.wav"}, "characters": [], "shots": [calm, shot]}
    (tmp_path / "m.wav").write_bytes(b"x")
    monkeypatch.setattr(media, "transients", lambda p: [(12.0, 30.0), (193.8, 11.9), (194.06, 10.0)])
    monkeypatch.setattr(media, "frame_at", lambda v, t, out: out)

    class FakeVLM:
        calls = 0
        def ask_json(self, prompt, images, **kw):
            FakeVLM.calls += 1
            return {"contacts": [{"who": "sister", "to": "shen", "action": "泼酒", "t": 0.45, "hand": "左手"}]}
    assert breakdown.contacts(story, tmp_path, FakeVLM()) == 1 and FakeVLM.calls == 1  # the calm shot costs nothing
    c = shot["contacts"][0]
    assert c["sound"] and c["t"] == 0.3  # snapped to the strongest onset within 0.3 s
    assert "contact" in breakdown.risks(story, shot)


def test_contact_staging_slow_in_video_prompt_and_anchor(tmp_path: Path):
    r, shot = _remake_with(tmp_path, {"in_first": ["groom"], "in_last": ["groom"], "change": "small"}, performance=PERF)
    r.spec.video_engine = "h3local"
    shot["contacts"] = [{"who": "groom", "to": "chef", "action": "新郎把蛋糕拍到厨师脸上", "t": 1.6, "hand": "右手", "sound": True}]
    shot["staging"] = {"angle": "low", "people": {"groom": {"facing": "right", "looking_at": "chef"}}}
    r.chars["chef"] = {"id": "chef", "role": "厨师"}
    shot["key"] = {"fx": ["慢动作"]}
    plan = r.shot_plan(shot)
    assert plan["mode"] == "ref_video" and 1.6 in plan["anchors"].values()
    text = r._video_prompt(shot, plan)
    assert "1.6s 接触：新郎把蛋糕拍到厨师脸上，用右手（原声此刻有音效" in text
    assert "机位仰拍" in text and "脸朝画面右侧、看着厨师" in text and "慢动作" in text


def test_freeze_label_and_aigc_mark(monkeypatch):
    from rea_toon import assemble
    monkeypatch.setattr(assemble, "font_path", lambda: "/f.ttf")
    assert assemble._freeze_filter({"freeze": [[1.5, 3.0]]}, 24) == "freezeframes=first=36:last=71:replace=36"
    story = {"shots": [{"screen_text": [{"text": "AI生成", "kind": "aigc", "box": [0.02, 0.03, 0.12, 0.05]}]},
                       {"screen_text": [{"text": "AI生成", "kind": "aigc", "box": [0.021, 0.03, 0.12, 0.05]}]}]}
    mark = assemble.aigc_mark(story)
    assert mark and "x=25:y=21" in assemble._label_filter("AI生成", 720, 1280, mark)
    assert assemble.aigc_mark({"shots": []}) is None
    assert "x=32:y=18" in assemble._label_filter("AI生成", 720, 1280, None)
    assert assemble._label_filter("", 720, 1280, None) == ""


def test_voice_mismatch(tmp_path: Path):
    from rea_toon.spec import CastSwap
    r, shot = _remake_with(tmp_path, {"speaker": "groom"}, SUB)
    r.chars["groom"]["role"] = "新郎"
    r.spec.cast["groom"] = CastSwap(id="groom", name="白雪公主", look="黑发少女，红唇")
    assert r.voice_mismatch()[0]["why"] == "性别 男→女"
    r.spec.cast["groom"] = CastSwap(id="groom", name="小王子", look="五岁的小男孩")
    assert "年龄 成年→小" in r.voice_mismatch()[0]["why"]
    r.spec.cast["groom"] = CastSwap(id="groom", name="孙悟空", look="猴脸")
    assert r.voice_mismatch() == []  # animals: voiced by convention


def test_still_picture_motion_and_kenburns(tmp_path: Path):
    """A picture pushed in / pulled out is found with its window path; rendering that path again reproduces it."""
    import subprocess
    import cv2
    import numpy as np
    from rea_toon.media import image_motion, kenburns
    rng = np.random.default_rng(1)
    img = cv2.resize((rng.random((36, 64, 3)) * 255).astype(np.uint8), (640, 360), interpolation=cv2.INTER_CUBIC)
    for k in range(40):  # some edges for the features
        cv2.circle(img, (int(rng.integers(0, 640)), int(rng.integers(0, 360))), int(rng.integers(5, 30)),
                   tuple(int(x) for x in rng.integers(0, 255, 3)), -1)
    pic = tmp_path / "p.png"
    cv2.imwrite(str(pic), img)
    zin, out, hold = tmp_path / "in.mp4", tmp_path / "out.mp4", tmp_path / "hold.mp4"
    kenburns(pic, [[0, 0, 0, 1, 1], [3, 0.1, 0.05, 0.9, 0.85]], (320, 180), 25, 3.0, zin)
    kenburns(pic, [[0, 0.15, 0.1, 0.85, 0.8], [3, 0, 0, 1, 1]], (320, 180), 25, 3.0, out)
    kenburns(pic, [[0, 0, 0, 1, 1], [3, 0, 0, 1, 1]], (320, 180), 25, 3.0, hold)
    r = image_motion(zin, 0, 3.0)
    assert r["kind"] == "pan" and r["base"] == "first"
    end = r["path"][-2]  # the last sample, 0.06 s before the end
    assert all(abs(a - b) < 0.02 for a, b in zip(end[1:], [0.098, 0.049, 0.902, 0.853]))
    r = image_motion(out, 0, 3.0)
    assert r["kind"] == "pan" and r["base"] == "last" and abs(r["path"][1][1] - 0.147) < 0.02
    assert image_motion(hold, 0, 3.0)["kind"] == "still"
    moving = tmp_path / "m.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=2",
                    "-pix_fmt", "yuv420p", str(moving)], check=True)
    assert image_motion(moving, 0, 2.0)["kind"] == "full"


def test_kenburns_route_skips_the_video_model(tmp_path: Path):
    from rea_toon.spec import CastSwap
    work = _story(tmp_path)
    story = json.loads((work / "story.json").read_text(encoding="utf-8"))
    path = [[0.0, 0.15, 0.1, 0.85, 0.8], [3.0, 0, 0, 1, 1]]
    story["shots"][0]["motion"] = {"kind": "pan", "base": "last", "path": path}
    story["shots"][0]["analysis"]["in_last"] = ["groom"]
    (work / "story.json").write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
    from rea_toon.remake import Remake
    spec = RemakeSpec(name="t", cast={"groom": CastSwap(id="groom", name="孙悟空")})
    plan = Remake(work, spec).plan()
    p = plan["shots"][0]
    assert p["mode"] == "kenburns" and p["frames"] == {"last": "edit"} and p["kenburns"]["path"] == path
    assert plan["video_calls"] == 0 and plan["kenburns_shots"] == 1 and plan["pilot"] == []
    spec.kenburns = False
    assert Remake(work, spec).plan()["shots"][0]["mode"] != "kenburns"
    # nobody to swap in the widest view: nothing to redo
    story["shots"][0]["analysis"]["in_last"] = []
    (work / "story.json").write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
    spec.kenburns = True
    assert Remake(work, spec).plan()["shots"][0]["route"] == "reuse"


def test_comic_elements_in_prompts_and_risks(tmp_path: Path):
    from rea_toon.breakdown import risks
    from rea_toon.spec import CastSwap
    r, shot = _remake_with(tmp_path, {"characters": [{"id": "groom", "where": "中间"}]})
    shot["staging"] = {"angle": "eye", "people": {}, "effects": [{"what": "剑气", "color": "蓝色", "from": "groom"}],
                       "marks": ["汗滴"], "panels": 3}
    shot["motion"] = {"kind": "partial", "where": [0.7, 0.2, 1.0, 0.9]}
    shot["screen_text"] = [{"text": "你给我站住", "kind": "bubble", "box": [0.1, 0.1, 0.3, 0.2], "frames": ["first"]}]
    r.spec.cast["groom"] = CastSwap(id="groom", name="孙悟空", fx_color="金色")
    lines = r._drawn_lines(shot) + r._text_lines(shot, "first")
    assert "剑气（颜色换成金色（原片是蓝色）" in lines and "汗滴" in lines and "3 宫格" in lines and "「你给我站住」" in lines
    motion = r._drawn_motion(shot)
    assert "分格边框始终不动" in motion and "只有画面中右那一小块在动" in motion
    story = {"characters": [], "shots": [shot]}
    got = set(risks(story, shot))
    assert {"vfx", "panels", "manga_marks", "bubble", "partial_motion"} <= got


def test_expression_list_and_sheet_refs(tmp_path: Path):
    from rea_toon import breakdown
    from rea_toon.spec import CastSwap
    story = {"characters": [{"id": "groom", "role": "新郎", "importance": "lead"}, {"id": "crowd", "importance": "extra"}],
             "shots": [{"index": i, "analysis": {"characters": [{"id": "groom", "expression": e, "visibility": "face"}]}}
                       for i, e in enumerate(["冷笑", "嘴角上扬地嘲讽", "震惊", "愤怒"], 1)]}

    class FakeVLM:
        def ask_json(self, prompt, images, **kw):
            assert "1:冷笑" in prompt and not images
            return {"characters": {"groom": [{"name": "冷笑", "look": "嘴角一边上扬", "shots": [1, 2]},
                                             {"name": "震惊", "look": "睁大眼", "shots": ["3"]}], "nobody": []}}
    assert breakdown.expressions(story, FakeVLM()) == 2
    assert story["characters"][0]["expressions"][1]["shots"] == [3]
    r, shot = _remake_with(tmp_path, {"characters": [{"id": "groom"}]})
    r.chars["groom"]["expressions"] = story["characters"][0]["expressions"]
    r.spec.cast["groom"] = CastSwap(id="groom", name="孙悟空")
    assert r._expr_cids() == []  # opt-in
    r.spec.expression_sheet = True
    assert r._expr_cids() == ["groom"] and r.plan()["cast_sheets"] == 2
    assert r._expression("groom", {"index": 2}) == "冷笑" and r._expression("groom", {"index": 4}) == ""
