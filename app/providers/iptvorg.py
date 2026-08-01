"""IPTV-org provider: category/language/country metadata + M3U playlist parsing.

Instead of hardcoding ~1500 languages, the metadata is proxied from iptv-org's own
public JSON index (`https://iptv-org.github.io/api/*.json`) which is always
up-to-date. Playlists are the same M3U feeds MovieBox-TUI uses, parsed with the
same logic (`src/providers/iptv_org/m3u.rs`).
"""
from __future__ import annotations

import re
from typing import Optional

import httpx

from .. import cache
from ..models import Channel, ListOption

API_ROOT = "https://iptv-org.github.io"
PLAYLIST_URLS = {
    "categories": f"{API_ROOT}/iptv/categories/{{name}}.m3u",
    "languages": f"{API_ROOT}/iptv/languages/{{code}}.m3u",
    "countries": f"{API_ROOT}/iptv/countries/{{code}}.m3u",
}

_http = httpx.AsyncClient(timeout=30.0, follow_redirects=True)


async def categories() -> list[ListOption]:
    return await _list_options("categories")


async def languages() -> list[ListOption]:
    return await _list_options("languages")


async def countries() -> list[ListOption]:
    return await _list_options("countries")


async def _list_options(kind: str) -> list[ListOption]:
    cached = cache.get_json("iptv_org", 24 * 3600, kind)
    if cached is not None:
        return [ListOption(**item) for item in cached]
    resp = await _http.get(f"{API_ROOT}/api/{kind}.json")
    resp.raise_for_status()
    options = []
    for row in resp.json():
        code = row.get("code") or row.get("name") or ""
        name = row.get("name") or code
        if code:
            options.append(ListOption(name=name, code=code))
    cache.set_json("iptv_org", [o.model_dump() for o in options], kind)
    return options


async def channels(
    category: Optional[str] = None,
    language: Optional[str] = None,
    country: Optional[str] = None,
    custom_url: Optional[str] = None,
) -> list[Channel]:
    urls: list[str] = []
    if custom_url:
        urls.append(custom_url)
    if category:
        urls.append(PLAYLIST_URLS["categories"].format(name=category.lower()))
    if language:
        urls.append(PLAYLIST_URLS["languages"].format(code=language.lower()))
    if country:
        urls.append(PLAYLIST_URLS["countries"].format(code=country.lower()))

    seen: set[str] = set()
    all_channels: list[Channel] = []
    for url in urls:
        for channel in await _fetch_playlist(url):
            if channel.stream_url not in seen:
                seen.add(channel.stream_url)
                all_channels.append(channel)
    return all_channels


async def _fetch_playlist(url: str) -> list[Channel]:
    cached = cache.get_json("iptv_org", 24 * 3600, "playlist", url)
    if cached is not None:
        return [Channel(**item) for item in cached]
    resp = await _http.get(url)
    resp.raise_for_status()
    content = resp.text
    channels = _parse_m3u(content)
    cache.set_json("iptv_org", [c.model_dump() for c in channels], "playlist", url)
    return channels


def _parse_m3u(content: str) -> list[Channel]:
    channels: list[Channel] = []
    current: Optional[Channel] = None

    def attr(line: str, name: str) -> str:
        match = re.search(name + r'="([^"]*)"', line)
        return match.group(1) if match else ""

    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            current = Channel(
                id=attr(line, "tvg-id"),
                name=line.rsplit(",", 1)[-1].strip(),
                logo=attr(line, "tvg-logo"),
                group=attr(line, "group-title"),
                stream_url="",
            )
        elif not line.startswith("#"):
            if current is not None:
                current.stream_url = line
                if not current.id:
                    current.id = current.name
                channels.append(current)
                current = None
    return channels
