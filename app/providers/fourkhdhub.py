"""4KHDHub provider — HTML scraping (no official API), mirroring MovieBox-TUI's
`src/providers/fourkhdhub/` logic: search, details, releases, and the full
HubCloud / HubDrive / PixelDrain mirror-resolution + preflight chain.
"""
from __future__ import annotations

import ipaddress
import os
import re
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .. import cache
from ..models import (
    Episode,
    MediaDetails,
    MediaItem,
    Season,
    Stream,
    SourceMirror,
    PlaybackSource,
)

DEFAULT_BASE_URL = "https://4khdhub.one/"
PIXELDRAIN_PREFIX = "https://pixeldrain.dev/u/"
KNOWN_GENRES = {
    "action", "adventure", "animation", "comedy", "crime", "documentary", "drama",
    "family", "fantasy", "history", "horror", "music", "mystery", "romance",
    "science fiction", "sci-fi", "thriller", "war", "western",
}


class FourKHdHubError(Exception):
    pass


class FourKHdHubClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("MOVIEBOX_FOURKHDHUB_URL") or DEFAULT_BASE_URL).rstrip("/") + "/"
        if urlsplit(self.base_url).scheme != "https":
            raise FourKHdHubError(f"base URL must be https: {self.base_url}")
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    # ---- core page fetching ------------------------------------------------
    async def _fetch_text(self, url: str) -> str:
        resp = await self._http.get(url)
        resp.raise_for_status()
        return resp.text

    def _provider_url(self, id_or_slug: str) -> str:
        slug = id_or_slug.lstrip("/")
        url = urljoin(self.base_url, slug)
        if urlsplit(url).hostname != urlsplit(self.base_url).hostname:
            raise FourKHdHubError(f"invalid id: {id_or_slug}")
        return url

    # ---- search -------------------------------------------------------------
    async def search(self, query: str) -> list[MediaItem]:
        url = f"{self.base_url}?s={query}"
        html = await self._fetch_text(url)
        return self._parse_search(html)

    def _parse_search(self, html: str) -> list[MediaItem]:
        soup = BeautifulSoup(html, "html.parser")
        items: list[MediaItem] = []
        for card in soup.select("a.movie-card"):
            href = card.get("href")
            if not href:
                continue
            url = urljoin(self.base_url, href)
            if urlsplit(url).hostname != urlsplit(self.base_url).hostname:
                continue
            title = _text(card.select_one(".movie-card-title"))
            if not title:
                continue
            meta = _text(card.select_one(".movie-card-meta"))
            media_type = "series" if "-series-" in href else "movie"
            img = card.find("img")
            items.append(
                MediaItem(
                    provider="fourkhdhub",
                    id=urlsplit(url).path,
                    title=title,
                    media_type=media_type,
                    year=_first_four_digit_year(meta),
                    poster_url=img.get("src") if img else None,
                    season_count=_parse_season_count(meta),
                )
            )
        return items

    # ---- details ------------------------------------------------------------
    async def details(self, id_or_slug: str) -> MediaDetails:
        html = await self._fetch_text(self._provider_url(id_or_slug))
        return self._parse_details(id_or_slug, html)

    def _parse_details(self, id_or_slug: str, html: str) -> MediaDetails:
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.find("h1")
        og_title = _meta_content(soup, 'meta[property="og:title"]')
        raw_title = _text(h1) or og_title
        if not raw_title:
            raise FourKHdHubError("title missing")
        title = _strip_trailing_year(raw_title)
        media_type = "series" if "-series-" in id_or_slug else "movie"
        description = _text(soup.select_one(".content-section p.mt-4")) or _meta_content(
            soup, 'meta[name="description"]'
        )
        tagline = _text(soup.select_one(".movie-tagline"))
        imdb = _text(soup.select_one(".imdb-score"))
        poster = _meta_content(soup, 'meta[property="og:image"]')
        year = (
            _first_four_digit_year(self._metadata(soup, "Release:"))
            or _first_four_digit_year(self._metadata(soup, "Last Air:"))
            or _first_four_digit_year(raw_title)
        )
        genres = [
            g
            for g in (_text(node) for node in soup.select(".badge-outline a"))
            if g and g.lower() in KNOWN_GENRES
        ]
        return MediaDetails(
            provider="fourkhdhub",
            id=id_or_slug,
            title=title,
            media_type=media_type,
            year=year,
            description=description,
            tagline=tagline,
            imdb_rating=imdb,
            director=self._metadata(soup, "Director:"),
            stars=self._metadata(soup, "Stars:"),
            prints=self._metadata(soup, "Prints:") or self._metadata(soup, "Print:"),
            audios=self._metadata(soup, "Audios:"),
            poster_url=poster,
            genres=genres,
            seasons=self._parse_seasons(soup),
        )

    def _metadata(self, soup: BeautifulSoup, label: str) -> Optional[str]:
        for item in soup.select(".metadata-item"):
            lab = _text(item.select_one(".metadata-label"))
            if lab == label:
                return _text(item.select_one(".metadata-value"))
        return None

    def _parse_seasons(self, soup: BeautifulSoup) -> list[Season]:
        seasons: dict[int, dict[int, Episode]] = {}
        for node in soup.select("#episodes .episode-download-item"):
            filename = _text(node.select_one(".episode-file-title"))
            parsed = _parse_season_episode(filename)
            if not parsed:
                continue
            se, ep = parsed
            seasons.setdefault(se, {}).setdefault(ep, Episode(season=se, number=ep))
        return [
            Season(number=n, episodes=[seasons[n][e] for e in sorted(seasons[n])])
            for n in sorted(seasons)
        ]

    # ---- releases -----------------------------------------------------------
    async def releases(self, id_or_slug: str, season: int = 0, episode: int = 0) -> list[Stream]:
        html = await self._fetch_text(self._provider_url(id_or_slug))
        return self._parse_releases(html, season, episode)

    def _parse_releases(self, html: str, season: int = 0, episode: int = 0) -> list[Stream]:
        soup = BeautifulSoup(html, "html.parser")
        item_sel = "#episodes .episode-download-item" if season > 0 else ".download-item"
        title_sel = ".episode-file-title" if season > 0 else ".file-title"
        page_language = self._metadata(soup, "Audios:")
        grouped: dict[str, Stream] = {}

        for item in soup.select(item_sel):
            filename = _text(item.select_one(title_sel))
            if not filename or _is_archive(filename):
                continue
            parsed = _parse_season_episode(filename)
            if season > 0 and parsed != (season, episode):
                continue
            mirrors = []
            for link in item.select("a[href]"):
                href = link.get("href") or ""
                if not href.startswith("https://") or "logout" in href:
                    continue
                label = _text(link) or "Source"
                mirrors.append(
                    SourceMirror(
                        label=label,
                        resolver_url=href,
                        direct_file="hubcloud." not in href and "hubdrive." not in href,
                    )
                )
            if not mirrors:
                continue
            size_text = next(
                (
                    _text(node)
                    for node in item.select(".badge-size, .badge")
                    if _text(node) and _parse_size_bytes(_text(node))
                ),
                None,
            )
            key = _normalize_filename(filename)
            if key not in grouped:
                lang = _detect_language(filename) or page_language
                grouped[key] = Stream(
                    provider="fourkhdhub",
                    filename=filename,
                    quality=_detect_quality(filename),
                    codec=_detect_codec(filename),
                    language=lang if lang and lang.lower() not in {"n/a", "unknown"} else None,
                    size_bytes=_parse_size_bytes(size_text),
                    season=parsed[0] if parsed else None,
                    episode=parsed[1] if parsed else None,
                    mirrors=[],
                )
            for mirror in mirrors:
                if not any(m.resolver_url == mirror.resolver_url for m in grouped[key].mirrors):
                    grouped[key].mirrors.append(mirror)

        streams = sorted(
            grouped.values(),
            key=lambda s: (s.quality is None, int(s.quality.rstrip("p")) if s.quality else 0),
            reverse=True,
        )
        return streams

    # ---- mirror resolution -> playable URL ----------------------------------
    async def resolve_release(self, stream: Stream) -> PlaybackSource:
        for mirror in stream.mirrors:
            try:
                if "hubcloud." in mirror.resolver_url:
                    candidates = await _resolve_hubcloud(self._http, mirror.resolver_url)
                elif "hubdrive." in mirror.resolver_url:
                    candidates = await _resolve_hubdrive(self._http, mirror.resolver_url)
                else:
                    validate_playback_url(mirror.resolver_url)
                    candidates = [(mirror.resolver_url, mirror.label)]
                for url, label in candidates:
                    playable = await self._preflight(url)
                    return PlaybackSource(
                        provider="fourkhdhub",
                        url=playable,
                        source_label=label,
                    )
            except (FourKHdHubError, httpx.HTTPError):
                continue
        raise FourKHdHubError("no playable mirror resolved")

    async def _probe(self, url: str, headers: dict[str, str]) -> tuple[str, str]:
        async with self._http.stream("GET", url, headers=headers) as resp:
            status = resp.status_code
            if status < 200 or status >= 400:
                await resp.aclose()
                resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            final = str(resp.url)
            try:
                async for _chunk in resp.aiter_bytes():
                    break
            except Exception:
                pass
        return final, content_type

    async def _preflight(self, url: str) -> str:
        validate_playback_url(url)
        headers = {"Range": "bytes=0-0"}
        final, content_type = await self._probe(url, headers)
        validate_playback_url(final)
        if _is_invalid_content_type(content_type):
            wrapped = _link_query_param(final)
            validate_playback_url(wrapped)
            final2, content_type2 = await self._probe(wrapped, headers)
            final = final2
            validate_playback_url(final)
            if _is_invalid_content_type(content_type2):
                raise FourKHdHubError(f"invalid wrapped media content type: {content_type2}")
        return final

    async def health_check(self) -> bool:
        try:
            resp = await self._http.get(self.base_url)
            return resp.status_code < 400
        except httpx.HTTPError:
            return False


