"""Simple TTL file cache (mirrors the MovieBox-TUI caching layer)."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

CACHE_ROOT = Path(os.environ.get("UNIFIED_API_CACHE_DIR", tempfile.gettempdir())) / "unified-api"


def _key(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.md5(raw.encode()).hexdigest()


def get_json(namespace: str, ttl_seconds: float, *key_parts: str):
    """Return cached JSON if fresh, else None."""
    path = CACHE_ROOT / namespace / f"{_key(*key_parts)}.json"
    try:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > ttl_seconds:
            path.unlink(missing_ok=True)
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def set_json(namespace: str, value, *key_parts: str) -> None:
    path = CACHE_ROOT / namespace
    path.mkdir(parents=True, exist_ok=True)
    tmp = path / f"{_key(*key_parts)}.json.tmp"
    dst = path / f"{_key(*key_parts)}.json"
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(dst)
    except OSError:
        pass


def clear(namespace: str | None = None) -> None:
    if namespace:
        target = CACHE_ROOT / namespace
        if target.exists():
            import shutil

            shutil.rmtree(target, ignore_errors=True)
    else:
        import shutil

        shutil.rmtree(CACHE_ROOT, ignore_errors=True)
