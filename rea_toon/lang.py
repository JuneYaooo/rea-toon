"""The language the breakdown is written in: the one the user speaks.

Two halves:
- what the model writes (summaries, per-shot reads, beats, the report, the script's action lines): `instruction`
  is appended to the prompts whose answers people read, only when the language is not Chinese, so a Chinese work dir
  keeps its prompts and answer cache as they were. Dialogue and subtitles stay in the drama's own language.
- the pages' own words (headings, labels, table heads): the code writes them in Chinese and wraps them in `_()`;
  `_()` looks them up in the table of the current language (`rea_toon/i18n/`). Chinese and English are built in;
  any other language gets the English page frame around content the model wrote in that language.

Where it is set: `--lang` on analyze / series, kept in data/source.json (one episode) or series.json (a series, for
all its episodes). `activate` makes it the language of the pages this process writes.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from pathlib import Path

DEFAULT = "zh"

# code -> the name the model is told (any other value given to --lang is passed on as it is: "español", "Deutsch" …)
NAMES = {"zh": "简体中文", "zh-hans": "简体中文", "zh-cn": "简体中文", "zh-hant": "繁體中文", "zh-tw": "繁體中文",
         "zh-hk": "繁體中文", "en": "English", "ja": "日本語", "ko": "한국어", "es": "Español", "fr": "Français",
         "de": "Deutsch", "pt": "Português", "ru": "Русский", "it": "Italiano", "vi": "Tiếng Việt", "th": "ภาษาไทย",
         "id": "Bahasa Indonesia", "ar": "العربية", "tr": "Türkçe", "hi": "हिन्दी"}
_ALIASES = {"chinese": "zh", "中文": "zh", "简体中文": "zh", "english": "en", "英文": "en", "英语": "en",
            "japanese": "ja", "日本語": "ja", "日语": "ja", "korean": "ko", "한국어": "ko", "韩语": "ko"}


def norm(code: str | None) -> str:
    """'EN' / 'english' / 'en-US' -> 'en'; 'zh-CN' -> 'zh'; '' / None -> the default; an unknown name is kept as given."""
    c = (code or "").strip()
    low = c.lower().replace("_", "-")
    if not c:
        return DEFAULT
    if c in _ALIASES or low in _ALIASES:
        return _ALIASES.get(c) or _ALIASES[low]
    if low in ("zh-hans", "zh-cn", "zh-sg"):
        return "zh"
    if low in NAMES:
        return low
    base = low.split("-")[0]
    return base if base in NAMES and base != "zh" else c


def is_zh(code: str) -> bool:
    """Simplified Chinese: the language the prompts and pages are written in."""
    return norm(code) == "zh"


def name(code: str) -> str:
    return NAMES.get(code.lower(), code)


def html(code: str) -> str:
    """The value of <html lang=…>."""
    return code if code.lower() in NAMES else "en"


# ---- where it is kept ----

def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def of(work: Path) -> str:
    """The language of a work dir: its series' (an episode follows its series), else its own, else the default."""
    from . import layout
    if layout.series(work).is_file():
        return norm(_read(layout.series(work)).get("lang"))
    root = layout.series_of(work)
    if root:
        return norm(_read(layout.series(root)).get("lang"))
    return norm(_read(layout.data(work, "source.json")).get("lang"))


def keep(work: Path, code: str) -> str:
    """Remember the language of one episode dir (data/source.json, which the story mirrors as story["source"])."""
    from . import layout
    p = layout.data(work, "source.json")
    meta = _read(p)
    code = norm(code)
    if meta.get("lang") != code:
        meta["lang"] = code
        layout.put(p, json.dumps(meta, ensure_ascii=False, indent=2))
    return code


# ---- what the model writes ----

def instruction(code: str) -> str:
    """Appended to a prompt whose answer people read; nothing for Chinese (the prompts are written in it)."""
    if is_zh(code):
        return ""
    return (f"\n\nOUTPUT LANGUAGE: the user reads {name(code)}. Write every free-text value meant for readers "
            f"(summaries, descriptions, appearances, places, reasons, analysis, names of roles, tropes and expressions) "
            f"in {name(code)}, even where a field above says 中文. Keep exactly as they are: JSON keys, ids, values picked "
            "from a fixed option list in this prompt (such as 爽/憋/悬, hook/setup, 特写/近景/中景, 日/夜, 内/外, "
            "lead/supporting, true/false), and quoted dialogue, subtitle or on-screen text, which stays in the drama's "
            "original language. A character's name stays as it is said in the drama; you may add a transliteration "
            "in parentheses. motion_prompt stays in Chinese.")


# ---- the pages' own words ----

_current = DEFAULT
_tables: dict[str, dict[str, str]] = {}


def activate(code: str) -> None:
    """The language of the pages this process writes from now on."""
    global _current
    _current = norm(code)


def current() -> str:
    return _current


def ui() -> str:
    """The language of the page frame: Chinese for Chinese, else English (the only other built-in table)."""
    return "zh" if _current.lower().startswith("zh") else "en"


def _table(ui_code: str) -> dict[str, str]:
    if ui_code not in _tables:
        from . import i18n
        out: dict[str, str] = {}
        for m in pkgutil.iter_modules(i18n.__path__):
            if m.name.startswith(ui_code + "_") or m.name == ui_code:
                out.update(getattr(importlib.import_module(f"{i18n.__name__}.{m.name}"), "T", {}))
        _tables[ui_code] = out
    return _tables[ui_code]


def _(s: str) -> str:
    """A page word or template written in Chinese, in the current page language (unknown ones stay as written)."""
    if not isinstance(s, str) or ui() == "zh":
        return s
    return _table(ui()).get(s, s)


def part(name: str):
    """`_` for one group of pages: its own table (i18n/<ui>_<name>.py) first, then all the others. The same Chinese
    word can want another English on another page (镜头: "Shots" in a table head, "the camera" in a sentence)."""
    def tr(s: str) -> str:
        if not isinstance(s, str) or ui() == "zh":
            return s
        own = _part_table(ui(), name)
        return own[s] if s in own else _table(ui()).get(s, s)
    return tr


def _part_table(ui_code: str, name: str) -> dict[str, str]:
    key = f"{ui_code}_{name}"
    if key not in _tables:
        try:
            _tables[key] = getattr(importlib.import_module(f"{__package__}.i18n.{key}"), "T", {})
        except ImportError:
            _tables[key] = {}
    return _tables[key]