# ---- mirror resolution helpers (hubcloud.rs) ----------------------------------
def validate_playback_url(raw: str) -> str:
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname:
        raise FourKHdHubError(f"invalid URL: {raw}")
    host = parts.hostname.lower()
    path = parts.path.lower()
    if (
        host == "localhost"
        or host.endswith(".local")
        or _is_non_public_ip(host)
        or path.endswith(".zip")
        or "login.php" in path
        or "logout" in path
    ):
        raise FourKHdHubError(f"invalid URL: {raw}")
    return raw


def _is_non_public_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global


def _is_invalid_content_type(content_type: str) -> bool:
    return any(t in content_type for t in ("text/html", "application/zip", "text/plain"))


def _link_query_param(url: str) -> str:
    values = parse_qs(urlsplit(url).query).get("link", [])
    for value in values:
        if value.startswith("https://"):
            return value
    raise FourKHdHubError(f"invalid media content type: {url}")


def _score(url: str, label: str) -> int:
    value = f"{url} {label}".lower()
    if "pixeldrain" in value or "pixel.hubcloud" in value:
        return 0
    if "gpdl." in value or "googleusercontent" in value:
        return 1
    if "workers.dev" in value or "r2.dev" in value:
        return 2
    if "latent.click" in value or "fsl" in value:
        return 3
    return 4


