"""Unified API — FastAPI entrypoint.

Exposes every MovieBox-TUI data source as clean, normalized REST endpoints.

Run locally:  uvicorn app.main:app --reload
Swagger UI:   http://localhost:8000/docs
ReDoc:        http://localhost:8000/redoc
OpenAPI JSON: http://localhost:8000/openapi.json
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query

from . import cache
from .models import (
    Channel,
    ListOption,
    MediaDetails,
    MediaItem,
    PlaybackSource,
    SearchResponse,
    StreamsResponse,
    SubtitleTrack,
)
from .providers import fourkhdhub, iptvorg
from .providers.moviebox import MovieBoxClient, MovieBoxError

app = FastAPI(
    title="Unified API",
    summary="One normalized REST API for every MovieBox-TUI data source.",
    description=(
        "Unified proxy for every source used by MovieBox-TUI: the signed "
        "MovieBox (OneRoom) BFF API, the 4KHDHub scraper, and IPTV-org "
        "live-TV feeds.\n\n"
        "All responses are normalized into the models in `app/models.py`, so a "
        "single client implementation can drive the whole app. Browse the "
        "endpoints below — every one can be executed interactively."
    ),
    version="1.0.0",
    contact={"name": "MovieBox-TUI", "url": "https://github.com/mesamirh/MovieBox-Tui"},
    license={"name": "MIT"},
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {
            "name": "System",
            "description": "Service metadata and cache management.",
        },
        {
            "name": "Discovery",
            "description": "Search, suggest, and browse tabs.",
        },
        {
            "name": "Media",
            "description": "Details, streams, subtitles, and playback URLs.",
        },
        {
            "name": "Live TV",
            "description": "IPTV-org categories, languages, countries, and channels.",
        },
    ],
)

_moviebox: Optional[MovieBoxClient] = None


def moviebox() -> MovieBoxClient:
    global _moviebox
    if _moviebox is None:
        _moviebox = MovieBoxClient()
    return _moviebox


def _fourk() -> fourkhdhub.FourKHdHubClient:
    return fourkhdhub.FourKHdHubClient()


PROVIDERS = Literal["moviebox", "fourkhdhub"]


@app.get("/", tags=["System"], summary="Service index")
def root():
    """Top-level index: service identity, version, and links to the docs."""
    return {
        "name": "Unified API",
        "version": app.version,
        "providers": ["moviebox", "fourkhdhub", "iptv-org"],
        "docs": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
        "health": "/health",
    }


@app.get("/health", tags=["System"], summary="Health check")
async def health():
    """Liveness probe; includes the active MovieBox host and 4KHDHub base URL."""
    mb = moviebox()
    return {
        "status": "ok",
        "moviebox_host": mb._active_idx,
        "fourkhdhub_url": fourkhdhub.DEFAULT_BASE_URL,
    }


# ---------------------------------------------------------------------------
# Discovery: search / suggest / discover
# ---------------------------------------------------------------------------
@app.get("/search", response_model=SearchResponse, tags=["Discovery"], summary="Search across providers")
async def search(
    q: str = Query(..., min_length=1, description="Search query", examples=["inception"]),
    provider: Literal["moviebox", "fourkhdhub", "all"] = Query("all", description="Which provider to query"),
    page: int = Query(1, ge=1, description="Result page (1-based)"),
):
    """Search MovieBox and/or 4KHDHub.

    Returns normalized `MediaItem`s. When a provider fails, its error is
    returned in the `502` detail while results from other providers are kept.
    """
    results: list[MediaItem] = []
    errors: list[str] = []
    if provider in ("moviebox", "all"):
        try:
            payload = await moviebox().search(q, page)
            results.extend(moviebox().normalize_search(q, payload))
        except MovieBoxError as exc:
            errors.append(f"moviebox: {exc}")
    if provider in ("fourkhdhub", "all"):
        try:
            results.extend(await _fourk().search(q))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fourkhdhub: {exc}")
    if not results and errors:
        raise HTTPException(status_code=502, detail=errors)
    return SearchResponse(query=q, provider=provider, items=results)


@app.get("/suggest", response_model=SearchResponse, tags=["Discovery"], summary="Typeahead suggestions")
async def suggest(
    q: str = Query(..., min_length=1, description="Partial query", examples=["spider"]),
):
    """MovieBox typeahead suggestions (search endpoint, page 1, top 8)."""
    try:
        payload = await moviebox().suggest(q)
        items = moviebox().normalize_search(q, payload)[:8]
    except MovieBoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return SearchResponse(query=q, provider="moviebox", items=items)


@app.get("/discover", response_model=SearchResponse, tags=["Discovery"], summary="Browse MovieBox tabs")
async def discover(
    tab: Literal["home", "movies", "shows", "anime"] = Query("home", description="Browse tab"),
    page: int = Query(1, ge=1, description="Result page (1-based)"),
):
    """MovieBox browse tabs: `home`, `movies`, `shows`, `anime`."""
    try:
        payload = await moviebox().get_homepage(moviebox_tab_id(tab), page)
        items = moviebox().normalize_homepage(tab, payload)
    except MovieBoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return SearchResponse(query=f"discover:{tab}", provider="moviebox", items=items)


def moviebox_tab_id(tab: str) -> str:
    return {"home": "0", "movies": "2", "shows": "5", "anime": "8"}[tab]


# ---------------------------------------------------------------------------
# Media: details / streams / subtitles / playback
# ---------------------------------------------------------------------------
@app.get(
    "/details/{provider}/{media_id}",
    response_model=MediaDetails,
    tags=["Media"],
    summary="Media details",
)
async def details(provider: PROVIDERS, media_id: str):
    """Full metadata for a movie or series (includes seasons/episodes).

    - `moviebox`: subjectId, e.g. `6391474290696802080`
    - `fourkhdhub`: slug path, e.g. `/dune-movie-195/`
    """
    if provider == "moviebox":
        try:
            payload = await moviebox().get_details(media_id)
        except MovieBoxError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return moviebox().normalize_details(media_id, payload)
    try:
        return await _fourk().details(media_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))


@app.get(
    "/streams/{provider}/{media_id}",
    response_model=StreamsResponse,
    tags=["Media"],
    summary="List available sources",
)
async def streams(
    provider: PROVIDERS,
    media_id: str,
    season: int = Query(0, ge=0, description="Season number (0 = movie / any)"),
    episode: int = Query(0, ge=0, description="Episode number"),
    resolution: Optional[str] = Query(None, description="MovieBox: filter by resolution"),
):
    """All available sources for a movie or a specific episode of a series.

    Each `Stream` carries metadata plus `mirrors` — the actual playable links
    (or resolver URLs for 4KHDHub). Use `/play` to get a resolved URL.
    """
    if provider == "moviebox":
        try:
            payload = await moviebox().get_resources(
                media_id, season, episode, resolution=resolution
            )
        except MovieBoxError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        resolutions = _extract_resolutions(payload)
        return StreamsResponse(
            provider="moviebox",
            id=media_id,
            season=season,
            episode=episode,
            resolutions=resolutions,
            streams=moviebox().normalize_streams(media_id, payload, season, episode),
        )
    try:
        items = await _fourk().releases(media_id, season, episode)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))
    return StreamsResponse(
        provider="fourkhdhub",
        id=media_id,
        season=season,
        episode=episode,
        streams=items,
    )


@app.get(
    "/subtitles/{media_id}",
    response_model=list[SubtitleTrack],
    tags=["Media"],
    summary="Subtitle tracks",
)
async def subtitles(
    media_id: str,
    resource_id: str = Query(..., description="Resource id from /streams"),
):
    """External subtitle tracks for a MovieBox resource."""
    try:
        payload = await moviebox().get_ext_captions(media_id, resource_id)
    except MovieBoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return moviebox().normalize_captions(payload)


@app.get(
    "/play/{provider}/{media_id}",
    response_model=PlaybackSource,
    tags=["Media"],
    summary="Get a playable URL",
)
async def play(
    provider: PROVIDERS,
    media_id: str,
    season: int = Query(0, ge=0, description="Season number"),
    episode: int = Query(0, ge=0, description="Episode number"),
    index: int = Query(0, ge=0, description="Stream index from /streams"),
    resolve: bool = Query(
        True, description="4KHDHub only: run the mirror resolver/preflight chain"
    ),
):
    """Get a directly playable URL (+headers) for a stream.

    - `moviebox`: returns the first mirror's `resolver_url` for the chosen stream.
    - `fourkhdhub`: resolves mirrors (HubCloud/HubDrive → PixelDrain / direct)
      and preflights with a `Range: bytes=0-0` probe until one passes; fails
      with `502` if none is playable.
    """
    if provider == "moviebox":
        try:
            payload = await moviebox().get_resources(media_id, season, episode)
        except MovieBoxError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        streams = moviebox().normalize_streams(media_id, payload, season, episode)
        if index >= len(streams):
            raise HTTPException(status_code=404, detail="stream index out of range")
        stream = streams[index]
        if not stream.mirrors:
            raise HTTPException(status_code=404, detail="stream has no mirrors")
        mirror = stream.mirrors[0]
        return PlaybackSource(
            provider="moviebox",
            url=mirror.resolver_url,
            source_label=mirror.label,
        )
    try:
        items = await _fourk().releases(media_id, season, episode)
        if index >= len(items):
            raise HTTPException(status_code=404, detail="stream index out of range")
        if not resolve:
            mirror = items[index].mirrors[0]
            return PlaybackSource(
                provider="fourkhdhub",
                url=mirror.resolver_url,
                source_label=mirror.label,
            )
        return await _fourk().resolve_release(items[index])
    except fourkhdhub.FourKHdHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def _extract_resolutions(payload) -> list[int]:
    raw = payload.get("collectionResolutions") if isinstance(payload, dict) else None
    resolutions = []
    for col in raw or []:
        value = col.get("resolution") if isinstance(col, dict) else None
        if isinstance(value, (int, float, str)):
            try:
                resolutions.append(int(value))
            except ValueError:
                pass
    resolutions = sorted(set(resolutions), reverse=True)
    return resolutions or [1080, 720, 480, 360]


# ---------------------------------------------------------------------------
# Live TV: IPTV-org
# ---------------------------------------------------------------------------
@app.get(
    "/tv/categories",
    response_model=list[ListOption],
    tags=["Live TV"],
    summary="List IPTV categories",
)
async def tv_categories():
    """All IPTV-org categories (cached 24h)."""
    return await iptvorg.categories()


@app.get(
    "/tv/languages",
    response_model=list[ListOption],
    tags=["Live TV"],
    summary="List IPTV languages",
)
async def tv_languages():
    """All IPTV-org languages (cached 24h)."""
    return await iptvorg.languages()


@app.get(
    "/tv/countries",
    response_model=list[ListOption],
    tags=["Live TV"],
    summary="List IPTV countries",
)
async def tv_countries():
    """All IPTV-org countries (cached 24h)."""
    return await iptvorg.countries()


@app.get(
    "/tv/channels",
    response_model=list[Channel],
    tags=["Live TV"],
    summary="Fetch channels as parsed M3U",
)
async def tv_channels(
    category: Optional[str] = Query(None, description="Category name/code, e.g. `News`"),
    language: Optional[str] = Query(None, description="Language code, e.g. `eng`"),
    country: Optional[str] = Query(None, description="Country code, e.g. `us`"),
    custom_url: Optional[str] = Query(None, description="Any public M3U URL to proxy"),
):
    """Fetch and parse an M3U playlist.

    At least one filter (or `custom_url`) is required. Results are cached 24h.
    """
    if not (category or language or country or custom_url):
        raise HTTPException(
            status_code=400,
            detail="provide category, language, country, or custom_url",
        )
    try:
        return await iptvorg.channels(
            category=category, language=language, country=country, custom_url=custom_url
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------
@app.delete(
    "/cache/{namespace}",
    tags=["System"],
    summary="Clear the response cache",
)
async def clear_cache(namespace: Optional[str] = None):
    """Clear all cached responses, or only those for a namespace.

    Namespaces: `fourkhdhub`, `iptv-org` (and the MovieBox session).
    """
    cache.clear(namespace)
    return {"cleared": namespace or "all"}
