"""Bring a reference video into a work dir: local file, direct URL, or a Douyin/XHS share text."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path

import requests

from .media import probe

LOG = logging.getLogger(__name__)
PARSE_API = "https://api.xiaolvfang.com/parseVideoUrl"


def _share_url(text: str) -> str | None:
    m = re.search(r"https?://[^\s，。]+", text)
    return m.group(0) if m else None


def _parse_share(url: str) -> dict:
    token = os.getenv("XIAOLVFANG_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("解析分享链接需要 XIAOLVFANG_API_TOKEN；或先手动下载视频再传本地路径")
    r = requests.post(PARSE_API, json={"url": url, "token": token}, timeout=60)
    r.raise_for_status()
    d = r.json()
    if not d.get("success"):
        raise RuntimeError(f"分享链接解析失败：{d.get('message')}")
    return d.get("data") or {}


def ingest(source: str, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    dst = work / "source.mp4"
    meta: dict = {"input": source}
    local = Path(source).expanduser()
    if local.is_file():
        if local.resolve() != dst.resolve():
            shutil.copy2(local, dst)
    else:
        url = _share_url(source)
        if not url:
            raise RuntimeError(f"既不是本地文件也找不到链接：{source}")
        from . import douyin
        known = douyin.PLAY.get(douyin.video_id(url) or "")
        if re.search(r"\.(mp4|mov|webm)(\?|$)", url):
            video_url = url
        elif known and known.get("url"):  # an episode of a collection: the play address is already known
            video_url = known["url"]
            meta.update(title=known.get("title"), author=known.get("author"), share_link=url, likes=known.get("likes"),
                        episode=known.get("episode"))
        else:
            data = _parse_share(url)
            video_url = data.get("video_url") or data.get("videoUrls")
            if isinstance(video_url, list):
                video_url = next((u for u in video_url if isinstance(u, str) and u), None)
            if not video_url:
                raise RuntimeError(f"分享链接解析没有返回视频地址（可能是图文或已删除）：{url}")
            meta.update(title=data.get("title"), author=data.get("nickname"), share_link=url,
                        likes=data.get("liked_count"))
        LOG.info("下载 %s", video_url[:80])
        with requests.get(video_url, stream=True, timeout=300, headers={"User-Agent": "Mozilla/5.0"}) as r:
            r.raise_for_status()
            with dst.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    meta.update(probe(dst))
    (work / "source.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
