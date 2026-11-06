"""OpenAI-compatible multimodal chat (Gemini / GPT-4o / Qwen-VL ...) returning JSON, with a disk cache."""

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


class VLM:
    """Config from env: REA_TOON_VLM_* first, then capsule-cinema's VIDEO_ANALYSIS_* / GEMINI3_* / CREW_*."""

    PREFIXES = (("REA_TOON_VLM", "REA_TOON_VLM_MODEL"), ("REA_VLM", "REA_VLM_MODEL"),
                ("GEMINI3", "GEMINI3_MODEL_NAME"), ("VIDEO_ANALYSIS", "VIDEO_ANALYSIS_MODEL_NAME"),
                ("CREW", "CREW_MODEL_NAME"))

    def __init__(self, cache_dir: Path | None = None, timeout: float = 300):
        self.endpoints: list[tuple[str, str, str]] = []
        for prefix, mkey in self.PREFIXES:
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
        self._lock = threading.Lock()
        self.cache_dir = cache_dir
        self.timeout = timeout
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def describe(self) -> str:
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
                    time.sleep(2)
        raise RuntimeError(f"所有 VLM 都失败了：{last}")