def _pixeldrain_api_url(raw: str) -> Optional[str]:
    parts = urlsplit(raw)
    if parts.hostname != "pixeldrain.dev":
        return None
    file_id = parts.path.removeprefix("/u/").strip("/")
    if not file_id or not re.fullmatch(r"[A-Za-z0-9_-]+", file_id):
        return None
    return f"https://pixeldrain.dev/api/file/{file_id}?download"


def _extract_script_pixeldrain_urls(html: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"var\s+pxl", html):
        rest = html[match.start():]
        idx = rest.find(PIXELDRAIN_PREFIX)
        if idx == -1:
            continue
        candidate = rest[idx:]
        end = re.search(r'["\'\s]', candidate)
        end = end.start() if end else len(candidate)
        api = _pixeldrain_api_url(candidate[:end])
        if api and api not in urls:
            urls.append(api)
    return urls


async def _resolve_hubcloud(client: httpx.AsyncClient, drive_url: str) -> list[tuple[str, str]]:
    parts = urlsplit(drive_url)
    if parts.scheme != "https" or not parts.hostname.startswith("hubcloud.") or not parts.path.startswith("/drive/"):
        raise FourKHdHubError(f"invalid resolver URL: {drive_url}")
    drive_html = (await client.get(drive_url)).text
    soup = BeautifulSoup(drive_html, "html.parser")
    sportverse = next(
        (a.get("href") for a in soup.select("a#download") if (a.get("href") or "").startswith("https://sportverse.")),
        None,
    )
    if not sportverse:
        raise FourKHdHubError("HubCloud resolver link missing")

    resolver_html = (await client.get(sportverse)).text
    resolver_soup = BeautifulSoup(resolver_html, "html.parser")
    candidates: list[tuple[int, str, str]] = [
        (0, url, "PixelDrain") for url in _extract_script_pixeldrain_urls(resolver_html)
    ]
    for a in resolver_soup.select("a[href]"):
        href = a.get("href") or ""
        label = a.get_text(strip=True)
        try:
            validate_playback_url(href)
        except FourKHdHubError:
            continue
        url = _pixeldrain_api_url(href) or href
        candidates.append((_score(url, label), url, label or "Direct"))
    candidates.sort(key=lambda c: c[0])
    resolved: list[tuple[str, str]] = []
    for _, url, label in candidates:
        if not any(existing == url for existing, _ in resolved):
            resolved.append((url, label))
    return resolved


