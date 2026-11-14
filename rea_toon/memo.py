"""Slow local measurements kept on disk between runs (OCR of frames, pixel motion of shots).

With the agent backend a run stops at every round of questions and is started again; the deep breakdown redoes its
passes in order each time, so without this the same minutes of OCR and motion measuring came back every round.
Only pure functions of the source video and the frames go in here: a different source.mp4 (size or mtime) drops the
whole file, and a key names everything its result depends on.
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any, Callable

from . import layout


def _stamp(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    return f"{st.st_size}:{st.st_mtime_ns}"


def frame_stamp(work: Path, rel: str | None) -> str:
    """What a key says about one frame file (frames are redrawn when a shot's cuts change)."""
    return f"{rel}@{_stamp(work / rel)}" if rel else ""


class Memo:
    def __init__(self, work: Path, name: str):
        self.path = layout.measured(work, name)
        self.source = _stamp(layout.source(work))
        self.items: dict[str, Any] = {}
        self.dirty = False
        self.lock = threading.Lock()
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if d.get("source") == self.source:
                self.items = d.get("items") or {}
        except (OSError, ValueError):
            pass

    def get(self, key: Any, fn: Callable[[], Any]) -> Any:
        """fn() the first time for this key, the kept result after (a copy: callers may change it)."""
        k = json.dumps(key, sort_keys=True, ensure_ascii=False)
        with self.lock:
            if k in self.items:
                return copy.deepcopy(self.items[k])
        v = fn()
        with self.lock:
            self.items[k] = copy.deepcopy(v)
            self.dirty = True
        return v

    def save(self) -> None:
        with self.lock:
            if self.dirty:
                layout.put(self.path, json.dumps({"source": self.source, "items": self.items}, ensure_ascii=False))
                self.dirty = False

    def __enter__(self) -> "Memo":
        return self

    def __exit__(self, *exc) -> None:
        self.save()  # also when the run stops for the agent's answers (NeedAgent)
