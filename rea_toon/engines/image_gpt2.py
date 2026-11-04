"""gpt-image-2 over an OpenAI-compatible Images API (generations + edits).

Channels are picked by name; each maps to env vars holding base URL and key.
Default channel "cyber" = the Chybenzun relay used by capsule-cinema.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import mimetypes
import os
import time
from pathlib import Path

import requests

from ..env import need

LOG = logging.getLogger(__name__)

CHANNELS = {
    "cyber": ("CHYBENZUN_GPT_IMAGE2_BASE_URL", "CHYBENZUN_GPT_IMAGE2_API_KEY"),
    "krill": ("KRILL_GPT_IMAGE2_BASE_URL", "KRILL_GPT_IMAGE2_API_KEY"),
    "juling": ("JULING_GPT_IMAGE2_BASE_URL", "JULING_GPT_IMAGE2_API_KEY"),
    "openai": ("OPENAI_BASE_URL", "OPENAI_API_KEY"),
}
SIZES = {"16:9": "1536x864", "9:16": "864x1536", "1:1": "1024x1024", "4:3": "1536x1152", "3:4": "1152x1536"}


def _endpoint(base: str, kind: str) -> str:
    base = base.rstrip("/")
    return f"{base}/images/{kind}" if base.endswith("/v1") else f"{base}/v1/images/{kind}"


class GptImage2:
    def __init__(self, channel: str | None = None, quality: str = "high", timeout: float = 360):
        self.channel = channel or os.getenv("REA_TOON_IMAGE_CHANNEL", "cyber")
        if self.channel not in CHANNELS:
            raise ValueError(f"未知图片渠道 {self.channel}；可选：{', '.join(CHANNELS)}")
        base_env, key_env = CHANNELS[self.channel]
        self.base = need(base_env) if self.channel != "openai" else (os.getenv(base_env) or "https://api.openai.com/v1")
        self.key = need(key_env)
        self.quality = quality
        self.timeout = timeout

    def describe(self) -> str:
        return f"gpt-image-2@{self.channel}"

    def generate(self, prompt: str, out: Path, aspect: str = "16:9", refs: list[Path] | None = None,
                 retries: int = 3) -> Path:
        """Text-to-image, or image edit when refs are given (refs[0] is the main image)."""
        out.parent.mkdir(parents=True, exist_ok=True)
        size = SIZES.get(aspect, "1536x864")
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                t = time.time()
                data = self._edit(prompt, refs, size) if refs else self._gen(prompt, size)
                self._save(data, out)
                LOG.info("[image] %s ok %.0fs (%d refs)", out.name, time.time() - t, len(refs or []))
                return out
            except Exception as exc:  # network errors, 5xx, moderation, bad payloads
                last = exc
                LOG.warning("[image] %s attempt %d failed: %s", out.name, attempt, str(exc)[:300])
                time.sleep(4 * attempt)
        raise RuntimeError(f"gpt-image-2 生成失败：{last}")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"}

    def _gen(self, prompt: str, size: str) -> dict:
        body = {"model": "gpt-image-2", "prompt": prompt, "size": size, "quality": self.quality,
                "n": 1, "response_format": "b64_json"}
        r = requests.post(_endpoint(self.base, "generations"), json=body, headers=self._headers(), timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    def _edit(self, prompt: str, refs: list[Path], size: str) -> dict:
        data = {"model": "gpt-image-2", "prompt": prompt, "size": size, "quality": self.quality,
                "n": "1", "response_format": "b64_json"}
        with contextlib.ExitStack() as stack:
            files = []
            for p in refs:
                p = Path(p)
                mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
                files.append(("image[]", (p.name, stack.enter_context(p.open("rb")), mime)))
            r = requests.post(_endpoint(self.base, "edits"), data=data, files=files,
                              headers=self._headers(), timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    @staticmethod
    def _save(payload: dict, out: Path) -> None:
        items = payload.get("data") or []
        if not items:
            raise RuntimeError(f"接口未返回图片：{str(payload)[:300]}")
        item = items[0]
        if item.get("b64_json"):
            raw = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            resp = requests.get(item["url"], timeout=120)
            resp.raise_for_status()
            raw = resp.content
        else:
            raise RuntimeError("图片结果既没有 b64_json 也没有 url")
        tmp = out.with_suffix(".part")
        tmp.write_bytes(raw)
        # normalise to the requested extension (png/jpg) through Pillow
        from PIL import Image
        with Image.open(tmp) as im:
            im.convert("RGB").save(out, quality=95)
        tmp.unlink(missing_ok=True)
