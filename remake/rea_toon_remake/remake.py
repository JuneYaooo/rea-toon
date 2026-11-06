"""Locked story.json (analysis + deep breakdown) + remake.yaml -> new keyframes and clips.

The remake never analyses: everything it knows about the original (who says what, each character's look in each
scene, what must survive in each shot, where the generation risks are) comes from the reviewed, locked breakdown.

Dependency graph (each node cached by input hash, so editing one character only redoes what depends on it):

    cast sheet (gpt-image-2) ──┐
    original first frame ──────┼─> new first frame (gpt-image-2 edit) ──┐
    original last frame ───────┴─> new last frame  (edit, + new first) ─┴─> clip (H3 / Seedance) ─> assemble

Shots without any swapped role (and no global style) keep the original footage.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import cache
from .engines import GptImage2, make_video_engine
from .prompts import (RESTYLE_MOTION_PROMPT, CLIP_CHECK_PROMPT, EXPRESSION_REF_HINT, EXPRESSION_SHEET_PROMPT, LIKENESS_PROMPT, ORIGINAL_FACE_NOTE, CROWD_SHEET_PROMPT, FRAME_CHECK_PROMPT, FRAME_RESTYLE_PROMPT, FRAME_SWAP_PROMPT, LAST_FRAME_HINT, SCENE_REF_HINT, SHEET_FROM_IMAGE_PROMPT,
                      REF_VIDEO_PROMPT, SHEET_PROMPT, VIDEO_PROMPT)
from rea_toon.breakdown import look_in, require_locked, scene_of
from rea_toon.subtitles import line_mode
from .spec import RemakeSpec, parse_shots

LOG = logging.getLogger(__name__)



_OUTFIT_WORDS = ("穿", "戴", "系", "配", "服", "装", "裙", "衣", "衫", "裤", "鞋", "袍", "领带", "马甲", "项链", "外套",
                 "眼镜", "帽", "包", "耳环", "手表", "围裙", "制服", "胸花", "假发")


def outfit(appearance: str) -> str:
    """Only the clothing/accessory clauses of an original character's look.

    costume: keep must not drag the original person's face, hair and build into the prompt: with a non-human
    (or just very different) new character the image model redraws the original person instead.
    """
    parts = [x.strip() for x in re.split(r"[。；;，,、（）()]", appearance) if x.strip()]
    keep = [x for x in parts if any(w in x for w in _OUTFIT_WORDS)]
    return "，".join(keep) if keep else appearance

class Remake:
    def __init__(self, work: Path, spec: RemakeSpec, out: Path | None = None):
        self.work = work
        self.spec = spec
        self.story = json.loads((work / "story.json").read_text(encoding="utf-8"))
        self.out = out or (work / "remakes" / spec.name)
        self.out.mkdir(parents=True, exist_ok=True)
        self.chars = {c["id"]: c for c in self.story.get("characters", [])}
        # products are not in the VLM cast table: a cast entry with kind=object adds itself to every shot
        for cid, sw in spec.cast.items():
            if sw.kind == "object" and cid not in self.chars:
                self.chars[cid] = {"id": cid, "role": sw.original.split("：")[0].split(":")[0][:20] or cid,
                                   "appearance": sw.original or cid, "importance": "lead", "object": True,
                                   "shots": [s["index"] for s in self.story["shots"]]}
        merged = {m["drop"]: m["keep"] for m in (self.story.get("refined") or {}).get("merges", [])}
        gone = {cid: merged[cid] for cid in spec.cast if cid not in self.chars and cid in merged}
        if gone:
            raise ValueError("remake.yaml 里的 " + "、".join(f"{a}（就是 {b}，换了一身衣服）" for a, b in gone.items())
                             + " 已在 refine/analyze 里合并成同一个角色：删掉这些条目，它们的镜头会按合并后的角色来换")
        unknown = [cid for cid in spec.cast if cid not in self.chars]
        if unknown:
            raise ValueError(f"remake.yaml 里的角色 {unknown} 不在 story.json 里；可选：{list(self.chars)}")
        self.keep: set[str] = set()  # stages whose existing outputs are kept even if their inputs changed
        self.expr: dict[str, Path] = {}  # expression sheets (spec.expression_sheet), made with the cast
        self._image: GptImage2 | None = None
        self._video = None
        self._vlm = None

    # ---- engines (lazy so `plan` works without keys) ----
    @property
    def image(self) -> GptImage2:
        if self._image is None:
            self._image = GptImage2(self.spec.image_channel)
        return self._image

    @property
    def video(self):
        if self._video is None:
            self._video = make_video_engine(self.spec.video_engine)
        return self._video

    @property
    def vlm(self):
        if self._vlm is None:
            from rea_toon.vlm import VLM
            self._vlm = VLM(cache_dir=self.work / "analysis" / "vlm_cache")
        return self._vlm

    # ---- planning ----
    def _style_text(self) -> str:
        return self.spec.style or f"保持原片画风：{self.story.get('style', '写实电影感')}"

    def shot_plan(self, shot: dict) -> dict:
        a = shot.get("analysis") or {}
        in_shot = {c.get("id") for c in a.get("characters") or []}
        in_shot |= {cid for cid, c in self.chars.items() if shot["index"] in c.get("shots", [])}
        swapped = sorted(cid for cid in self.spec.cast if cid in in_shot)
        regen = bool(swapped) or bool(self.spec.style) or self.spec.regen == "all"
        plan = {"index": shot["index"], "route": "regen" if regen else "reuse", "swapped": swapped}
        if not regen:
            return plan
        # short shots get one frame, except a big move inside one scene (same people at both ends, e.g. a jacket
        # lowered to reveal someone): with only a covered first frame the model invents who is revealed.
        # Different people at the two ends is a transition cut, where a last frame would morph one scene into the next.
        def frame(name: str) -> tuple[str, list[str]]:
            listed = a.get(f"in_{name}")
            visible = set(listed) if isinstance(listed, list) else in_shot
            visible |= {c for c in swapped if self.chars[c].get("object")}  # in_first/in_last only list people
            fs = [cid for cid in swapped if cid in visible]
            return ("edit" if (fs or self.spec.style) else "clean"), fs

        # one picture held or pushed / panned (动态漫画): the new keyframe of its widest view, moved the same way
        # locally. Cheaper, and truer than a video model, which would animate what the original keeps still.
        mo = shot.get("motion") or {}
        if self.spec.kenburns and mo.get("kind") in ("still", "pan"):
            base = mo.get("base", "first")
            how, fs = frame(base)
            if how == "clean":  # nobody to swap in the widest view: the original shot stays
                return {"index": shot["index"], "route": "reuse", "swapped": []}
            path = mo.get("path") or [[0.0, 0.0, 0.0, 1.0, 1.0], [shot["duration"], 0.0, 0.0, 1.0, 1.0]]
            plan.update(frames={base: how}, frame_swaps={base: fs}, mode="kenburns",
                        kenburns={"base": base, "path": path}, gen_seconds=None)
            return plan
        reveal = a.get("change") == "large" and a.get("in_first") and a.get("in_first") == a.get("in_last")
        use_last = self.spec.frames == "first_last" and (shot["duration"] >= 0.8 or reveal)
        frames = {}
        for name in ("first", "last") if use_last else ("first",):
            frames[name], plan.setdefault("frame_swaps", {})[name] = frame(name)
        plan["frames"] = frames
        speaker = self._speaker(shot)
        # dialogue of a swapped role, face visible, camera roughly still: drive the mouth with the original voice
        # (not for fade-ins: the audio workflow only sees the first frame and would keep the whole shot dark)
        # (not for big camera moves: it only sees the first frame, so it re-frames the shot or, when the first frame
        #  shows just legs/hands, invents the character's face; seen on every large-move shot tried so far)
        if (self.spec.lipsync and self.spec.video_engine in ("h3", "h3local") and speaker in swapped and a.get("lip_visible")
                and a.get("change") != "large"
                and shot.get("subtitles") and shot["duration"] <= 15
                and not self._fades_in(shot)):
            plan["mode"] = "lipsync"
        elif "last" in frames:
            plan["mode"] = "first_last"
        else:
            plan["mode"] = "first"
        if all(v == "clean" for v in frames.values()) and swapped:
            if not self.spec.style and all(self._is_crowd(c) for c in swapped):
                return {"index": shot["index"], "route": "reuse", "swapped": []}  # no visible crowd to change
            plan["mode"] = "refs"  # role appears only mid-shot: animate with the cast sheet as a reference
        # still-camera dialogue stays on lipsync even on h3local: the mouth follows the original voice (pilot:
        # corr 0.90 in 4 min vs ref_video 0.57 best of 3 at 15 min each), and nothing of the original people leaks
        if self._motion_ref(shot) and plan["mode"] != "lipsync":
            plan["mode"] = "ref_video"  # self-hosted H3 Ref2VA: the original shot itself drives motion and timing
            # anchors: extra swapped keyframes inside the shot (each outfit / each scene of a long take), passed as refs
            offs = self.spec.anchors.get(shot["index"], [])
            # nobody to swap in the first frame (a flash transition, people entering later) but they are there by the
            # end: without a new-look frame in between, the original actors walk in (cat/dog remake shot 32)
            enters = (frames.get("first") == "clean" and frames.get("last") == "edit"
                      or {"entrance", "flash_in"} & set(shot.get("risks") or []))
            if (not offs and enters and frames.get("last") == "edit"
                    and shot["duration"] >= 1.5 and shot["index"] not in self.spec.anchors):
                offs = [round(shot["duration"] * 0.35, 2), round(shot["duration"] * 0.65, 2)]
            # a contact (slap, splash, grab) is where clips break and where the original mix has its hit:
            # a swapped keyframe at that moment pins both the bodies and the timing
            if shot["index"] not in self.spec.anchors:
                for c in shot.get("contacts") or []:
                    if 0.3 < c["t"] < shot["duration"] - 0.2 and all(abs(c["t"] - t) > 0.4 for t in offs):
                        offs = sorted([*offs, round(c["t"], 2)])
            offs = [t for t in offs if 0 < t < shot["duration"]][:7 - len(swapped)]
            for k, t in enumerate(offs, 1):
                frames[f"a{k}"] = "edit"
                plan.setdefault("frame_swaps", {})[f"a{k}"] = swapped
                plan.setdefault("anchors", {})[f"a{k}"] = round(t, 2)
        plan["gen_seconds"] = None  # filled by engine.clamp when the engine is known
        return plan

    def _motion_ref(self, shot: dict) -> bool:
        """Ref2VA takes 2-15s reference videos: shots of 0.8-2 s get theirs padded with the last frame (H3 FL2VA,
        the alternative, barely moves). Shorter ones are mostly transitions and stay on first/last frames."""
        skip = parse_shots(str(self.spec.no_motion_ref), len(self.story["shots"])) if self.spec.no_motion_ref else []
        return (self.spec.video_engine == "h3local" and self.spec.motion_ref
                and 0.8 <= shot["duration"] <= 15 and shot["index"] not in skip)

    def _fades_in(self, shot: dict) -> bool:
        if (shot.get("key") or {}).get("transition_in") in ("fade", "white_flash", "dissolve"):
            return True
        from .media import mean_luma
        try:
            first, mid = (self.work / shot["frames"][k] for k in ("first", "mid"))
            return mean_luma(first) < 0.5 * mean_luma(mid)
        except Exception:
            return False

    def selected(self) -> list[dict]:
        idx = set(parse_shots(self.spec.shots, len(self.story["shots"])))
        return [s for s in self.story["shots"] if s["index"] in idx]

    def plan(self) -> dict:
        """Plan for the whole film. Shots outside `spec.shots` keep the original footage; CLI `--shots`
        only limits what gets processed in one run (see `run(only=...)`), never what the film is made of."""
        sel = {s["index"] for s in self.selected()}
        shots = [self.shot_plan(s) if s["index"] in sel else {"index": s["index"], "route": "reuse", "swapped": []}
                 for s in self.story["shots"]]
        regen = [p for p in shots if p["route"] == "regen"]
        if self.spec.scene_ref:
            self._scene_anchors(regen)
        edits = sum(1 for p in regen for v in p["frames"].values() if v == "edit")
        gen = [p for p in regen if p.get("mode") != "kenburns"]
        sheets = len(self.spec.cast) + len(self._expr_cids())
        summary = {"name": self.spec.name, "shots": shots, "regen_shots": len(regen),
                   "reuse_shots": len(shots) - len(regen), "cast_sheets": sheets,
                   "image_calls": sheets + edits, "video_calls": len(gen), "kenburns_shots": len(regen) - len(gen),
                   "video_seconds": round(sum(self.story["shots"][p["index"] - 1]["duration"] for p in gen), 1),
                   "kept_roles": self.kept_roles(sel), "pilot": self.pilot_shots(shots),
                   "voice_mismatch": self.voice_mismatch()}
        (self.out / "plan.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        return summary

    def _people(self, p: dict) -> set[str]:
        """Swapped people (not crowds, not products) edited into a shot's first frame."""
        if (p.get("frames") or {}).get("first") != "edit":
            return set()
        return {c for c in p.get("frame_swaps", {}).get("first", [])
                if not self._is_crowd(c) and self.spec.cast[c].kind != "object"}

    def _scene_anchors(self, regen: list[dict]) -> None:
        """Per scene, the shot whose new first frame the scene's other shots look at (ViMax-style shot dependency,
        but a star, not a chain: redoing one shot only invalidates the shots that look at the anchor).
        Anchor = shows the most swapped faces clearly, then a must-keep beat, then a closer shot, then the earliest."""
        story = {s["index"]: s for s in self.story["shots"]}
        close = ("特写", "近景", "中近景")

        def score(p: dict) -> tuple:
            s = story[p["index"]]
            vis = {c.get("id"): c.get("visibility") for c in (s.get("analysis") or {}).get("characters") or []}
            faces = sum(1 for c in self._people(p) if vis.get(c) in ("face", "profile", None))
            size = str((s.get("analysis") or {}).get("shot_size", ""))
            return (-faces, (s.get("key") or {}).get("priority") != "must", not any(k in size for k in close), p["index"])

        groups: dict[str, list[dict]] = {}
        for p in regen:
            sc = scene_of(self.story, p["index"])
            if sc and self._people(p):
                groups.setdefault(sc["id"], []).append(p)
        for ps in groups.values():
            if len(ps) < 2:
                continue
            anchor = min(ps, key=score)
            for p in ps:
                shared = sorted(self._people(p) & self._people(anchor))
                if p is not anchor and shared:
                    p["scene_ref"] = {"shot": anchor["index"], "who": shared}

    _FEMALE = ("女", "新娘", "姐", "妹", "妈", "母", "奶奶", "婆", "公主", "皇后", "王后", "夫人", "小姐", "她", "妻", "娘", "姑")
    _MALE = ("男", "新郎", "哥", "弟", "爸", "父", "爷", "公子", "王子", "先生", "他", "丈夫", "叔", "舅", "伯")
    _OLD = ("老人", "老头", "老太", "爷爷", "奶奶", "外婆", "外公", "大爷", "大妈", "老奶奶", "老爷爷", "白发苍苍")
    _CHILD = ("小孩", "孩子", "男孩", "女孩", "婴儿", "幼儿", "小朋友", "小学生", "萌娃")

    @classmethod
    def _voice_of(cls, text: str) -> tuple[str | None, str | None]:
        """(gender, age) a description implies, by keywords; None where it says nothing clear."""
        f, m = sum(text.count(w) for w in cls._FEMALE), sum(text.count(w) for w in cls._MALE)
        gender = "女" if f > m else "男" if m > f else None
        age = "老" if any(w in text for w in cls._OLD) else "小" if any(w in text for w in cls._CHILD) else None
        return gender, age

    def voice_mismatch(self) -> list[dict]:
        """Swapped roles that speak, whose new look implies another gender or age than the voice in the original mix
        (the remake keeps that voice). Animals and products are voiced by convention and skipped."""
        talking = {x.get("speaker") for s in self.story["shots"] for x in s.get("subtitles") or [] if x.get("speaker")}
        talking |= {(s.get("analysis") or {}).get("speaker") for s in self.story["shots"] if s.get("subtitles")}  # older dirs
        out = []
        for cid, sw in self.spec.cast.items():
            if cid not in talking or sw.kind == "object" or self._is_crowd(cid) or not self._human(cid):
                continue
            c = self.chars.get(cid, {})
            og, oa = self._voice_of(f"{c.get('role', '')} {c.get('appearance', '')}")
            ng, na = self._voice_of(f"{sw.name} {sw.look}")
            why = []
            if og and ng and og != ng:
                why.append(f"性别 {og}→{ng}")
            if na and na != oa:  # only when the new look says an age; an unstated one may well fit
                why.append(f"年龄 {oa or '成年'}→{na}")
            if why:
                out.append({"id": cid, "name": sw.name, "why": "，".join(why)})
        return out

    def kept_roles(self, scope: set[int] | None = None) -> list[dict]:
        """Roles NOT in the cast that are on screen: they stay exactly as in the original.
        Shown by `plan`, so a "turn everyone into X" request that only swaps two leads is caught before any spend."""
        out = []
        for cid, c in self.chars.items():
            if cid in self.spec.cast:
                continue
            shots = sorted(i for i in c.get("shots", []) if scope is None or i in scope)
            if shots:
                out.append({"id": cid, "role": c.get("role", cid), "importance": c.get("importance", ""), "shots": shots})
        return out

    def pilot_shots(self, shots: list[dict], k: int = 3) -> list[int]:
        """Representative shots to try before the full run, one per failure-prone kind (longest first):
        a swapped role talking with a still camera (lip sync), a wide swapped crowd with nobody talking
        (original people growing back), a swapped role talking during a big camera move (re-framing, sync)."""
        story = {s["index"]: s for s in self.story["shots"]}
        must = lambda i: (story[i].get("key") or {}).get("priority") == "must"
        regen = sorted((p for p in shots if p["route"] == "regen" and p.get("mode") != "kenburns"),
                       key=lambda p: (not must(p["index"]), -story[p["index"]]["duration"]))
        large = lambda p: (story[p["index"]].get("analysis") or {}).get("change") == "large"
        talks = lambda p: self._talks(story[p["index"]], p)
        crowd = lambda p: any(self._is_crowd(c) for c in p["swapped"])
        contact = lambda p: bool(story[p["index"]].get("contacts"))
        kinds = [lambda p: talks(p) and not large(p),
                 lambda p: crowd(p) and not talks(p),
                 contact,
                 lambda p: talks(p) and large(p),
                 large]
        picked: list[int] = []
        for want in kinds:
            if len(picked) >= k:
                break
            hit = next((p["index"] for p in regen if want(p) and p["index"] not in picked
                        and story[p["index"]]["duration"] <= 15), None)
            if hit:
                picked.append(hit)
        for p in regen:  # fill up when some kind does not occur
            if len(picked) >= k:
                break
            if p["index"] not in picked:
                picked.append(p["index"])
        return sorted(picked[:k])

    # ---- stage 1: cast sheets ----
    def cast_sheet(self, cid: str, force: bool = False) -> Path:
        sw = self.spec.cast[cid]
        orig = self.chars[cid]
        costume = (f"穿原角色「{orig.get('role')}」的服装：{outfit(orig.get('appearance', ''))}"
                   if sw.costume == "keep" else sw.costume)
        refs: list[Path] = []
        if sw.image:
            img = Path(sw.image).expanduser()
            if not img.is_absolute() and self.spec.path:
                img = (self.spec.path.parent / img).resolve()
            refs = [img]
            prompt = SHEET_FROM_IMAGE_PROMPT.format(style=self._style_text(), extra=sw.look, costume=costume)
        else:
            tmpl = CROWD_SHEET_PROMPT if self._is_crowd(cid) else SHEET_PROMPT
            prompt = tmpl.format(style=self._style_text(), name=sw.name, look=sw.look, costume=costume)
        out = self.out / "cast" / f"{cid}.png"
        # keyed without the original-face note: adding it later must not redraw every existing cast (and its keyframes)
        key = cache.input_key(prompt=prompt, refs=refs, engine=self.image.describe())
        if not sw.image:  # a picture the user gave is their call
            prompt += ORIGINAL_FACE_NOTE
        if force or not (cache.fresh(out, key) or ("cast" in self.keep and out.is_file())):
            self.image.generate(prompt, out, aspect="16:9", refs=refs or None)
            cache.stamp(out, key, prompt=prompt, character=sw.name)
        self._likeness(cid, out)
        return out

    def _human(self, cid: str) -> bool:
        sw = self.spec.cast[cid]
        animal = ("猫", "狗", "犬", "动物", "机器人", "兔", "熊", "狐", "鸟", "猴", "龙", "妖怪", "怪物")
        return sw.kind != "object" and not self._is_crowd(cid) and not any(w in f"{sw.name}{sw.look}" for w in animal)

    def _likeness(self, cid: str, sheet: Path) -> None:
        """Does the new face look like a real star? A warning for the human reviewer (check.md), never a blocker;
        asked once per sheet (kept in its meta). Animals, products and crowds are skipped."""
        if not self._human(cid) or not sheet.is_file() or not cache.meta_path(sheet).is_file():
            return
        meta = json.loads(cache.meta_path(sheet).read_text(encoding="utf-8"))
        if "likeness" in meta:
            return
        try:
            r = self.vlm.ask_json(LIKENESS_PROMPT.format(name=self.spec.cast[cid].name), [sheet], max_tokens=512, timeout=90)
        except Exception as exc:  # unchecked: tried again next run
            LOG.warning("设定图 %s 像真人检查失败：%s", sheet.name, exc)
            return
        meta["likeness"] = {"risk": bool(r.get("risk")), "reason": str(r.get("reason") or "")}
        if meta["likeness"]["risk"]:
            LOG.warning("设定图 %s 可能长得像真实的明星/公众人物：%s（过审风险，改 look 后用 --force cast 重画）",
                        sheet.name, meta["likeness"]["reason"])
        key = meta.pop("key", None)
        cache.stamp(sheet, key, **meta)

    def build_cast(self, force: bool = False) -> dict[str, Path]:
        with ThreadPoolExecutor(max(1, len(self.spec.cast))) as ex:
            futs = {cid: ex.submit(self.cast_sheet, cid, force) for cid in self.spec.cast}
            sheets = {cid: f.result() for cid, f in futs.items()}
        todo = self._expr_cids()
        if todo:
            with ThreadPoolExecutor(len(todo)) as ex:
                futs = {cid: ex.submit(self.expression_sheet, cid, sheets[cid], force) for cid in todo}
                self.expr = {cid: f.result() for cid, f in futs.items()}
        return sheets

    def _expr_cids(self) -> list[str]:
        """Swapped characters that get an expression sheet: opt-in, and only those the breakdown listed moods for."""
        if not self.spec.expression_sheet:
            return []
        return [cid for cid, sw in self.spec.cast.items()
                if sw.kind != "object" and not self._is_crowd(cid) and self.chars.get(cid, {}).get("expressions")]

    def expression_sheet(self, cid: str, sheet: Path, force: bool = False) -> Path:
        """The episode's expressions (breakdown) drawn on the new character's face, from its cast sheet."""
        sw, ex = self.spec.cast[cid], self.chars[cid]["expressions"]
        items = "\n".join(f"{k}. {x['name']}：{x.get('look', '')}" for k, x in enumerate(ex, 1))
        prompt = EXPRESSION_SHEET_PROMPT.format(style=self._style_text(), name=sw.name, n=len(ex), items=items)
        out = self.out / "cast" / f"{cid}_expr.png"
        key = cache.input_key(prompt=prompt, refs=[sheet], engine=self.image.describe())
        if force or not (cache.fresh(out, key) or ("cast" in self.keep and out.is_file())):
            self.image.generate(prompt, out, aspect="16:9", refs=[sheet])
            cache.stamp(out, key, prompt=prompt, character=sw.name, expressions=[x["name"] for x in ex])
        return out

    def _expression(self, cid: str, shot: dict) -> str:
        """Which of the character's listed expressions this shot plays ("" when none)."""
        return next((x["name"] for x in self.chars.get(cid, {}).get("expressions") or [] if shot["index"] in x.get("shots", [])), "")

    def _is_crowd(self, cid: str) -> bool:
        c = self.chars.get(cid, {})
        return "crowd" in cid or any(k in str(c.get("role", "")) for k in ("群众", "人群", "宾客", "路人"))

    # ---- stage 2: keyframes ----
    def _look(self, cid: str, shot: dict) -> str:
        """The original look of a character in this shot: a merged id (disguise, costume change) keeps its own outfit."""
        c = self.chars.get(cid, {})
        for o in c.get("outfits") or []:
            if shot["index"] in o.get("shots", []):
                return o.get("appearance", "")
        return c.get("appearance", "")

    def _dress(self, cid: str, shot: dict) -> str:
        """What a character wears and carries in this shot: the scene breakdown (outfit, wig, face mask, props, stains,
        changes inside the scene) first; else the clothing clauses of the per-shot look."""
        return look_in(self.story, cid, shot["index"], dress=True) or outfit(self._look(cid, shot))

    def _keep_text(self, shot: dict) -> str:
        """The beat this shot exists for: what it must achieve, then the concrete things that achieve it.
        With a new setting the props may change, the purpose may not."""
        k = shot.get("key") or {}
        keep = "；".join(k.get("keep") or [])
        if keep and self.spec.style:
            keep += "（原片里的道具和场景按改编换成对应的新东西）"
        fn = (k.get("function") or "").strip().rstrip("。")
        return "；".join(x for x in (f"这一镜的作用是{fn}" if fn else "", keep) if x)

    def _swap_lines(self, shot: dict, swaps: list[str], first_ref: int) -> str:
        where = {c.get("id"): c for c in (shot.get("analysis") or {}).get("characters") or []}
        lines = []
        for k, cid in enumerate(swaps):
            sw, orig = self.spec.cast[cid], self.chars[cid]
            pos = where.get(cid) or {}
            place = "，".join(x for x in (pos.get("where"), pos.get("expression")) if x)
            if self._is_crowd(cid):
                lines.append(f"- 把图1中所有的{orig.get('role')}（{orig.get('appearance')}）换成图{first_ref + k}这类造型：{sw.name}。{sw.look}"
                             "每个人造型各不相同；人数、分布、站位、动作、远近大小和图1完全一致，远处的小人只改服装轮廓和颜色。")
                continue
            # how the original person looks in *this* shot (scene breakdown), not a summary of all their outfits
            look = look_in(self.story, cid, shot["index"]) or self._look(cid, shot)
            dress = (f"衣服、配饰和道具必须和图1里这个人身上的一样（{self._dress(cid, shot)}），不要换成别的服装"
                     if sw.costume == "keep" else f"服装：{sw.costume}")
            lines.append(f"- 把图1中的{orig.get('role')}（原外貌：{look}"
                         f"{'；在画面中：' + place if place else ''}）替换为图{first_ref + k}中的角色「{sw.name}」。{sw.look}"
                         f"{dress}。")
        lines.append("- 每个被替换的角色必须留在图1中原来的位置，保持原来的大小、朝向和遮挡关系（远处的小人仍然是远处的小人，"
                     "背对镜头的仍然背对）；不要交换角色位置，不要新增或放大人物；图1里看不到的角色不要添加。")
        return "\n".join(lines)

    def _frame_prompt(self, shot: dict, swaps: list[str], first_ref: int) -> str:
        a = shot.get("analysis") or {}
        emotion = a.get("emotion") or "与原画面一致"
        swap_text = self._swap_lines(shot, swaps, first_ref)
        if self._keep_text(shot):  # the beat this shot exists for (from the breakdown) must survive the edit
            swap_text += f"\n- 这一镜的看点，必须保住：{self._keep_text(shot)}。"
        if self.spec.style:
            return FRAME_RESTYLE_PROMPT.format(style=self.spec.style, emotion=emotion, swaps=swap_text)
        keeps_costume = all(self.spec.cast[c].costume == "keep" for c in swaps)
        costume_rule = "，服装保持原角色的服装" if keeps_costume else "，服装换成设定图里的服装"
        return FRAME_SWAP_PROMPT.format(swaps=swap_text, emotion=emotion, costume_rule=costume_rule,
                                        style_rule=f"- 画风：{self._style_text()}；替换后的角色要和画面光影、质感融为一体。")

    @staticmethod
    def _texts_in(shot: dict, name: str, kinds: tuple) -> list[dict]:
        """Screen text (breakdown) seen in one keyframe; anchors inside the shot go by the middle frame."""
        at = name if name in ("first", "last") else "mid"
        return [t for t in shot.get("screen_text") or [] if t.get("kind") in kinds and at in t.get("frames", [])]

    def _text_lines(self, shot: dict, name: str) -> str:
        """Scene text stays legible (or follows the new setting); 花字 is left out, assembly redraws it."""
        out = []
        keep = self._texts_in(shot, name, ("scene", "unknown"))
        if keep:
            what = "、".join(f"「{t['text']}」" for t in keep)
            out.append(f"\n- 场景里的文字{what}按改编换成对应的新内容（招牌、屏幕上的字随新场景改），字形清楚、不要乱码" if self.spec.style
                       else f"\n- 场景里真实存在的文字{what}原样保留：同样的字、同样的位置，笔画清楚，不要写错或变成乱码")
        bubbles = self._texts_in(shot, name, ("bubble",))
        if bubbles:
            what = "、".join("「" + t["text"] + "」" for t in bubbles)
            out.append(f"\n- 对话气泡和里面的字{what}原样保留：气泡的形状、位置、大小和字都和图1一样，笔画清楚")
        drop = self._texts_in(shot, name, ("overlay", "watermark"))
        if drop:
            what = "、".join("「" + t["text"] + "」" for t in drop)
            out.append(f"\n- 后期叠加的花字/水印{what}不要画，那块画面按周围补全")
        return "".join(out)

    def _effects(self, shot: dict) -> list[str]:
        """Effects of the shot (breakdown staging) with their colour; a swapped role's `fx_color` replaces its own."""
        out = []
        for e in (shot.get("staging") or {}).get("effects") or []:
            sw = self.spec.cast.get(e.get("from"))
            color = (f"颜色换成{sw.fx_color}（原片是{e.get('color') or '别的颜色'}）" if sw and sw.fx_color
                     else e.get("color") or "")
            src = f"从{self._who(e['from'])}身上发出" if e.get("from") in self.chars else ""
            out.append(e["what"] + (f"（{'，'.join(x for x in (color, src) if x)}）" if color or src else ""))
        return out

    def _drawn_lines(self, shot: dict) -> str:
        """Comic-drama elements drawn over the scene (effects, manga marks, split panels): the edit must keep them."""
        st = shot.get("staging") or {}
        out = ""
        if self._effects(shot):
            out += "\n- 画面里的特效保留，形状、位置、大小、亮度和图1一样：" + "；".join(self._effects(shot))
        if st.get("marks"):
            out += f"\n- 漫画符号（{'、'.join(st['marks'])}）照图1的位置画出来，画法和画风一致"
        if st.get("panels"):
            out += f"\n- 画面是 {st['panels']} 宫格分屏：格子边框、每格的位置和大小和图1完全一样，每一格里分别替换"
        return out

    def _drawn_motion(self, shot: dict) -> str:
        from rea_toon.breakdown import _where
        st = shot.get("staging") or {}
        out = []
        if self._effects(shot):
            out.append("特效全程保持形状和颜色：" + "；".join(self._effects(shot)))
        if st.get("marks"):
            out.append(f"漫画符号（{'、'.join(st['marks'])}）留在原位置")
        if st.get("panels"):
            out.append(f"画面是 {st['panels']} 宫格分屏：分格边框始终不动，每一格里的画面各自动，不要合成一个画面、不要切换构图")
        mo = shot.get("motion") or {}
        if mo.get("kind") == "partial":
            out.append(f"原片这一镜画面基本不动，只有画面{_where(mo.get('where') or [0, 0, 1, 1])}那一小块在动；"
                       "镜头和其余部分保持静止，不要加额外的动作")
        elif mo.get("kind") in ("still", "pan"):  # kenburns off: at least keep the picture still
            out.append("原片这一镜是一张静止的画面（只有镜头推拉），人物和背景都不要动")
        return "".join(f"{x}。" for x in out)

    def _missing_text(self, shot: dict, name: str, out: Path) -> list[str]:
        """Scene text big enough to read in the original that OCR no longer finds in the edited frame (the edit
        garbled or dropped it). Restyled remakes rewrite the text on purpose, so they are not checked."""
        if self.spec.style:
            return []
        want = [t for t in self._texts_in(shot, name, ("scene", "bubble")) if t["box"][3] - t["box"][1] >= 0.025]
        if not want:
            return []
        from rea_toon.subtitles import read_text, similar_text
        try:
            got = [r["text"] for r in read_text(out, 0.5)]
        except Exception as exc:  # OCR missing: no text check
            LOG.warning("关键帧文字检查失败 %s：%s", out.name, exc)
            return []
        return [t["text"] for t in want if not any(similar_text(t["text"], g) >= 0.6 or t["text"] in g for g in got)]

    def _clean_frame(self, src: Path, out: Path, shot: dict, t: float) -> Path:
        """No role to swap in this frame: just inpaint burned subtitles away (cheap, local)."""
        import cv2
        import numpy as np
        img = cv2.imread(str(src))
        subs = [s for s in shot.get("subtitles", []) if s["start"] - 0.1 <= t <= s["end"] + 0.1]
        if subs:
            h, w = img.shape[:2]
            mask = np.zeros((h, w), np.uint8)
            for s in subs:
                x0, y0, x1, y1 = s["box"]
                cv2.rectangle(mask, (int(x0 * w) - 6, int(y0 * h) - 6), (int(x1 * w) + 6, int(y1 * h) + 6), 255, -1)
            img = cv2.inpaint(img, mask, 7, cv2.INPAINT_TELEA)
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), img)
        return out

    def keyframes(self, shot: dict, plan: dict, sheets: dict[str, Path], force: bool = False) -> dict[str, Path]:
        result: dict[str, Path] = {}
        anchors = plan.get("anchors", {})
        for name in ("first", "last", *anchors):
            how = plan.get("frames", {}).get(name)
            if not how:
                continue
            if name in anchors:
                from .media import frame_at
                t = shot["start"] + anchors[name]
                src = self.work / "frames" / f"{shot['id']}_{name}_{anchors[name]:.2f}.jpg"
                if not src.is_file():
                    frame_at(self.work / "source.mp4", t, src)
            else:
                src = self.work / shot["frames"][name]
                t = shot["start"] + 0.04 if name == "first" else shot["end"] - 0.07
            out = self.out / "frames" / f"{shot['id']}_{name}.png"
            if how == "clean":
                key = cache.input_key(src=src, how="clean", subs=shot.get("subtitles"))
                if force or not cache.fresh(out, key):
                    self._clean_frame(src, out, shot, t)
                    cache.stamp(out, key, how="clean")
                result[name] = out
                continue
            swaps = plan["frame_swaps"][name]
            refs = [src] + [sheets[c] for c in swaps]
            prompt = self._frame_prompt(shot, swaps, first_ref=2) + self._text_lines(shot, name) + self._drawn_lines(shot)
            if name != "first" and "first" in result and plan["frames"].get("first") == "edit":
                refs.append(result["first"])
                prompt += LAST_FRAME_HINT.format(n=len(refs))
            if name == "first" and plan.get("scene_ref"):
                hint = self._scene_ref(shot, plan["scene_ref"])
                if hint:
                    refs.append(hint[0])
                    prompt += hint[1].replace("{n}", str(len(refs)))
            for c in swaps:
                expr = self._expression(c, shot)
                if expr and c in self.expr and self.expr[c].is_file():
                    refs.append(self.expr[c])
                    prompt += EXPRESSION_REF_HINT.format(n=len(refs), name=self.spec.cast[c].name, expr=expr)
            note = self.spec.frame_notes.get(shot["index"], {}).get(name)
            if note:  # e.g. an outfit change inside the take: keep the face from the first frame, not its clothes
                prompt = prompt.replace("长相、服装、画风必须与", "长相和画风必须与")
                prompt += f"\n- 这一帧的特别要求（和上面的服装/外貌描述冲突时以这条为准）：{note}"
            key = cache.input_key(prompt=prompt, refs=refs, engine=self.image.describe())
            kept = (not force and "frames" in self.keep and out.is_file() and cache.meta_path(out).is_file())
            if force or not (cache.fresh(out, key) or kept):
                self.image.generate(prompt, out, aspect=self.story["aspect"], refs=refs)
                cache.stamp(out, key, prompt=prompt, refs=[str(r) for r in refs])
            if not kept:
                self._guard_frame(shot, name, src, out, key, prompt, refs, swaps, t)
            result[name] = out
        return result

    def _places(self, shot: dict, swaps: list[str]) -> str:
        """Who must end up where: with two swapped people in one frame the edit can give A's new look to B."""
        where = {c.get("id"): c.get("where") for c in (shot.get("analysis") or {}).get("characters") or []}
        people = [c for c in swaps if not self._is_crowd(c) and self.spec.cast[c].kind != "object"]
        rows = [f"「{self.spec.cast[c].name}」必须出现在图1里{self.chars[c].get('role')}的位置（{where[c]}）"
                for c in people if where.get(c)]
        if len(people) < 2 or not rows:
            return ""
        return ("\n   这一帧有多个被替换的人，逐个核对没有张冠李戴：" + "；".join(rows)
                + "。新形象画到了别人身上（左右对调、主次对调）就是 ok=false，problems 里写清谁该在哪。")

    def _scene_ref(self, shot: dict, ref: dict) -> tuple[Path, str] | None:
        """The scene anchor's approved first frame and how to use it; None when it has not been made (e.g. a
        `--shots` run without the anchor): the frame is then made from the cast sheets alone."""
        anchor = next((s for s in self.story["shots"] if s["index"] == ref["shot"]), None)
        path = self.out / "frames" / f"{anchor['id']}_first.png" if anchor else None
        if not path or not path.is_file():
            LOG.info("镜头 %s：同场参考镜 %s 的首帧还没有，只用设定图", shot["index"], ref["shot"])
            return None
        names = "、".join(f"「{self.spec.cast[c].name}」" for c in ref["who"])
        # an outfit change between the two shots (wig off, disguise, a per-frame note): copy the face, not the clothes
        same = (not self.spec.frame_notes.get(shot["index"], {}).get("first")
                and all(self._dress(c, shot) == self._dress(c, anchor) for c in ref["who"]))
        dress = "、服装和配饰" if same else "（服装按上面这一镜的描述来，不要照抄图{n}的衣服）"
        return path, SCENE_REF_HINT.replace("{dress}", dress).replace("{anchor}", str(ref["shot"])).replace("{names}", names)

    def _check_frame(self, src: Path, out: Path, swaps: list[str], note: str = "", shot: dict | None = None,
                     name: str = "first") -> dict:
        check = self._check_frame_vlm(src, out, swaps, note, shot)
        lost = self._missing_text(shot, name, out) if shot and not check.get("error") else []
        if lost:
            check = {**check, "ok": False, "tiny": False, "lost_text": lost,
                     "problems": [*(check.get("problems") or []),
                                  f"图1 场景里的文字{'、'.join(f'「{x}」' for x in lost)}在图2 里没了或写错了，要原样保留、笔画清楚"]}
        return check

    def _check_frame_vlm(self, src: Path, out: Path, swaps: list[str], note: str = "", shot: dict | None = None) -> dict:
        who = "、".join(f"{self.chars[c].get('role')}（换成{self.spec.cast[c].name}）" for c in swaps)
        prompt = FRAME_CHECK_PROMPT.format(who=who, places=self._places(shot, swaps) if shot else "")
        # changes the remake asks for (new setting / art style, per-frame notes) are not problems
        wanted = "；".join(x for x in (f"整体换成：{self.spec.style}" if self.spec.style else "", note) if x)
        if wanted:
            prompt += f"\n以下是这次复刻要求的改动，图2 按这些改了是正确的，不算问题：{wanted}"
        try:
            return self.vlm.ask_json(prompt, [src, out], max_tokens=2048)
        except Exception as exc:  # the check is a safety net, never a blocker
            LOG.warning("关键帧质检失败 %s：%s", out.name, exc)
            return {"ok": True, "error": str(exc)}

    def _guard_frame(self, shot, name, src, out, key, prompt, refs, swaps, t) -> None:
        """Compare the edited frame with the original; redo once with the problems spelled out.
        If the swapped roles are too small to recognise anyway, keep the original frame (no swap)."""
        meta = json.loads(cache.meta_path(out).read_text(encoding="utf-8"))
        if "check" in meta:
            return
        note = self.spec.frame_notes.get(shot["index"], {}).get(name, "")
        check = self._check_frame(src, out, swaps, note, shot, name)
        if check.get("error"):  # VLM unavailable: leave unchecked so the next run checks it
            return
        # a tiny person may keep the original frame, but not a product, and not when the whole scene is restyled
        tiny_ok = not self.spec.style and all(self.spec.cast[c].kind != "object" for c in swaps)
        if not check.get("ok") and check.get("tiny") and tiny_ok:
            self._clean_frame(src, out, shot, t)
            LOG.info("镜头 %s %s 帧：被换角色太小看不清，保留原帧", shot["index"], name)
        elif not check.get("ok"):
            problems = "；".join(check.get("problems") or [])
            LOG.info("镜头 %s %s 帧质检不通过，重画：%s", shot["index"], name, problems[:200])
            self.image.generate(prompt + f"\n- 上一版的问题（必须修正）：{problems}", out, aspect=self.story["aspect"], refs=refs)
            check = {**self._check_frame(src, out, swaps, note, shot, name), "retried": problems}
            if not check.get("ok"):
                LOG.warning("镜头 %s %s 帧重画后仍未通过质检：%s", shot["index"], name, check.get("problems"))
        meta.pop("key", None)
        cache.stamp(out, key, **{**meta, "check": check})

    # ---- stage 3: clips ----
    def performance(self, shot: dict) -> dict:
        """Time-coded acting beats of the original shot, from the analysis (the remake never analyses: a missing
        one is a breakdown lint error, fixed before lock)."""
        perf = shot.get("performance") or {}
        return {} if perf.get("error") else perf

    def _who(self, cid: str) -> str:
        role = self.chars.get(cid, {}).get("role", cid)
        return f"{role}（{self.spec.cast[cid].name}）" if cid in self.spec.cast else role

    @staticmethod
    def _wide(shot: dict) -> bool:
        """Big camera move or tiny figures: the camera leads, face details would pull the model into a close-up."""
        a = shot.get("analysis") or {}
        size = str(a.get("shot_size", ""))
        if a.get("lip_visible"):  # someone talks on camera (e.g. talks, then the camera pulls out): keep the face
            return False
        return a.get("change") == "large" or any(k in size for k in ("远景", "航拍", "全景", "大全"))

    @staticmethod
    def _attributed(shot: dict) -> bool:
        """Lines carry their own speaker (analyze / refine): then they, not the shot's single guess, decide mouths."""
        return any("speaker" in x for x in shot.get("subtitles") or [])

    @classmethod
    def _lines(cls, shot: dict) -> list[dict]:
        """Spoken lines in shot time: who says each and whether that mouth is on camera. Name cards are not lines."""
        sp = (shot.get("analysis") or {}).get("speaker")
        out = []
        for x in shot.get("subtitles") or []:
            if x.get("card") or x.get("speaker") == "title":
                continue
            mode = line_mode(x)
            out.append({"t0": max(0.0, x["start"] - shot["start"]), "t1": min(shot["duration"], x["end"] - shot["start"]),
                        "who": x.get("speaker", sp), "on": mode == "on", "mode": mode, "text": x["text"]})
        return out

    @classmethod
    def _speaker(cls, shot: dict) -> str | None:
        """Main on-camera speaker of the shot (most talking time), None if every line is off camera."""
        if cls._attributed(shot):
            talk: dict[str, float] = {}
            for l in cls._lines(shot):
                if l["on"] and l["who"]:
                    talk[l["who"]] = talk.get(l["who"], 0) + l["t1"] - l["t0"]
            return max(talk, key=talk.get) if talk else None
        sp = (shot.get("analysis") or {}).get("speaker")
        return None if str(sp).strip().lower() in ("", "none", "null", "offscreen") else sp

    def _offscreen(self, shot: dict) -> list[tuple[float, float]]:
        """Spans of lines whose speaker is off camera (or a name card): muted in the lipsync voice."""
        on = [(l["t0"], l["t1"]) for l in self._lines(shot) if l["on"]]
        off = [(l["t0"], l["t1"]) for l in self._lines(shot) if not l["on"]] if self._attributed(shot) else []
        # overlapping subtitle timings: never mute a moment an on-screen line covers
        out = []
        for a, b in off:
            pieces = [(a, b)]
            for c, d in on:
                pieces = [p for x, y in pieces for p in ((x, min(y, c)), (max(x, d), y)) if p[1] - p[0] > 0.02]
            out += pieces
        return out

    def _voice_cut(self, shot: dict, out: Path, seconds: float) -> Path:
        from .media import cut_audio
        return cut_audio(self.work / self.story["audio"]["vocals"], shot["start"], shot["duration"], out,
                         pad_to=seconds, mute=self._offscreen(shot))

    def _speaking_text(self, shot: dict, audio: bool) -> str:
        """Who talks when, line by line; everyone else keeps the mouth shut. Spelled-out words make audio-driven
        workflows burn them in as subtitles, so with audio only the spans are given."""
        lines = self._lines(shot)
        if not lines:
            return ""
        parts = []
        for l in lines:
            span = f"{l['t0']:.1f}–{l['t1']:.1f}s"
            words = "" if audio else f"「{l['text']}」"
            if l["on"]:
                parts.append(f"{span} {self._who(l['who'])}说话{words}")
            elif l["mode"] == "inner":
                parts.append(f"{span} 是{self._who(l['who'])}的内心独白{words}：{self._who(l['who'])}嘴巴闭着不说话，"
                             "只用眼神和表情演出心里的想法，其他角色也不张嘴")
            elif l["mode"] == "narration":
                parts.append(f"{span} 是旁白{words}，画面里的角色都不张嘴")
            else:
                parts.append(f"{span} 是画外的{self._who(l['who'])}在说话{words}，画面里的角色都不张嘴，只做倾听反应")
        tail = "口型严格跟随音频；台词只用嘴说，画面上不要出现任何文字。" if audio else "说话时嘴明显张合。"
        return ("台词和口型（按时间，严格照做）：" + "；".join(parts) + "。同一时间只有正在说话的那个角色张嘴，"
                "其他角色嘴巴闭着；没有台词的时间所有人都闭嘴。" + tail)

    def _performance_text(self, shot: dict) -> str:
        perf = self.performance(shot)
        if not perf or perf.get("error"):
            return ""
        wide = self._wide(shot)
        lines = []
        if wide and perf.get("camera"):
            lines.append(f"运镜（最重要，从第一帧就开始，节奏和原片一致）：{perf['camera']}"
                         + "。全程不要推近、不要切成人物特写，人物在画面中的大小随运镜变化，和原片一致。")
        lines.append("表演（按时间，严格照做）：")
        for b in perf.get("beats") or []:
            # per-line speakers decide the mouths; the beats' mouth guesses (from stills) would contradict them
            mouth = None if self._attributed(shot) else b.get("mouth")
            fields = (b.get("body"),) if wide else (b.get("face"), mouth, b.get("body"))
            parts = "；".join(x.strip().rstrip("。；;") for x in fields if x)
            if not parts:
                continue
            lines.append(f"- {b.get('t0', 0):.1f}–{b.get('t1', 0):.1f}s {self._who(b.get('who', ''))}：{parts}")
        avoid = [x for x in perf.get("avoid") or [] if x]
        if avoid:
            lines.append("禁止：" + "；".join(x.strip().rstrip("。；;") for x in avoid))
        return "\n".join(lines) + "\n"

    def _drift(self, shot: dict, plan: dict, frames: dict, clip: Path, qa_dir: Path) -> dict:
        """Composition / brightness likeness of a clip. Restyled remakes compare with their own keyframes:
        a new setting makes every clip 'drift' from the source by design."""
        from .media import clip_drift, keyframe_drift
        if not self.spec.style:
            return clip_drift(self.work / "source.mp4", shot["start"], clip, shot["duration"], qa_dir)
        anchors = plan.get("anchors", {})
        keys = [(0.05 if n == "first" else max(0.05, shot["duration"] - 0.1) if n == "last" else anchors[n], p)
                for n, p in frames.items() if n in ("first", "last") or n in anchors]
        return keyframe_drift(clip, keys, qa_dir)

    def _restyled_motion(self, shot: dict, motion: str) -> str:
        """The shot's action description still names the original things (banana, forest...). With a new setting,
        rewrite it from the new keyframes so the video prompt only mentions what is in this shot now."""
        frames = [self.out / "frames" / f"{shot['id']}_{n}.png" for n in ("first", "last")]
        frames = [f for f in frames if f.is_file()]
        if not frames or not motion:
            return motion
        key = cache.input_key(motion=motion, style=self.spec.style, frames=frames)
        p = self.out / "motion" / f"{shot['id']}.json"
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("key") == key:
                return d["motion"]
        try:
            res = self.vlm.ask_json(RESTYLE_MOTION_PROMPT.format(motion=motion, style=self.spec.style), frames, max_tokens=2048)
            new = (res.get("motion") or "").strip() or motion
        except Exception as exc:  # keep the original description rather than fail the shot
            LOG.warning("镜头 %s 动作描述改写失败：%s", shot["index"], exc)
            return motion
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"key": key, "motion": new, "original": motion}, ensure_ascii=False, indent=1), encoding="utf-8")
        return new

    def _video_prompt(self, shot: dict, plan: dict) -> str:
        a = shot.get("analysis") or {}
        perf = self.performance(shot)
        perf = {} if perf.get("error") else perf
        # the performance pass watches more frames than the shot pass, so its description wins
        motion = perf.get("motion") or a.get("motion_prompt") or a.get("action_summary") or ""
        emotion = (perf.get("overall") or a.get("emotion", "")).rstrip("。")
        if self.spec.style:
            motion = self._restyled_motion(shot, motion)
        speaking = ""
        if self._attributed(shot):
            speaking = self._speaking_text(shot, audio=plan.get("mode") in ("lipsync", "ref_video"))
        elif self._speaker(shot) and not self._wide(shot):
            who = self._who(self._speaker(shot))
            subs = shot.get("subtitles", [])
            timed = "，".join(f"{max(0, x['start'] - shot['start']):.1f}s 起说「{x['text']}」" for x in subs)
            if plan.get("mode") in ("lipsync", "ref_video"):
                # the audio already carries the words; spelling them out makes the model burn them in as subtitles
                spans = "、".join(f"{max(0, x['start'] - shot['start']):.1f}–{min(shot['duration'], x['end'] - shot['start']):.1f}s"
                                  for x in subs)
                speaking = (f"{who}在 {spans} 说话，口型严格跟随音频；台词只用嘴说，画面上不要出现任何文字；"
                            "其他时间嘴保持自然闭合或按表演描述。") if spans else ""
            else:
                speaking = f"{who}在说话（{timed}），说话时嘴明显张合。" if timed else f"{who}在说话。"
        cast_note = "".join(f"画面中的{self.chars[c].get('role')}是{self.spec.cast[c].name}"
                            + (f"（这一镜的造型：{self._dress(c, shot)}）" if not self._is_crowd(c) else "")
                            + "。" for c in plan["swapped"])
        if self._keep_text(shot):
            cast_note += f"这一镜的看点，必须演出来：{self._keep_text(shot)}。"
        cast_note += self._staging_text(shot) + self._contact_text(shot) + self._drawn_motion(shot)
        # the whole old->new mapping names scenes of other shots too (and the old things): the keyframes carry the look
        style = "画面的场景、道具和画风和首帧完全一致。" if self.spec.style else ""
        base = VIDEO_PROMPT.format(motion=motion, shot_size=a.get("shot_size", ""), camera_move=a.get("camera_move", ""),
                                   emotion=emotion, performance=self._performance_text(shot),
                                   speaking=cast_note + style + speaking).strip()
        if plan.get("mode") != "ref_video":
            return base
        frames = plan.get("frames", {})
        pics, k = [], 1
        for name, label in (("first", "first frame"), ("last", "last frame")):
            if name in frames:
                pics.append(f"<Picture {k}> is the new {label} of this shot")
                k += 1
        for name, t in plan.get("anchors", {}).items():
            pics.append(f"<Picture {k}> is the new frame at {t:.1f}s of this shot")
            k += 1
        lines = ["; ".join(pics) + ": the characters' faces, hair, costumes and the look of the image must match it exactly."] if pics else []
        if self.spec.style:  # new setting / art style: <Video 1> keeps only the layout and motion, never its look
            lines.append("The setting, props, materials, colors and art style come ONLY from the new pictures; "
                         "<Video 1> is the old version of the scene, never copy its look.")
        replace = []
        for c in plan["swapped"]:
            lines.append(f"<Picture {k}> is the character sheet of 「{self.spec.cast[c].name}」.")
            replace.append(f"Replace the {self.chars[c].get('role')} in <Video 1> completely with 「{self.spec.cast[c].name}」 "
                           f"from <Picture {k}> (same face, hair and features as <Picture {k}> and the first frame).")
            k += 1
        if self._attributed(shot):
            said = {"on": "「{w}」 speaks",
                    "inner": "inner monologue of 「{w}」 (thoughts only: 「{w}」 keeps the mouth closed and acts with the eyes and face)",
                    "narration": "narrator voice-over (nobody on screen moves the mouth)",
                    "off": "off-screen voice of 「{w}」 (nobody on screen moves the mouth)"}
            spans = [f"{l['t0']:.1f}-{l['t1']:.1f}s " + said[l["mode"]].format(w=self._who(l["who"]))
                     for l in self._lines(shot)]
            speech = ("; by time: " + "; ".join(spans) + ". Only the character speaking at that moment opens the mouth, "
                      "everyone else keeps it closed") if spans else ""
        else:
            sp = self._speaker(shot)
            speech = (f"; {self._who(sp)} speaks these lines, so his/her mouth must open and close exactly with it"
                      if sp and shot.get("subtitles") else "")
        return REF_VIDEO_PROMPT.format(pictures="\n".join(lines), replace=" ".join(replace), speech=speech, base=base).strip()

    def clip(self, shot: dict, plan: dict, frames: dict[str, Path], sheets: dict[str, Path], force: bool = False) -> Path:
        from .media import cut_audio
        out = self.out / "clips" / f"{shot['id']}.mp4"
        if plan["mode"] == "kenburns":
            return self._still_clip(shot, plan, frames, out, force)
        prompt = self._video_prompt(shot, plan)
        mode = plan["mode"]
        seconds = self.video.clamp(shot["duration"])
        audio = None
        if mode == "lipsync":
            # the voice of exactly this shot, padded to the generated length so the mouth stays on the words,
            # with off-screen lines muted (the visible mouth would follow them too)
            audio = self._voice_cut(shot, self.out / "audio" / f"{shot['id']}_vocals.wav", seconds)
        refs = [sheets[c] for c in plan["swapped"]] if mode == "refs" else []
        ref_video = None
        if mode == "ref_video":
            ref_video, refs = self._ref_inputs(shot, plan, frames)
        key = cache.input_key(prompt=prompt, mode=mode, seconds=seconds, engine=self.video.describe(),
                              first=frames.get("first"), last=frames.get("last"), refs=refs, audio=audio, video=ref_video)
        # keep clips: a clip made from the same keyframes is re-checked by the current guard instead of being
        # regenerated just because the prompt text changed; only the ones that fail get new candidates
        reuse = (not force and "clips" in self.keep and out.is_file() and cache.meta_path(out).is_file()
                 and all(Path(p).stat().st_mtime <= out.stat().st_mtime for p in frames.values() if p))
        if reuse and not cache.fresh(out, key):
            LOG.info("镜头 %s 沿用已有视频，按当前检查规则重新检查", shot["index"])
        old = json.loads(cache.meta_path(out).read_text(encoding="utf-8")) if cache.meta_path(out).is_file() else {}
        # a forced redo of a clip that was already generated starts a new seed round: the same seed would just
        # reproduce the clip that was rejected (H3 is deterministic for the same inputs and seed)
        rnd = old.get("seed_round", 0) + 1 if (force and out.is_file()) else old.get("seed_round", 0)
        if not reuse and (force or not cache.fresh(out, key)):
            eng = self._seeded(rnd)
            eng.generate(prompt, out, seconds, self.story["aspect"], first=frames.get("first"),
                                last=frames.get("last"), refs=refs, audio=audio, video=ref_video,
                                mode="first" if mode == "refs" else mode)
            cache.stamp(out, key, prompt=prompt, mode=mode, seconds=seconds, engine=self.video.describe(), seed_round=rnd)
        if plan["swapped"] or self.spec.style:
            self._guard(shot, plan, frames, out, key, prompt, refs=refs, audio=audio, video=ref_video)
        return out

    def _still_clip(self, shot: dict, plan: dict, frames: dict[str, Path], out: Path, force: bool) -> Path:
        """A held / pushed / panned still picture: the new keyframe moved along the original's window path (local)."""
        from .media import kenburns
        kb = plan["kenburns"]
        img = frames[kb["base"]]
        size, fps = (self.story["width"], self.story["height"]), self.story["fps"]
        key = cache.input_key(mode="kenburns", image=img, path=kb["path"], size=list(size), fps=fps, seconds=shot["duration"])
        if force or not cache.fresh(out, key):
            kenburns(img, kb["path"], size, fps, shot["duration"], out)
            cache.stamp(out, key, mode="kenburns", seconds=shot["duration"], base=kb["base"])
        return out

    SEED_ROUND = 100_000  # seed offset per forced redo; the guard's retries use offsets below this

    def _seeded(self, rnd: int, off: int = 0):
        import copy
        eng = copy.copy(self.video)
        if hasattr(eng, "seed"):
            eng.seed += rnd * self.SEED_ROUND + off
        return eng

    def _ref_inputs(self, shot: dict, plan: dict, frames: dict) -> tuple[Path, list[Path]]:
        """Ref2VA inputs: the original shot (subtitles and lead faces blurred, padded to 2 s) + anchors + cast sheets."""
        from .engines.video import H3Local
        from .media import ref_clip
        ref_video = self.out / "refclips" / f"{shot['id']}.mp4"
        ref_clip(self.work / "source.mp4", self.work / self.story["audio"]["vocals"], shot["start"], shot["duration"],
                 shot.get("subtitles", []), ref_video, size=H3Local.RES.get(self.story["aspect"], (1024, 576)),
                 mute=self._offscreen(shot))
        sheets = {c: self.out / "cast" / f"{c}.png" for c in plan["swapped"]}
        refs = [frames[n] for n in plan.get("anchors", {}) if n in frames] + [p for p in sheets.values() if p.is_file()]
        return ref_video, refs

    SYNC_TRIES = 2     # seeds tried per audio-driven mode before keeping the best one (must shots get one more)
    # ...but only while the score is near the bar: of 13 retries in the cat/dog remake only one that
    # started at >=0.5 got over 0.6; the ones starting at 0.2-0.4 stayed there (~5 GPU minutes each).
    # Logs of all runs so far (194 clips): a 2nd seed of the same mode rescued 2 of 19, a 3rd seed 1 of 11;
    # the next mode down the ladder rescues far more often (ViMax found the same: best-of-2 is the plateau).
    GUARD = 2          # bump when the acceptance rules change: clips checked by an older guard are re-checked

    def _talks(self, shot: dict, plan: dict) -> bool:
        """A swapped role speaks on camera, so the clip's mouths are measured against the original voice."""
        lines = self._lines(shot)
        if self._attributed(shot):
            return any(l["on"] and l["who"] in plan["swapped"] for l in lines)
        a = shot.get("analysis") or {}
        return bool(lines and a.get("lip_visible") and self._speaker(shot) in plan["swapped"])

    def _nonhuman(self) -> bool:
        looks = " ".join(f"{sw.name} {sw.look}" for sw in self.spec.cast.values())
        return any(w in looks for w in ("猫", "狗", "犬", "动物", "机器人", "兔", "熊", "狐", "鸟"))

    def _gaze(self, shot: dict) -> str:
        """Which way each face points and whom it looks at, from the breakdown's staging pass."""
        from rea_toon.breakdown import FACING_CN
        st = shot.get("staging") or {}
        rows = []
        for cid, v in (st.get("people") or {}).items():
            if cid not in self.chars:
                continue
            look = v.get("looking_at") or ""
            target = (self._who(look) if look in self.chars else
                      {"camera": "镜头", "offscreen_left": "画面左外侧", "offscreen_right": "画面右外侧", "down": "下方"}.get(look, ""))
            face = FACING_CN.get(v.get("facing"), "")
            parts = [f"脸朝{face}" if face else "", f"看着{target}" if target else ""]
            if any(parts):
                rows.append(f"{self._who(cid)}" + "、".join(x for x in parts if x))
        return "；".join(rows)

    def _staging_text(self, shot: dict) -> str:
        from rea_toon.breakdown import ANGLE_CN
        st = shot.get("staging") or {}
        out = []
        if st.get("angle") in ("high", "low", "dutch", "pov", "ots"):
            out.append(f"机位{ANGLE_CN[st['angle']]}，全程保持这个角度，不要拉平成平视")
        if self._gaze(shot):
            out.append(f"朝向和视线全程保持：{self._gaze(shot)}")
        if any("慢" in str(f) for f in (shot.get("key") or {}).get("fx") or []):
            out.append("这一镜是慢动作，动作速度明显慢于正常")
        return "".join(f"{x}。" for x in out)

    def _contact_text(self, shot: dict) -> str:
        """Each contact at its time: the original mix plays the hit there, so the action must land on it."""
        rows = []
        for c in shot.get("contacts") or []:
            hit = "（原声此刻有音效，接触必须正好在这个时间发生）" if c.get("sound") else ""
            hand = f"，用{c['hand']}" if c.get("hand") and c["hand"] != "不涉及" else ""
            rows.append(f"{c['t']:.1f}s 接触：{c['action']}{hand}{hit}")
        if not rows:
            return ""
        return "身体接触（按时间，严格照做）：" + "；".join(rows) + "。接触前后两个人的手、脸、身体轮廓清楚分开，不要融在一起，不要换人。"

    def _check_clip(self, shot: dict, plan: dict, frames: dict, clip: Path) -> dict:
        """VLM over the new first frame + 4 frames of the clip: does anyone change (species, face, outfit) or turn back
        into an original person, and does the right mouth move on each line? Any mode, every candidate."""
        from .media import frames_between
        first = frames.get("first")
        if not first or not plan["swapped"]:
            return {}
        mids = frames_between(clip, (0.2, 0.4, 0.6, 0.85), self.out / "qa" / shot["id"], "chk")
        who = "；".join(f"{self._who(c)}：{self.spec.cast[c].look[:60]}" for c in plan["swapped"])
        tag = {"on": "", "off": "（画外/背对）", "inner": "（内心独白，嘴闭着）", "narration": "（旁白，没人张嘴）"}
        lines = "；".join(f"{l['t0']:.1f}-{l['t1']:.1f}s {self._who(l['who'])}{tag[l['mode']]}：{l['text']}"
                         for l in self._lines(shot)) or "无台词"
        nonhuman = "（新角色都不是人：画面里不能有人的皮肤、人手、人脚、人脸）" if self._nonhuman() else ""
        try:
            r = self.vlm.ask_json(CLIP_CHECK_PROMPT.format(duration=shot["duration"], who=who, lines=lines, nonhuman=nonhuman,
                                                           gaze=self._gaze(shot) or "无要求"),
                                  [first, *mids], max_tokens=1024, timeout=90)
            # gaze is reported (check.md), not a retry reason: the keyframes already pin where faces point
            return {"identity_ok": bool(r.get("identity_ok", True)), "identity": r.get("identity", ""),
                    "mouth_ok": bool(r.get("mouth_ok", True)), "mouth": r.get("mouth", ""),
                    "gaze_ok": bool(r.get("gaze_ok", True)), "gaze": r.get("gaze", "")}
        except Exception as exc:  # a safety net, never a blocker
            LOG.warning("镜头 %s 成片检查失败：%s", shot["index"], exc)
            return {"error": str(exc)}

    def _guard(self, shot, plan, frames, out, key, prompt, refs=None, audio=None, video=None) -> None:
        """Every clip is measured before it is kept. What went wrong before, and what catches it now:
        - drift: the workflow re-frames the shot, swaps the background or cuts mid-shot (layout / light vs original);
        - frozen: H3 FL2VA holds the picture and jumps every 4 frames, which plays as stutter (still-frame share);
        - identity: a character changes species/face mid-clip or the original people grow back (VLM, any mode);
        - mouths: B's mouth moves on A's line, or nobody's on an on-camera line (VLM, from the per-line speakers);
        - sync: H3 voices the line itself and the mouths follow *its* speech (loudness correlation with the voice).
        Seeds are retried only while a clip is close; then the ladder goes ref_video -> lipsync -> first_last, and the
        best candidate is kept (a smooth, slightly re-framed clip beats a frozen one). The lag of the kept clip is
        stored so assembly can shift the picture onto the original voice."""
        import copy
        from .media import SYNC_MIN, audio_sync, cut_audio, frozen
        SYNC_RETRY = 0.5
        meta = json.loads(cache.meta_path(out).read_text(encoding="utf-8"))
        if meta.get("guard") == self.GUARD:
            return
        talks = self._talks(shot, plan)
        voice = self.work / self.story["audio"]["vocals"]
        seconds = self.video.clamp(shot["duration"])
        qa_dir = self.out / "qa" / shot["id"]
        pinned = ("first_last", "first")  # both ends pinned by keyframes: a big move just interpolates differently

        def evaluate(path: Path, mode: str) -> dict:
            qa = self._drift(shot, plan, frames, path, qa_dir)
            qa["motion"] = frozen(path, shot["duration"], self.work / "source.mp4", shot["start"])
            framed = mode in pinned or (qa["layout"] >= 0.25 and qa["light"] >= 0.5)
            usable = not qa["motion"]["frozen"] and (mode in pinned or qa["layout"] >= 0.12)
            qa["check"] = self._check_clip(shot, plan, frames, path) if usable else {}
            ck = qa["check"]
            own = self._voice_cut(shot, qa_dir / "own_voice.wav", seconds) if talks and usable else None
            sync = audio_sync(path, own, 0.0, shot["duration"]) if own else {}
            corr = sync.get("corr", 1.0) if talks else 1.0
            # not checked (frozen / far off) or the check errored: unknown counts as failed, so such a clip never
            # outranks one that was checked and passed (a re-framed ref_video clip can still be full of the original people)
            checked = bool(ck) and "error" not in ck or not (plan["swapped"] and frames.get("first"))
            ok = {"identity": checked and ck.get("identity_ok", True), "moving": not qa["motion"]["frozen"], "usable": usable,
                  "mouth": checked and ck.get("mouth_ok", True), "framed": framed, "sync": corr >= SYNC_MIN}
            return {"qa": qa, "sync": sync, "ok": ok, "corr": corr if usable else -1.0,
                    "rank": (ok["identity"], ok["moving"], ok["usable"], ok["mouth"], ok["framed"], corr)}

        def gen(mode: str, seed_off: int, dst: Path) -> str:
            p = {**plan, "mode": mode}
            pr = self._video_prompt(shot, p)
            eng = self._seeded(meta.get("seed_round", 0), seed_off)
            au, ref_v, rf = None, None, []
            if mode == "lipsync":
                au = self._voice_cut(shot, self.out / "audio" / f"{shot['id']}_vocals.wav", seconds)
            if mode == "ref_video":
                ref_v, rf = self._ref_inputs(shot, plan, frames)
            first = frames.get("first")
            eng.generate(pr, dst, seconds, self.story["aspect"], first=first,
                         last=frames.get("last") or (first if mode == "first_last" else None),
                         refs=rf, audio=au, video=ref_v, mode=mode)
            return pr

        must = int((shot.get("key") or {}).get("priority") == "must")
        mode0 = meta.get("mode", plan["mode"])
        still = (shot.get("analysis") or {}).get("change") != "large"
        ladder = [mode0]
        if mode0 == "ref_video" and talks and still and frames.get("first"):
            ladder.append("lipsync")
        if mode0 in pinned and self._motion_ref(shot):
            ladder.append("ref_video")  # a frozen first/last clip gets a motion-referenced try
        if mode0 == "lipsync" and self._motion_ref(shot):
            ladder.append("ref_video")  # the original shot shows whose mouth is shut while someone else talks
        if "first_last" not in ladder and mode0 != "first" and frames.get("first"):
            ladder.append("first_last")  # last resort: pinned, but often frozen
        cands = []    # (rank, path, mode, prompt, result)
        tmp = out.with_name(out.stem + ".try.mp4")
        done = False
        for li, mode in enumerate(ladder):
            # seeds: for sync only on dialogue; a character changing mid-clip (original people growing back,
            # two roles swapping looks) is seed luck, so it gets seeds in every mode (2 on pinned ones)
            tries = (self.SYNC_TRIES if mode not in pinned else 2) + must  # the shots the episode stands on get one more
            for k in range(tries):
                if li == 0 and k == 0:
                    path, pr = out, meta.get("prompt", prompt)
                else:
                    path = tmp
                    try:
                        pr = gen(mode, 1000 * li + k, path)
                    except Exception as exc:  # keep what we have
                        LOG.warning("镜头 %s 重试 %s 失败：%s", shot["index"], mode, exc)
                        continue
                r = evaluate(path, mode)
                bad = [n for n, v in r["ok"].items() if not v]
                LOG.info("镜头 %s %s#%d %s | 构图 %.2f 静止 %.2f 同步 %s %s", shot["index"], mode, k,
                         "通过" if not bad else "未过：" + ",".join(bad), r["qa"]["layout"], r["qa"]["motion"]["still"],
                         r["sync"].get("corr"), (r["qa"]["check"].get("identity") or "") + (r["qa"]["check"].get("mouth") or ""))
                keep = out.with_name(f"{out.stem}.c{len(cands)}.mp4")
                shutil.copy2(path, keep)
                cands.append((r["rank"], keep, mode, pr, r))
                if not bad:
                    done = True
                    break
                ok = r["ok"]
                if not ok["identity"]:
                    close = ok["moving"] and ok["usable"]  # worst failure, and random: another seed often fixes it
                else:
                    close = ok["moving"] and ok["framed"] and talks and (r["corr"] >= SYNC_RETRY or not ok["mouth"])
                if not close:
                    break  # far off: other seeds of this mode stayed far off too, go down the ladder
            if done:
                break
        if not cands:
            raise RuntimeError(f"镜头 {shot['index']} 没有生成出可用的视频")
        rank, path, mode, pr, r = max(cands, key=lambda c: c[0])
        shutil.move(str(path), out)
        failed = [n for n, v in r["ok"].items() if not v]
        if failed:
            LOG.warning("镜头 %s 所有候选都有问题，保留最好的一个（%s，未过：%s）", shot["index"], mode, ",".join(failed))
        meta.update(prompt=pr, mode=mode, qa={**r["qa"], "failed": failed}, guard=self.GUARD)
        meta.pop("fallback_from", None)
        if mode != plan["mode"]:
            meta["fallback_from"] = plan["mode"]
        if r["sync"]:
            meta["sync"] = r["sync"]
        else:
            meta.pop("sync", None)
        for c in cands:
            c[1].unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
        meta.pop("key", None)
        cache.stamp(out, key, **meta)

    # ---- orchestration ----
    def run(self, until: str = "clips", force: set[str] | None = None, only: list[int] | None = None) -> dict:
        require_locked(self.story)  # nothing is generated from an analysis nobody has reviewed
        force = force or set()
        plan = self.plan()
        LOG.info("计划：重做 %d 镜、复用原片 %d 镜；图片调用约 %d 次，视频 %d 段（%.1fs）",
                 plan["regen_shots"], plan["reuse_shots"], plan["image_calls"], plan["video_calls"], plan["video_seconds"])
        sheets = self.build_cast(force="cast" in force) if self.spec.cast else {}
        if until == "cast":
            return plan
        shots = {s["index"]: s for s in self.story["shots"]}
        todo = [p for p in plan["shots"] if p["route"] == "regen" and (not only or p["index"] in only)]
        results: dict[int, dict] = {}

        def frames_one(p: dict) -> dict:
            frames = self.keyframes(shots[p["index"]], p, sheets, force="frames" in force)
            return {"frames": {k: str(v) for k, v in frames.items()}, "mode": p["mode"]}

        def clip_one(p: dict) -> dict:
            r = results[p["index"]]
            clip = self.clip(shots[p["index"]], p, {k: Path(v) for k, v in r["frames"].items()}, sheets,
                             force="clips" in force)
            # the mode actually used (a drifting lip-sync clip falls back to first/last) decides retiming
            return {**r, "clip": str(clip),
                    "mode": json.loads(cache.meta_path(clip).read_text(encoding="utf-8")).get("mode", p["mode"])}

        def stage(fn, items: list[dict], workers: int, label: str) -> None:
            with ThreadPoolExecutor(max(1, workers)) as ex:
                futs = {ex.submit(fn, p): p["index"] for p in items}
                for f in as_completed(futs):
                    i = futs[f]
                    try:
                        results[i] = f.result()
                        LOG.info("镜头 %d %s完成", i, label)
                    except Exception as exc:
                        results[i] = {"error": str(exc)}
                        LOG.error("镜头 %d %s失败：%s", i, label, exc)

        # keyframes are image-API calls (parallel); clips share one GPU on h3local (`workers`)
        # scene anchors first: the other shots of the scene take the anchor's new first frame as a reference.
        # A partial run (--shots / --pilot) still makes the anchors' keyframes (frames only, no clip), else its
        # frames would be made without the reference now and be redone once the anchor exists.
        need = {p["scene_ref"]["shot"] for p in todo if p.get("scene_ref")} - {p["index"] for p in todo}
        extra = [p for p in plan["shots"] if p["index"] in need]
        first = [p for p in todo + extra if not p.get("scene_ref")]
        stage(frames_one, first, self.spec.frame_workers, "关键帧")
        stage(frames_one, [p for p in todo if p.get("scene_ref")], self.spec.frame_workers, "关键帧")
        for p in extra:  # not part of this run's clips or results
            results.pop(p["index"], None)
        if until != "frames":
            stage(clip_one, [p for p in todo if "error" not in results[p["index"]]], self.spec.workers, "视频")
        # a log of this run only: assembly reads each shot's clip and its .meta.json from disk, never this file
        (self.out / "results.json").write_text(json.dumps({str(k): v for k, v in results.items()}, ensure_ascii=False,
                                                          indent=1), encoding="utf-8")
        if self.spec.path and self.spec.path.resolve() != (self.out / "remake.yaml").resolve():
            shutil.copy2(self.spec.path, self.out / "remake.yaml")
        return {"plan": plan, "results": results}
