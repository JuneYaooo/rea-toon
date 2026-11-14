"""Load API settings from .env files without overriding variables already set.

The defaults are ./.env and the repo's own .env (git-ignored). Settings kept in another project's .env are reached by
naming it there: REA_TOON_ENV_FILES=<path>[:<path>…] (read after the file that names it)."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_FILES = (
    Path.cwd() / ".env",
    Path(__file__).resolve().parent.parent / ".env",
)


def load_env(path: str | Path | None = None) -> list[Path]:
    """Read KEY=VALUE lines. Explicit path first, then the defaults. Returns files read."""
    files = [Path(path).expanduser()] if path else list(DEFAULT_ENV_FILES)
    used: list[Path] = []
    while files:
        f = files.pop(0)
        if not f.is_file() or f.resolve() in {u.resolve() for u in used}:
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        used.append(f)
        if not files:  # the files the ones read so far point to
            files = [Path(x).expanduser() for x in os.getenv("REA_TOON_ENV_FILES", "").split(os.pathsep) if x.strip()]
    return used


def need(*names: str) -> str:
    """First non-empty env var among names, or raise with a clear message."""
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    raise RuntimeError(f"缺少环境变量：{' 或 '.join(names)}（可用 --env-file 指定 .env）")
