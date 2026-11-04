"""story.json + remake.yaml -> new keyframes and clips.

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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import cache
from .engines import GptImage2, make_video_engine
from .prompts import (RESTYLE_MOTION_PROMPT, CLIP_LEAK_PROMPT, CROWD_SHEET_PROMPT, FRAME_CHECK_PROMPT, FRAME_RESTYLE_PROMPT, FRAME_SWAP_PROMPT, LAST_FRAME_HINT, SHEET_FROM_IMAGE_PROMPT,
                      REF_VIDEO_PROMPT, SHEET_PROMPT, VIDEO_PROMPT)
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
        unknown = [cid for cid in spec.cast if cid not in self.chars]
        if unknown:
            raise ValueError(f"remake.yaml 里的角色 {unknown} 不在 story.json 里；可选：{list(self.chars)}")
        self.keep: set[str] = set()  # stages whose existing outputs are kept even if their inputs changed
        self._image: GptImage2 | None = None
        self._video = None
        self._vlm = None
        self._lock = threading.Lock()

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
            from .engines.vlm import VLM
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
        use_last = self.spec.frames == "first_last" and shot["duration"] >= 0.8
        frames = {}
        for name in ("first", "last") if use_last else ("first",):
            listed = a.get(f"in_{name}")
            visible = set(listed) if isinstance(listed, list) else in_shot
            visible |= {c for c in swapped if self.chars[c].get("object")}  # in_first/in_last only list people
            frame_swaps = [cid for cid in swapped if cid in visible]
            frames[name] = "edit" if (frame_swaps or self.spec.style) else "clean"
            plan.setdefault("frame_swaps", {})[name] = frame_swaps
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
            offs = [t for t in self.spec.anchors.get(shot["index"], []) if 0 < t < shot["duration"]][:7 - len(swapped)]
            for k, t in enumerate(offs, 1):
                frames[f"a{k}"] = "edit"
                plan.setdefault("frame_swaps", {})[f"a{k}"] = swapped
                plan.setdefault("anchors", {})[f"a{k}"] = round(t, 2)
        plan["gen_seconds"] = None  # filled by engine.clamp when the engine is known
        return plan

    def _motion_ref(self, shot: dict) -> bool:
        """Ref2VA takes 2-15s reference videos; shorter shots stay on first/last frames."""
        skip = parse_shots(str(self.spec.no_motion_ref), len(self.story["shots"])) if self.spec.no_motion_ref else []
        return (self.spec.video_engine == "h3local" and self.spec.motion_ref
                and 2.0 <= shot["duration"] <= 15 and shot["index"] not in skip)

    def _fades_in(self, shot: dict) -> bool:
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
        edits = sum(1 for p in regen for v in p["frames"].values() if v == "edit")
        summary = {"name": self.spec.name, "shots": shots, "regen_shots": len(regen),
                   "reuse_shots": len(shots) - len(regen), "cast_sheets": len(self.spec.cast),
                   "image_calls": len(self.spec.cast) + edits, "video_calls": len(regen),
                   "video_seconds": round(sum(self.story["shots"][p["index"] - 1]["duration"] for p in regen), 1),
                   "kept_roles": self.kept_roles(sel), "pilot": self.pilot_shots(shots)}
        (self.out / "plan.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        return summary

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
        regen = sorted((p for p in shots if p["route"] == "regen"), key=lambda p: -story[p["index"]]["duration"])
        large = lambda p: (story[p["index"]].get("analysis") or {}).get("change") == "large"
        talks = lambda p: self._talks(story[p["index"]], p)
        crowd = lambda p: any(self._is_crowd(c) for c in p["swapped"])
        kinds = [lambda p: talks(p) and not large(p),
                 lambda p: crowd(p) and not talks(p),
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
        key = cache.input_key(prompt=prompt, refs=refs, engine=self.image.describe())
        if not force and (cache.fresh(out, key) or ("cast" in self.keep and out.is_file())):
            return out
        self.image.generate(prompt, out, aspect="16:9", refs=refs or None)
        cache.stamp(out, key, prompt=prompt, character=sw.name)
        return out

    def build_cast(self, force: bool = False) -> dict[str, Path]:
        with ThreadPoolExecutor(max(1, len(self.spec.cast))) as ex:
            futs = {cid: ex.submit(self.cast_sheet, cid, force) for cid in self.spec.cast}
            return {cid: f.result() for cid, f in futs.items()}

    def _is_crowd(self, cid: str) -> bool:
        c = self.chars.get(cid, {})
        return "crowd" in cid or any(k in str(c.get("role", "")) for k in ("群众", "人群", "宾客", "路人"))

    # ---- stage 2: keyframes ----
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
            dress = (f"衣服必须和图1里这个人身上的一样（{outfit(orig.get('appearance', ''))}），不要换成别的服装"
                     if sw.costume == "keep" else f"服装：{sw.costume}")
            lines.append(f"- 把图1中的{orig.get('role')}（原外貌：{orig.get('appearance')}"
                         f"{'；在画面中：' + place if place else ''}）替换为图{first_ref + k}中的角色「{sw.name}」。{sw.look}"
                         f"{dress}。")
        lines.append("- 每个被替换的角色必须留在图1中原来的位置，保持原来的大小、朝向和遮挡关系（远处的小人仍然是远处的小人，"
                     "背对镜头的仍然背对）；不要交换角色位置，不要新增或放大人物；图1里看不到的角色不要添加。")
        return "\n".join(lines)

    def _frame_prompt(self, shot: dict, swaps: list[str], first_ref: int) -> str:
        a = shot.get("analysis") or {}
        emotion = a.get("emotion") or "与原画面一致"
        swap_text = self._swap_lines(shot, swaps, first_ref)
        if self.spec.style:
            return FRAME_RESTYLE_PROMPT.format(style=self.spec.style, emotion=emotion, swaps=swap_text)
        keeps_costume = all(self.spec.cast[c].costume == "keep" for c in swaps)
        costume_rule = "，服装保持原角色的服装" if keeps_costume else "，服装换成设定图里的服装"
        return FRAME_SWAP_PROMPT.format(swaps=swap_text, emotion=emotion, costume_rule=costume_rule,
                                        style_rule=f"- 画风：{self._style_text()}；替换后的角色要和画面光影、质感融为一体。")

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
            prompt = self._frame_prompt(shot, swaps, first_ref=2)
            if name != "first" and "first" in result and plan["frames"].get("first") == "edit":
                refs.append(result["first"])
                prompt += LAST_FRAME_HINT.format(n=len(refs))
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

    def _check_frame(self, src: Path, out: Path, swaps: list[str], note: str = "") -> dict:
        who = "、".join(f"{self.chars[c].get('role')}（换成{self.spec.cast[c].name}）" for c in swaps)
        prompt = FRAME_CHECK_PROMPT.format(who=who)
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
        check = self._check_frame(src, out, swaps, note)
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
            check = {**self._check_frame(src, out, swaps, note), "retried": problems}
            if not check.get("ok"):
                LOG.warning("镜头 %s %s 帧重画后仍未通过质检：%s", shot["index"], name, check.get("problems"))
        meta.pop("key", None)
        cache.stamp(out, key, **{**meta, "check": check})

    # ---- stage 3: clips ----
    def performance(self, shot: dict) -> dict:
        """Time-coded acting beats from the original shot (computed once, saved into story.json)."""
        perf = shot.get("performance")
        if perf and not perf.get("error"):
            return perf
        from .analyze import performance
        perf = performance(self.work, self.story, shot)
        if not perf.get("error"):
            with self._lock:
                shot["performance"] = perf
                (self.work / "story.json").write_text(json.dumps(self.story, ensure_ascii=False, indent=1), encoding="utf-8")
        return perf

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
    def _speaker(shot: dict) -> str | None:
        sp = (shot.get("analysis") or {}).get("speaker")
        return None if str(sp).strip().lower() in ("", "none", "null", "offscreen") else sp

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
            fields = (b.get("body"),) if wide else (b.get("face"), b.get("mouth"), b.get("body"))
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
        if self._speaker(shot) and not self._wide(shot):
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
        cast_note = "".join(f"画面中的{self.chars[c].get('role')}是{self.spec.cast[c].name}。" for c in plan["swapped"])
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
        sp = self._speaker(shot)
        speech = (f"; {self._who(sp)} speaks these lines, so his/her mouth must open and close exactly with it"
                  if sp and shot.get("subtitles") else "")
        return REF_VIDEO_PROMPT.format(pictures="\n".join(lines), replace=" ".join(replace), speech=speech, base=base).strip()

    def clip(self, shot: dict, plan: dict, frames: dict[str, Path], sheets: dict[str, Path], force: bool = False) -> Path:
        from .media import cut_audio
        out = self.out / "clips" / f"{shot['id']}.mp4"
        prompt = self._video_prompt(shot, plan)
        mode = plan["mode"]
        seconds = self.video.clamp(shot["duration"])
        audio = None
        if mode == "lipsync":
            audio = self.out / "audio" / f"{shot['id']}_vocals.wav"
            # the voice of exactly this shot, padded to the generated length so the mouth stays on the words
            cut_audio(self.work / self.story["audio"]["vocals"], shot["start"], shot["duration"], audio, pad_to=seconds)
        refs = [sheets[c] for c in plan["swapped"]] if mode in ("refs", "ref_video") else []
        if mode == "ref_video":
            refs = [frames[n] for n in plan.get("anchors", {}) if n in frames] + refs
        ref_video = None
        if mode == "ref_video":
            from .engines.video import H3Local
            from .media import ref_clip
            ref_video = self.out / "refclips" / f"{shot['id']}.mp4"
            ref_clip(self.work / "source.mp4", self.work / self.story["audio"]["vocals"], shot["start"], shot["duration"],
                     shot.get("subtitles", []), ref_video, size=H3Local.RES.get(self.story["aspect"], (1024, 576)))
        key = cache.input_key(prompt=prompt, mode=mode, seconds=seconds, engine=self.video.describe(),
                              first=frames.get("first"), last=frames.get("last"), refs=refs, audio=audio, video=ref_video)
        if force or not cache.fresh(out, key):
            self.video.generate(prompt, out, seconds, self.story["aspect"], first=frames.get("first"),
                                last=frames.get("last"), refs=refs, audio=audio, video=ref_video,
                                mode="first" if mode == "refs" else mode)
            cache.stamp(out, key, prompt=prompt, mode=mode, seconds=seconds, engine=self.video.describe())
        if mode in ("lipsync", "ref_video"):
            self._guard_lipsync(shot, plan, frames, out, key, prompt, refs=refs, audio=audio, video=ref_video)
        return out

    SYNC_TRIES = 3     # seeds tried per audio-driven mode before keeping the best one

    def _talks(self, shot: dict, plan: dict) -> bool:
        a = shot.get("analysis") or {}
        return bool(shot.get("subtitles") and a.get("lip_visible") and self._speaker(shot) in plan["swapped"])

    def _check_leak(self, shot: dict, plan: dict, frames: dict, clip: Path) -> dict:
        """VLM: do the middle frames still show the new characters of the (reviewed) new first frame?"""
        from .media import frames_between
        first = frames.get("first")
        if not first or not plan["swapped"]:
            return {"leak": False}
        mids = frames_between(clip, (1 / 3, 2 / 3), self.out / "qa" / shot["id"], "mid")
        who = "、".join(f"{self.chars[c].get('role')} → {self.spec.cast[c].name}" for c in plan["swapped"])
        try:
            r = self.vlm.ask_json(CLIP_LEAK_PROMPT.format(who=who), [first, *mids], max_tokens=1024)
            return {"leak": bool(r.get("leak")), "problems": r.get("problems", "")}
        except Exception as exc:  # a safety net, never a blocker
            LOG.warning("镜头 %s 漏换检查失败：%s", shot["index"], exc)
            return {"leak": False, "error": str(exc)}

    def _guard_lipsync(self, shot, plan, frames, out, key, prompt, refs=None, audio=None, video=None) -> None:
        """Clips that take the original shot or voice (ref_video / lipsync) can go wrong three ways:
        - drift: the workflow re-frames the shot, swaps the background or cuts mid-shot;
        - leak (ref_video): the original people grow back mid-clip (clothes/build copied from the reference);
        - sync: H3 voices the line itself and the mouths follow *its* speech, which may start late or differ
          from the original voice laid under the final cut.
        Measure both; retry other seeds for bad sync, then fall back ref_video -> lipsync -> first_last.
        The lag of the kept clip is stored so assembly can shift the picture onto the original voice."""
        import copy
        import shutil
        from .media import SYNC_MIN, audio_sync, clip_drift, cut_audio
        meta = json.loads(cache.meta_path(out).read_text(encoding="utf-8"))
        talks = self._talks(shot, plan)
        if "qa" in meta and ("sync" in meta or not talks):
            return
        voice = self.work / self.story["audio"]["vocals"]
        seconds = self.video.clamp(shot["duration"])
        qa_dir = self.out / "qa" / shot["id"]

        def check(path: Path, mode: str) -> tuple[bool, dict, dict]:
            qa = self._drift(shot, plan, frames, path, qa_dir)
            # first/last clips are pinned by both keyframes: a big camera move just interpolates differently
            ok = mode == "first_last" or (qa["layout"] >= 0.25 and qa["light"] >= 0.5)
            if ok and mode == "ref_video":
                qa["leak"] = self._check_leak(shot, plan, frames, path)
                ok = not qa["leak"]["leak"]
            sync = audio_sync(path, voice, shot["start"], shot["duration"]) if ok and talks else {}
            return ok, qa, sync

        def gen(mode: str, seed_off: int, dst: Path) -> str:
            p = {**plan, "mode": mode}
            pr = self._video_prompt(shot, p)
            eng = copy.copy(self.video)
            if hasattr(eng, "seed"):
                eng.seed += seed_off
            au = None
            if mode == "lipsync":
                au = self.out / "audio" / f"{shot['id']}_vocals.wav"
                cut_audio(voice, shot["start"], shot["duration"], au, pad_to=seconds)
            first = frames.get("first")
            eng.generate(pr, dst, seconds, self.story["aspect"], first=first,
                         last=frames.get("last") or (first if mode == "first_last" else None),
                         refs=refs if mode == "ref_video" else [], audio=au,
                         video=video if mode == "ref_video" else None, mode=mode)
            return pr

        mode0 = meta.get("mode", plan["mode"])
        # first/last clips voice the line from the prompt; the mouths follow that speech, so they get measured too
        still = (shot.get("analysis") or {}).get("change") != "large"
        ladder = [mode0] + (["lipsync"] if mode0 == "ref_video" and talks and still and frames.get("first") else [])
        if talks and mode0 != "first_last":
            ladder.append("first_last")
        best = None   # (corr, path, mode, prompt, qa, sync)
        tmp = out.with_name(out.stem + ".try.mp4")
        for mode in ladder:
            # no dialogue: one more seed of the motion reference before falling back, since first/last frames
            # lose the whole action of a long take (people lie down upside down, props reappear)
            tries = self.SYNC_TRIES if talks else (2 if mode == "ref_video" else 1)
            for k in range(tries):
                if mode == mode0 and k == 0:
                    path, pr = out, meta.get("prompt", prompt)
                else:
                    path = tmp
                    try:
                        pr = gen(mode, 1000 * (ladder.index(mode)) + k, path)
                    except Exception as exc:  # keep what we have
                        LOG.warning("镜头 %s 重试 %s 失败：%s", shot["index"], mode, exc)
                        continue
                ok, qa, sync = check(path, mode)
                LOG.info("镜头 %s %s#%d 构图 %s 同步 %s", shot["index"], mode, k, qa, sync)
                if not ok:
                    if mode == "ref_video" and not talks and k + 1 < tries:
                        continue
                    break  # re-framed or leaking: other seeds of this mode rarely help, go down the ladder
                score = sync.get("corr", 1.0)
                if best is None or score > best[0]:
                    keep = out.with_name(out.stem + ".best.mp4")
                    shutil.copy2(path, keep)
                    best = (score, keep, mode, pr, qa, sync)
                if score >= SYNC_MIN:
                    break
            if best and best[0] >= SYNC_MIN:
                break
        if best:
            shutil.move(str(best[1]), out)
            meta.update(prompt=best[3], mode=best[2], qa=best[4], sync=best[5])
            if best[2] != mode0:
                meta["fallback_from"] = mode0
        else:
            LOG.info("镜头 %s 构图跑偏或原片的人漏回来，改用首尾帧重做", shot["index"])
            pr = gen("first_last", 0, out)
            meta.update(prompt=pr, mode="first_last", fallback_from=mode0,
                        qa=self._drift(shot, plan, frames, out, qa_dir))
            meta.pop("sync", None)
        tmp.unlink(missing_ok=True)
        meta.pop("key", None)
        cache.stamp(out, key, **meta)

    # ---- orchestration ----
    def run(self, until: str = "clips", force: set[str] | None = None, only: list[int] | None = None) -> dict:
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
        stage(frames_one, todo, self.spec.frame_workers, "关键帧")
        if until != "frames":
            stage(clip_one, [p for p in todo if "error" not in results[p["index"]]], self.spec.workers, "视频")
        # a log of this run only: assembly reads each shot's clip and its .meta.json from disk, never this file
        (self.out / "results.json").write_text(json.dumps({str(k): v for k, v in results.items()}, ensure_ascii=False,
                                                          indent=1), encoding="utf-8")
        if self.spec.path and self.spec.path.resolve() != (self.out / "remake.yaml").resolve():
            shutil.copy2(self.spec.path, self.out / "remake.yaml")
        return {"plan": plan, "results": results}
