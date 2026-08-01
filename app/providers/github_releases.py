"""GitHub Releases proxy — mirrors `src/tui/updater.rs` (update checker)."""
from __future__ import annotations

import os
from typing import Any

import httpx

from .. import cache

OWNER = "mesamirh"
REPOSITORY = "MovieBox-Tui"
URL = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/releases?per_page=20"


async def latest_releases(per_page: int = 20) -> list[dict[str, Any]]:
    url = URL if per_page == 20 else f"{URL.split('?')[0]}?per_page={per_page}"
    cached = cache.get_json("github", 3600, "releases", str(per_page))
    if cached is not None:
        return cached
    headers = {"User-Agent": "MovieBox-Tui"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    cache.set_json("github", data, "releases", str(per_page))
    return data


async def latest_version() -> str | None:
    releases = await latest_releases()
    for release in releases:
        tag = release.get("tag_name")
        if tag:
            return tag.lstrip("v")
    return None
