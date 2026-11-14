"""The stages of one episode, and the only place that runs them in order.

    ingest      source -> work/source.mp4                                  ingest.py
    perceive    shots, frames, subtitles, cast, per-shot read               perceive.py
    refine      who says each line, one id per person                       refine.py
    breakdown   scenes and looks, screen text, staging, motion, beats       breakdown/
    report      pacing, payoffs, screen time, tropes -> pages/report.html   report.py
    (review)    a person reads pages/breakdown.html and edits story.json
    deepen      per-second acting + contacts, on top of a fast analysis       breakdown/
    lock        approval of the reviewed breakdown                          story.py

Every stage here loads story.json, changes it, redoes the code-only checks (checks.py) and saves it with what is made
from it: the review page for people and the VIDEO.md package for models (videomd.py). Where each file of a work dir
sits is layout.py. A stage that re-asks the model for the breakdown unlocks the story; report never writes story.json.
`episode` runs perceive -> breakdown -> report and skips what is done, so running `analyze` again (the agent does
after each round of answers, a person does after editing story.json) never throws the reviewed story away; only an
explicit change (other --cuts, --full on a fast story) perceives again, and the story before is kept in data/.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from . import breakdown as bd
from . import layout
from . import perceive as perceive_mod
from . import refine as refine_mod
from . import report as report_mod
from .checks import lint, risks
from .review import write_review
from .story import digest, load, save
from .videomd import write as package
from .vlm import for_work

LOG = logging.getLogger(__name__)


def _save(work: Path, story: dict, unlock: bool = False) -> None:
    """Save the story with fresh checks, its review page and its package. A locked story edited by a stage shows as stale."""
    if story.get("breakdown"):
        for s in story["shots"]:
            s["risks"] = risks(story, s)
        story["lint"] = lint(story)
    if unlock:
        story.pop("lock", None)
    save(work, story)
    if story.get("breakdown"):
        write_review(story, work)
    package(work, story)


# ---- stages ----

def perceive(work: Path, use_vlm: bool = True, workers: int = 6, cuts: list[float] | None = None,
             prior: dict | None = None, full: bool = False) -> dict:
    """A new story.json from work/source.mp4 (replaces the old one), refined when the model is on.
    prior: what earlier episodes of a series settled (series.prior_of); full: one call per shot + acting pass.
    A story already there (hand edits, a lock) is copied to data/story.prev.json first."""
    if layout.story(work).is_file():
        layout.put(layout.prev(work, "story.json"), layout.story(work).read_text(encoding="utf-8"))
        LOG.info("重新感知：原来的 story.json 存在 data/story.prev.json")
    story = perceive_mod.perceive(work, use_vlm=use_vlm, workers=workers, cuts=cuts, prior=prior, full=full)
    if use_vlm:
        refine_mod.refine(story, work, for_work(work))
    _save(work, story)
    return story


def refine(work: Path) -> dict:
    """Redo who says each line and the identity merge on an existing story (no re-analysis of the shots)."""
    story = load(work)
    r = refine_mod.refine(story, work, for_work(work))
    _save(work, story)
    return r


def proofread(work: Path) -> int:
    """Proofread the OCR subtitles again and carry the fixes into the story."""
    story = load(work)
    n = refine_mod.proofread_story(story, work, for_work(work))
    _save(work, story)
    return n


def subtitles(work: Path) -> dict:
    """Read the subtitle track again and put it into the story; speakers already settled stay (no model call)."""
    story = load(work)
    r = refine_mod.resync_subtitles(story, work)
    _save(work, story)
    return r


def breakdown(work: Path, workers: int = 6, prior: dict | None = None) -> dict:
    """The deep breakdown, from scratch. A redo replaces hand edits of key / scenes: the old file is kept to copy
    them back from. prior: what earlier episodes of a series settled (series.prior_of), read by the beats pass; an
    episode redone on its own keeps the one it was analysed with."""
    before = layout.story(work).read_text(encoding="utf-8")
    story = load(work)
    if prior:
        story["prior"] = prior
    if story.get("breakdown"):
        layout.put(layout.prev(work, "story.json"), before)
        LOG.info("重做深度拆解：旧版本存在 data/story.prev.json（手改过的看点/造型可以从这里抄回来）")
    bd.run(story, work, for_work(work), workers)
    _save(work, story, unlock=True)
    return story


def deepen(work: Path, workers: int = 6) -> dict:
    """What a fast analysis left out: the per-second acting and contact passes."""
    story = load(work)
    bd.deepen(story, work, for_work(work), workers)
    _save(work, story, unlock=True)
    return story


def report(work: Path, use_vlm: bool = True) -> dict:
    return report_mod.report(work, use_vlm=use_vlm)


def pages(work: Path) -> list[Path]:
    """Only the pages and the package again, from what is on disk (after an update of how they look): no model
    call, story.json / report.json only read. work: one episode's directory, or a series'."""
    from . import report as rp, series
    if layout.series(work).is_file():
        series.render(work)
        return [layout.site(work), layout.series_entry(work)]
    story = load(work)
    story["lint"] = lint(story)  # as the checks stand today; shown, not saved
    if story.get("breakdown"):
        write_review(story, work)
    rp.render(work, story=story)
    from .page import link_up
    home = link_up(work)  # one video has the same site as a series, without the episode row
    return [x for x in (home, package(work, story)) if x]


def lock(work: Path, force: bool = False) -> dict:
    """Approve the reviewed breakdown. Refuses while lint has errors, unless forced."""
    story = load(work)
    story["lint"] = lint(story)
    errors = [x for x in story["lint"] if x["level"] == "error"]
    if errors and not force:
        raise ValueError(f"拆解还有 {len(errors)} 个必须修的问题（看 VIDEO.md 的「要处理的问题」），修好再 lock；"
                         "确实要带着这些问题锁定就加 --force")
    story["lock"] = {"hash": digest(story), "at": time.strftime("%Y-%m-%d %H:%M:%S"), "forced": bool(errors)}
    save(work, story)
    write_review(story, work)
    package(work, story)
    return story["lock"]


# ---- one episode, start to review ----

def _perceived(story: dict | None) -> bool:
    return bool(story) and any(s.get("analysis") for s in story.get("shots") or [])


def _report_fresh(work: Path) -> bool:
    rp = layout.data(work, "report.json")
    if not rp.is_file() or rp.stat().st_mtime < layout.story(work).stat().st_mtime:
        return False
    return json.loads(rp.read_text(encoding="utf-8")).get("schema") == report_mod.SCHEMA  # older numbers: redo


def done(work: Path, use_vlm: bool = True) -> bool:
    """Nothing left for `episode(resume=True)` to do here."""
    if not layout.story(work).is_file():
        return False
    story = load(work)
    return _perceived(story) and (not use_vlm or bool((story.get("breakdown") or {}).get("beats")) and _report_fresh(work))


def _asked_again(story: dict, cuts: list[float] | None, full: bool) -> bool:
    """The command asks for another perception than the one on disk: other forced cuts, or --full on a fast story.
    cuts=None (the option left out) keeps the cuts the story was made with."""
    return (cuts is not None and sorted(cuts) != sorted(story.get("cuts") or [])) or (full and story.get("depth") == "fast")


def episode(work: Path, use_vlm: bool = True, workers: int = 6, cuts: list[float] | None = None,
            full: bool = False, prior: dict | None = None, resume: bool = True) -> dict:
    """perceive -> breakdown -> report, each stage only when it is not done yet (or the command asks for another
    perception, `_asked_again`). resume=False redoes every stage (answers are cached: only what changed costs
    anything); a series uses it for an episode that was broken down without an earlier one's context.
    Returns {"story", "report"}."""
    story = load(work) if layout.story(work).is_file() else None
    if not resume or not _perceived(story) or _asked_again(story, cuts, full):
        story = perceive(work, use_vlm=use_vlm, workers=workers, cuts=cuts, prior=prior, full=full)
    else:
        meta = layout.data(work, "source.json")
        if meta.is_file() and (src := json.loads(meta.read_text(encoding="utf-8"))) != story.get("source"):
            story["source"] = src  # a new --title
            _save(work, story)
    out = {"story": story, "report": None}
    if not use_vlm:
        return out
    if not resume or not (story.get("breakdown") or {}).get("beats"):
        out["story"] = breakdown(work, workers=workers, prior=prior)
    if not resume or not _report_fresh(work):
        out["report"] = report(work)
    else:
        out["report"] = json.loads(layout.data(work, "report.json").read_text(encoding="utf-8"))
    return out
