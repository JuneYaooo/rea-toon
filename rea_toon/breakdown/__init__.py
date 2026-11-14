"""Deep breakdown (stage 3): reading the drama as a whole, after the shots, cast and lines are settled.

perceive.py and refine.py see shots, subtitles, cast, who says each line. This stage reads the drama as a whole, the
way a director would before shooting it again. The passes, in the order `run` does them:

- scenes       style_bible (visual rules of the whole film) + continuous scenes, and each person's look *in that
               scene* (outfit, wig, face mask, props, stains) plus changes inside it        scenes.py
- screen_text  text in the picture besides subtitles: signs, bubbles, 花字, AIGC labels, watermarks   screen_text.py
- staging      camera angle, facing, who looks at whom; effects, manga marks, panels       staging.py
- contacts     slaps, splashes, hand-overs and the sound hit they land on (full depth only) acting.py
- freezes, motions  held frames, still / pushed / partly moving pictures (local, no model)  motion.py
- expressions  the expressions each main character plays                                   acting.py
- beats        the dramatic spine and per-shot key: role, feel, priority, function, keep,  beats.py
               fx; the information gaps the episode plays on

`deepen` adds what a fast analysis leaves out: per-second acting and contacts.
Risks and lint (code only) are checks.py; pipeline.py loads / saves the story and writes the review pages.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import layout
from ..media import contact_sheets
from ..vlm import VLM
from . import acting, beats, motion, scenes, screen_text, staging

LOG = logging.getLogger(__name__)


def run(story: dict, work: Path, vlm: VLM, workers: int = 6) -> None:
    """All deep passes over a perceived story, in place. A pass that fails is logged and the rest go on
    (lint reports what is missing); the scenes and beats calls are the spine and raise."""
    # in shot order: by name, contact_10 would come before contact_2
    sheets = (sorted(layout.analysis(work).glob("contact_*.jpg"), key=lambda p: int(p.stem.rsplit("_", 1)[-1]))
              or contact_sheets(story["shots"], work))
    LOG.info("深度拆解：画风档案 + 分场 …")
    scenes.scenes(story, work, vlm, sheets, workers)
    LOG.info("深度拆解：画面里的文字（招牌/屏幕/花字/水印）…")
    try:
        LOG.info("画面文字 %d 处", screen_text.screen_text(story, work, vlm, workers))
    except Exception as exc:  # e.g. OCR not installed: the breakdown goes on, lint says the step is missing
        LOG.warning("画面文字识别失败：%s", exc)
    for label, fn in (("机位角度和视线", lambda: staging.staging(story, work, vlm, workers)),
                      ("身体接触和音效点", lambda: acting.contacts(story, work, vlm, workers) if story.get("depth") != "fast"
                       else "快拆跳过（要补跑 rea-toon deepen）"),
                      ("定格", lambda: motion.freezes(story, work)),
                      ("画面动法（静图推拉/局部动/全动画）", lambda: motion.motions(story, work)),
                      ("角色表情清单", lambda: acting.expressions(story, vlm))):
        LOG.info("深度拆解：%s …", label)
        try:
            LOG.info("%s：%s", label, fn())
        except Exception as exc:  # the breakdown goes on with less to go by
            LOG.warning("%s失败：%s", label, exc)
    LOG.info("深度拆解：戏剧节拍和逐镜看点 …")
    beats.beats(story, work, vlm, sheets)


def deepen(story: dict, work: Path, vlm: VLM, workers: int = 6) -> None:
    """The per-second acting pass and (on a broken-down story) the contact pass; nothing else is asked again."""
    LOG.info("补拆：逐秒表演 %d 镜", acting.add_performance(story, work, vlm, workers))
    if story.get("breakdown"):
        LOG.info("补拆：身体接触和音效点 %s", acting.contacts(story, work, vlm, workers))
    story["depth"] = "full"
