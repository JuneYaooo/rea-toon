"""Input-hash memo so only artifacts whose inputs changed get regenerated (hypit-style incremental builds).

Each output file has a sidecar `<file>.meta.json` with the hash of everything that produced it:
prompt text, engine, parameters and the bytes of every input file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def file_digest(p: Path) -> str:
    return hashlib.sha1(Path(p).read_bytes()).hexdigest()[:16]


def input_key(**parts) -> str:
    norm = {}
    for k, v in sorted(parts.items()):
        if isinstance(v, Path):
            v = file_digest(v) if v.is_file() else f"missing:{v}"
        elif isinstance(v, (list, tuple)):
            v = [file_digest(x) if isinstance(x, Path) and x.is_file() else str(x) for x in v]
        norm[k] = v
    return hashlib.sha1(json.dumps(norm, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:20]


def meta_path(out: Path) -> Path:
    return out.with_name(out.name + ".meta.json")


def fresh(out: Path, key: str) -> bool:
    m = meta_path(out)
    if not (out.is_file() and m.is_file()):
        return False
    try:
        return json.loads(m.read_text(encoding="utf-8")).get("key") == key
    except (OSError, json.JSONDecodeError):
        return False


def stamp(out: Path, key: str, **info) -> None:
    meta_path(out).write_text(json.dumps({"key": key, **info}, ensure_ascii=False, indent=2), encoding="utf-8")
