import json
from pathlib import Path


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


SUB = [{"start": 0.5, "end": 2.0, "text": "来者皆是客", "y_center": 0.9, "height": 0.05, "box": [0.4, 0.88, 0.6, 0.93]}]
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
