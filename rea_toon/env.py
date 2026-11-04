"""Load API settings from a .env file without overriding variables already set."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_FILES = (
    Path.cwd() / ".env",
    Path(__file__).resolve().parent.parent / ".env",
    Path.home() / "code/github/capsule-cinema/.env",
)


def load_env(path: str | Path | None = None) -> list[Path]:
    """Read KEY=VALUE lines. Explicit path first, then the defaults. Returns files read."""
    files = [Path(path).expanduser()] if path else list(DEFAULT_ENV_FILES)
    used: list[Path] = []
    for f in files:
        if not f.is_file():
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
    return used


def need(*names: str) -> str:
    """First non-empty env var among names, or raise with a clear message."""
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    raise RuntimeError(f"缺少环境变量：{' 或 '.join(names)}（可用 --env-file 指定 .env）")