async def _resolve_hubdrive(client: httpx.AsyncClient, drive_url: str) -> list[tuple[str, str]]:
    parts = urlsplit(drive_url)
    if parts.scheme != "https" or not parts.hostname.startswith("hubdrive.") or not parts.path.startswith("/file/"):
        raise FourKHdHubError(f"invalid hubdrive URL: {drive_url}")
    html = (await client.get(drive_url)).text
    soup = BeautifulSoup(html, "html.parser")
    hubcloud_url = None
    for a in soup.select("a[href]"):
        raw = a.get("href") or ""
        url_parts = urlsplit(raw)
        if url_parts.hostname and url_parts.hostname.startswith("hubcloud.") and url_parts.path.startswith("/drive/"):
            hubcloud_url = raw
            break
    if not hubcloud_url:
        raise FourKHdHubError("HubDrive HubCloud mirror missing")
    return await _resolve_hubcloud(client, hubcloud_url)


# ---- filename parsing helpers (parser.rs) -------------------------------------
def _text(node) -> Optional[str]:
    if node is None:
        return None
    text = " ".join(node.get_text(strip=True).split())
    return text or None


def _meta_content(soup: BeautifulSoup, selector: str) -> Optional[str]:
    node = soup.select_one(selector)
    return node.get("content") if node else None


def _first_four_digit_year(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"(?:^|\D)(1\d{3}|2\d{3})(?:\D|$)", value)
    return match.group(1) if match else None


def _strip_trailing_year(value: str) -> str:
    return re.sub(r"\s*\(\d{4}\)\s*$", "", value).strip()


def _parse_season_count(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    match = re.search(r"S(\d+)", value)
    return int(match.group(1)) if match else None


def _parse_season_episode(value: Optional[str]) -> Optional[tuple[int, int]]:
    if not value:
        return None
    match = re.search(r"[Ss](\d+)[Ee](\d+)", value)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _parse_size_bytes(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    value = value.replace(" ", "").upper()
    multipliers = [("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)]
    for suffix, mult in multipliers:
        if value.endswith(suffix):
            try:
                return int(float(value.removesuffix(suffix)) * mult)
            except ValueError:
                return None
    return None


def _detect_quality(value: str) -> Optional[str]:
    lower = value.lower()
    for q in ("2160p", "1080p", "720p", "480p"):
        if q in lower:
            return q
    return None


def _detect_codec(value: str) -> Optional[str]:
    lower = value.lower()
    if "av1" in lower:
        return "AV1"
    if any(t in lower for t in ("h.265", "h265", "x265")):
        return "H.265"
    if "hevc" in lower:
        return "HEVC"
    if any(t in lower for t in ("h.264", "h264", "x264")):
        return "H.264"
    if "remux" in lower:
        return "REMUX"
    return None


def _detect_language(value: str) -> Optional[str]:
    lower = value.lower()
    hindi, english = "hindi" in lower, "english" in lower
    if hindi and english:
        return "Hindi, English"
    if hindi:
        return "Hindi"
    if english:
        return "English"
    if "dual audio" in lower:
        return "Dual Audio"
    if "multi audio" in lower or "multi-audio" in lower:
        return "Multi Audio"
    return None


def _is_archive(value: str) -> bool:
    lower = value.lower()
    return lower.endswith(".zip") or "complete season" in lower or "season pack" in lower


def _normalize_filename(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())
