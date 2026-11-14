"""Who answers the breakdown's questions (a prompt, a few pictures, JSON back), with a disk cache.

Two backends behind the same `ask_json`:
- agent (default): the coding agent running the command reads the pictures itself. A question nobody answered yet is
  written to <work>/analysis/agent/<key>.json and the run stops (NeedAgent); the agent writes <key>.answer.json and
  runs the same command again, which picks the answers up and goes on to the next stage.
- api: an OpenAI-compatible multimodal chat (Gemini / GPT-4o / Qwen-VL ...). Used when the user configured one for
  rea-toon (REA_TOON_VLM_BASE_URL / _API_KEY / _MODEL) or asked for it (--backend api / REA_TOON_BACKEND=api).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

LOG = logging.getLogger(__name__)


def parse_json(text: str) -> dict | list:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


_MISSING: set[tuple[str, str]] = set()  # (base url, model) a relay said it does not have
_SWEPT: set[Path] = set()  # agent folders whose unanswered questions of an earlier run were cleared in this run


class NeedAgent(BaseException):
    """Agent backend: questions are waiting in `folder` for the agent to answer. Not an Exception on purpose: the
    stages treat a failed model call as "this pass failed, go on" (`except Exception`), and an unanswered question
    is not a failure; it has to stop the run, after the other questions of the same stage were written too."""

    def __init__(self, folder: Path):
        super().__init__(f"等 agent 看图作答：{folder}")
        self.folder = folder


def backend_of() -> str:
    """agent unless the user configured a model for rea-toon or asked for the api. Keys borrowed from another
    project's .env (REA_TOON_VLM_FALLBACK_PREFIXES) do not switch it on by themselves."""
    asked = os.getenv("REA_TOON_BACKEND", "").strip().lower()
    if asked in ("agent", "api"):
        return asked
    return "api" if all(os.getenv(f"REA_TOON_VLM_{k}", "").strip() for k in ("BASE_URL", "API_KEY", "MODEL")) else "agent"


class Pool:
    """A thread pool for model calls. Its map runs every call before it gives up: with the agent backend an
    unanswered question stops the run (NeedAgent), and the plain executor would cancel the calls not started yet,
    leaving the agent a handful of questions per round instead of the whole stage's."""

    def __init__(self, workers: int):
        self._ex = ThreadPoolExecutor(workers)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._ex.shutdown(wait=True)

    def map(self, fn, items) -> list:
        futures = [self._ex.submit(fn, x) for x in items]
        out, need = [], None
        for f in futures:
            try:
                out.append(f.result())
            except NeedAgent as stop:
                need = stop
        if need:
            raise need
        return out


def pending(folder: Path) -> list[dict]:
    """The questions in an agent folder that have no answer yet, oldest first."""
    out = []
    for p in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime):
        if not p.name.endswith(".answer.json") and not p.with_name(p.stem + ".answer.json").is_file():
            out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


