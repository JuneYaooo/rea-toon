"""Douyin collections (合集): a series' episodes in order, with their play addresses, from the public mix list.

A collection link expands to one video page per episode; the play addresses are remembered for this run so ingest can
download them straight away (the share-link parser often fails on official episodes)."""

from __future__ import annotations

import logging
import re

import requests

LOG = logging.getLogger(__name__)
MIX_API = "https://www.iesdouyin.com/web/api/mix/item/list/"
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"}
PLAY: dict[str, dict] = {}  # aweme id -> {"url", "title", "author", "likes", "episode"}


def mix_id(text: str) -> str | None:
    m = re.search(r"douyin\.com/(?:collection|mix)/(\d+)", text)
    return m.group(1) if m else None


def video_id(text: str) -> str | None:
    m = re.search(r"douyin\.com/(?:video|share/video)/(\d+)", text)
    return m.group(1) if m else None


def episodes(mid: str, page: int = 20) -> list[str]:
    """Video page URLs of a collection, episode 1 first."""
    items, cursor = [], 0
    while True:
        r = requests.get(MIX_API, params={"mix_id": mid, "count": page, "cursor": cursor}, headers=UA, timeout=30)
        r.raise_for_status()
        d = r.json()
        items += d.get("aweme_list") or []
        if not d.get("has_more") or not d.get("aweme_list"):
            break
        cursor = d.get("cursor") or cursor + page
    out = []
    for a in items:
        ep = ((a.get("mix_info") or {}).get("statis") or {}).get("current_episode") or 0
        urls = ((a.get("video") or {}).get("play_addr") or {}).get("url_list") or []
        PLAY[a["aweme_id"]] = {"url": urls[0] if urls else "", "title": (a.get("desc") or "")[:80],
                               "author": (a.get("author") or {}).get("nickname"),
                               "likes": (a.get("statistics") or {}).get("digg_count"), "episode": ep}
        out.append((ep, f"https://www.douyin.com/video/{a['aweme_id']}"))
    out.sort(key=lambda x: x[0])
    LOG.info("抖音合集 %s：%d 集", mid, len(out))
    return [u for _, u in out]