class VLM:
    """Config from env: REA_TOON_VLM_BASE_URL / _API_KEY / _MODEL first, then the endpoints of other prefixes named
    in REA_TOON_VLM_FALLBACK_PREFIXES (e.g. OTHER: OTHER_BASE_URL / _API_KEY / _MODEL_NAME or _MODEL)."""

    @staticmethod
    def prefixes() -> list[tuple[str, str]]:
        out = [("REA_TOON_VLM", "REA_TOON_VLM_MODEL")]
        for p in (x.strip() for x in os.getenv("REA_TOON_VLM_FALLBACK_PREFIXES", "").split(",")):
            if p:
                out.append((p, f"{p}_MODEL_NAME" if os.getenv(f"{p}_MODEL_NAME") else f"{p}_MODEL"))
        return out

    def __init__(self, cache_dir: Path | None = None, timeout: float = 300, backend: str | None = None):
        self.endpoints: list[tuple[str, str, str]] = []
        self.backend = backend or backend_of()
        self._lock = threading.Lock()
        self.cache_dir = cache_dir
        self.timeout = timeout
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        if self.backend == "agent":
            if not cache_dir:
                raise RuntimeError("agent 作答要有工作目录（问题和答案放在 <work>/analysis/agent/）")
            return
        for prefix, mkey in self.prefixes():
            base = os.getenv(f"{prefix}_BASE_URL", "").rstrip("/")
            key = os.getenv(f"{prefix}_API_KEY", "")
            for model in [m.strip() for m in os.getenv(mkey, "").split(",") if m.strip()]:
                if base and key and (base, key, model) not in self.endpoints:
                    self.endpoints.append((base, key, model))
        # extra model names tried on the first configured endpoint when the usual ones are down
        extra = [m.strip() for m in os.getenv("REA_TOON_VLM_FALLBACK_MODELS", "").split(",") if m.strip()]
        if extra and self.endpoints:
            base, key, _ = self.endpoints[0]
            self.endpoints += [(base, key, m) for m in extra if (base, key, m) not in self.endpoints]
        if not self.endpoints:
            raise RuntimeError("没有可用的 VLM：设置 REA_TOON_VLM_BASE_URL / _API_KEY / _MODEL")

    def _ask_agent(self, key: str, prompt: str, images: list[Path], cached: Path):
        """The agent's answer to this question if it wrote one, else leave the question for it and stop the run."""
        folder = self.cache_dir.parent / "agent"
        ask, ans = folder / f"{key}.json", folder / f"{key}.answer.json"
        with self._lock:
            folder.mkdir(parents=True, exist_ok=True)
            if folder not in _SWEPT:  # questions of an earlier run that are no longer asked (a prompt or input changed)
                _SWEPT.add(folder)
                for p in folder.glob("*.json"):
                    if not p.name.endswith(".answer.json") and not p.with_name(p.stem + ".answer.json").is_file():
                        p.unlink()
        if ans.is_file():
            try:
                out = parse_json(ans.read_text(encoding="utf-8"))
            except ValueError as exc:
                bad = ans.with_name(f"{key}.bad.txt")
                ans.replace(bad)
                LOG.warning("agent 的答案不是合法 JSON（%s），挪到 %s，重答这一题", exc, bad.name)
            else:
                cached.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                ask.unlink(missing_ok=True)
                ans.unlink()
                return out
        ask.write_text(json.dumps({"key": key, "images": [str(Path(p).resolve()) for p in images], "answer": str(ans),
                                   "prompt": prompt}, ensure_ascii=False, indent=1), encoding="utf-8")
        raise NeedAgent(folder)

    def describe(self) -> str:
        if self.backend == "agent":
            return "agent（当前会话自己看图作答）"
        b, _, m = self.endpoints[0]
        return f"{m} @ {re.sub(r'^https?://', '', b).split('/')[0]}"

    def ask_json(self, prompt: str, images: list[Path], max_tokens: int = 8192, timeout: float | None = None):
        h = hashlib.sha1(prompt.encode())
        for p in images:
            h.update(hashlib.sha1(Path(p).read_bytes()).digest())
        key = h.hexdigest()
        cached = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cached and cached.is_file():
            return json.loads(cached.read_text(encoding="utf-8"))
        if self.backend == "agent":
            return self._ask_agent(key, prompt, images, cached)
        content = []
        for p in images:
            mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
            content.append({"type": "image_url", "image_url": {
                "url": f"data:{mime};base64," + base64.b64encode(Path(p).read_bytes()).decode()}})
        content.append({"type": "text", "text": prompt})
        if not images:  # text-only: some relays reject a part list without an image
            content = prompt
        last: Exception | None = None
        for base, api_key, model in list(self.endpoints):
            if (base, model) in _MISSING:
                continue
            for attempt in range(2):
                try:
                    t = time.time()
                    r = requests.post(f"{base}/chat/completions", timeout=timeout or self.timeout,
                                      headers={"Authorization": f"Bearer {api_key}"},
                                      json={"model": model, "max_tokens": max_tokens, "temperature": 0.2,
                                            "messages": [{"role": "user", "content": content}]})
                    if r.status_code >= 400:
                        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                    text = r.json()["choices"][0]["message"]["content"]
                    out = parse_json(text)
                    LOG.info("[vlm] %s answered in %.0fs", model, time.time() - t)
                    ep = (base, api_key, model)
                    with self._lock:  # the model that answered goes first next time
                        if ep in self.endpoints and self.endpoints[0] != ep:
                            self.endpoints.remove(ep)
                            self.endpoints.insert(0, ep)
                    if cached:
                        cached.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                    return out
                except Exception as exc:
                    last = exc
                    LOG.warning("[vlm] %s attempt %d failed: %s", model, attempt + 1, str(exc)[:200])
                    if "model_not_found" in str(exc):  # the relay has no such model: skip it for the rest of the run
                        _MISSING.add((base, model))
                        break
                    time.sleep(2)
        raise RuntimeError(f"所有 VLM 都失败了：{last}")


def for_work(work: Path, backend: str | None = None) -> VLM:
    """The model client of one work dir, with its answer cache (a re-run with the same prompt and pictures is free)."""
    from . import layout
    return VLM(cache_dir=layout.analysis(work, "vlm_cache"), backend=backend)
