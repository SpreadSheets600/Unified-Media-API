"""MovieBox (OneRoom) BFF provider.

Replicates the full request-signing / device-spoofing / host-failover logic of
MovieBox-TUI (`src/providers/moviebox/`) so the private mobile API can be called
directly as a normal REST API.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import random
import time
import uuid
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from .. import cache
from ..models import (
    Episode,
    MediaDetails,
    MediaItem,
    Season,
    Stream,
    SourceMirror,
    SubtitleTrack,
)

# --- constants (mirror of crypto.rs / client.rs) -------------------------------
HOST_POOL = [
    "https://api6.aoneroom.com",
    "https://api5.aoneroom.com",
    "https://api4.aoneroom.com",
    "https://api4sg.aoneroom.com",
    "https://api3.aoneroom.com",
    "https://api6sg.aoneroom.com",
    "https://api.inmoviebox.com",
]
RETRY_STATUS_CODES = {403, 406, 407, 429, 500, 502, 503, 504}
SECRET_KEY_DEFAULT = "76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O"
SIGNATURE_BODY_MAX_BYTES = 102_400

TAB_IDS = {"home": "0", "movies": "2", "shows": "5", "anime": "8"}

ANDROID_VERSIONS = [
    ("9", "PQ3A.190605.03081104"),
    ("10", "QP1A.191005.007.A3"),
    ("11", "RP1A.200720.011"),
    ("12", "S1B.220414.015"),
    ("13", "TQ2A.230405.003"),
]
REDMI_DEVICES = [
    ("23078RKD5C", "Redmi"),
    ("2201117TY", "Redmi"),
    ("2201117TG", "Redmi"),
    ("22101316G", "Redmi"),
    ("21121210G", "Redmi"),
    ("M2012K11AG", "Redmi"),
    ("M2007J20CG", "Redmi"),
]
VERSION_CODES = [50020042, 50020043, 50020044, 50020045, 50020046]
NETWORK_TYPES = ["NETWORK_WIFI", "NETWORK_MOBILE"]
TIMEZONES = ["Asia/Kolkata", "Asia/Shanghai", "Asia/Tokyo", "America/New_York", "Europe/London"]
SPOOFED_IP_PREFIXES = [
    "103.241", "49.36", "117.195", "106.198", "122.162", "157.32", "182.70", "103.58", "27.60", "59.90",
]


def _md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _b64_decode(val: str) -> bytes:
    return base64.b64decode(val + "=" * ((4 - len(val) % 4) % 4))


def _b64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode()


def generate_x_client_token(ts: int) -> str:
    return f"{ts},{_md5_hex(str(ts)[::-1].encode())}"


def sorted_query_string(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return ""
    params: dict[str, list[str]] = {}
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        params.setdefault(k, []).append(v)
    pieces = []
    for key in sorted(params):
        for val in params[key]:
            pieces.append(f"{key}={val}")
    return "&".join(pieces)


def build_canonical_string(
    method: str,
    accept: Optional[str],
    content_type: Optional[str],
    url: str,
    body: Optional[str],
    timestamp_ms: int,
) -> str:
    parts = urlsplit(url)
    query = sorted_query_string(url)
    canonical_url = parts.path if not query else f"{parts.path}?{query}"
    if body is None:
        body_hash = ""
        body_length = ""
    else:
        body_bytes = body.encode()
        body_hash = _md5_hex(body_bytes[:SIGNATURE_BODY_MAX_BYTES])
        body_length = str(len(body_bytes))
    return "\n".join(
        [
            method.upper(),
            accept or "",
            content_type or "",
            body_length,
            str(timestamp_ms),
            body_hash,
            canonical_url,
        ]
    )


def generate_x_tr_signature(
    method: str,
    accept: Optional[str],
    content_type: Optional[str],
    url: str,
    body: Optional[str],
    timestamp_ms: int,
) -> str:
    canonical = build_canonical_string(method, accept, content_type, url, body, timestamp_ms)
    secret = _b64_decode(SECRET_KEY_DEFAULT)
    sig = hmac.new(secret, canonical.encode(), hashlib.md5).digest()
    return f"{timestamp_ms}|2|{_b64_encode(sig)}"


def random_spoofed_ip() -> str:
    prefix = random.choice(SPOOFED_IP_PREFIXES)
    return f"{prefix}.{random.randint(1, 253)}.{random.randint(1, 253)}"


def random_uuid() -> str:
    return str(uuid.uuid4())


def random_hex(length: int) -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(length))


def generate_client_info_and_ua() -> tuple[str, str]:
    android = random.choice(ANDROID_VERSIONS)
    device = random.choice(REDMI_DEVICES)
    version_code = random.choice(VERSION_CODES)
    network = random.choice(NETWORK_TYPES)
    timezone = random.choice(TIMEZONES)
    gaid = random_uuid()
    device_id = random_hex(32)

    user_agent = (
        f"com.community.oneroom/{version_code} (Linux; U; Android {android[0]}; en_US; "
        f"{device[0]}; Build/{android[1]}; Cronet/135.0.7012.3)"
    )
    client_info = (
        '{"package_name":"com.community.oneroom","version_name":"3.0.03.0529.03",'
        f'"version_code":{version_code},"os":"android","os_version":"{android[0]}",'
        f'"install_ch":"ps","device_id":"{device_id}","install_store":"ps","gaid":"{gaid}",'
        f'"brand":"{device[1]}","model":"{device[0]}","system_language":"en","net":"{network}",'
        f'"region":"US","timezone":"{timezone}","sp_code":"40401","X-Play-Mode":"2"}}'
    )
    return user_agent, client_info


class MovieBoxError(Exception):
    pass


class MovieBoxClient:
    """Stateless-enough proxy client: per-request signing + in-memory bearer token."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._active_idx = 0
        self.user_agent, self.client_info = generate_client_info_and_ua()
        self.spoofed_ip = random_spoofed_ip()
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=3.0),
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=4),
        )

    # ---- request plumbing ---------------------------------------------------
    def _signed_headers(self, method: str, url: str, body: Optional[str]) -> dict[str, str]:
        ts = int(time.time() * 1000)
        accept = content_type = "application/json"
        headers = {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Content-Type": content_type,
            "Connection": "keep-alive",
            "X-Client-Token": generate_x_client_token(ts),
            "x-tr-signature": generate_x_tr_signature(method, accept, content_type, url, body, ts),
            "X-Client-Info": self.client_info,
            "X-Client-Status": "0",
            "X-Forwarded-For": self.spoofed_ip,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _absorb_x_user(self, headers: httpx.Headers) -> None:
        raw = headers.get("x-user")
        if not raw:
            return
        import json

        try:
            token = json.loads(raw).get("token")
        except (ValueError, TypeError):
            return
        if token:
            self._token = token

    async def request(self, method: str, path_and_query: str, body: Optional[dict] = None) -> Any:
        import json as _json

        if not self._token:
            try:
                await self._request_raw(
                    "GET", "/wefeed-mobile-bff/tab-operating?page=1&tabId=0&version="
                )
            except MovieBoxError:
                pass
            if not self._token:
                raise MovieBoxError("token acquisition failed (all hosts rejected init)")

        body_str = (
            _json.dumps(body, ensure_ascii=False, separators=(",", ":"))
            if body is not None
            else None
        )
        return await self._request_raw(method, path_and_query, body_str)

    async def _request_raw(
        self, method: str, path_and_query: str, body_str: Optional[str] = None
    ) -> Any:
        start = self._active_idx
        for i in range(len(HOST_POOL)):
            if i > 0:
                await _sleep(0.05)
            idx = (start + i) % len(HOST_POOL)
            base = HOST_POOL[idx]
            url = f"{base}{path_and_query}"
            headers = self._signed_headers(method, url, body_str)
            try:
                resp = await self._http.request(method, url, headers=headers, content=body_str)
            except httpx.HTTPError:
                continue
            self._absorb_x_user(resp.headers)
            if resp.status_code in RETRY_STATUS_CODES:
                continue
            if resp.status_code >= 400:
                continue
            self._active_idx = idx
            try:
                value = resp.json()
            except ValueError:
                continue
            # strip top-level "data" wrapper (same as MovieBox-TUI parse_response)
            if isinstance(value, dict) and "data" in value:
                return value["data"]
            return value
        raise MovieBoxError("all hosts exhausted")

    async def get(self, path: str) -> Any:
        return await self.request("GET", path)

    async def post(self, path: str, body: dict) -> Any:
        return await self.request("POST", path, body)

    # ---- endpoints (mirror of providers/moviebox/mod.rs) --------------------
    async def search(self, query: str, page: int = 1, per_page: int = 20) -> Any:
        payload = {"keyword": query, "page": page, "perPage": per_page,
                   "subjectType": "All", "tabId": "All"}
        return await self.post("/wefeed-mobile-bff/subject-api/search/v2", payload)

    async def suggest(self, query: str) -> Any:
        return await self.search(query, page=1, per_page=20)

    async def get_details(self, subject_id: str) -> Any:
        details = await self.get(
            f"/wefeed-mobile-bff/subject-api/get?subjectId={subject_id}"
        )
        stype = _dig(details, "subjectType", "stype") or 1
        if int(stype) == 2:
            try:
                season_info = await self.get(
                    f"/wefeed-mobile-bff/subject-api/season-info?subjectId={subject_id}"
                )
                details["seasons"] = season_info
            except MovieBoxError:
                pass
        return details

    async def get_homepage(self, tab_id: str, page: int = 1) -> Any:
        return await self.get(
            f"/wefeed-mobile-bff/tab-operating?page={page}&tabId={tab_id}&version="
        )

    async def get_resources(
        self,
        subject_id: str,
        season: int = 0,
        episode: int = 0,
        page: int = 1,
        resolution: Optional[str] = None,
        per_page: int = 20,
    ) -> Any:
        res = f"&resolution={resolution}" if resolution else ""
        path = (
            f"/wefeed-mobile-bff/subject-api/resource?subjectId={subject_id}&page={page}"
            f"&perPage={per_page}{res}"
        )
        if season != 0 or episode != 0:
            path = (
                f"/wefeed-mobile-bff/subject-api/resource?subjectId={subject_id}&se={season}"
                f"&ep={episode}&page={page}&perPage={per_page}{res}"
            )
        return await self.get(path)

    async def get_ext_captions(self, subject_id: str, resource_id: str) -> Any:
        return await self.get(
            f"/wefeed-mobile-bff/subject-api/get-ext-captions?subjectId={subject_id}"
            f"&resourceId={resource_id}"
        )

    # ---- normalization to unified models ------------------------------------
    def normalize_search(self, query: str, payload: Any) -> list[MediaItem]:
        items: list[MediaItem] = []
        subjects = _dig(payload, "results", 0, "subjects") or []
        for sub in subjects or []:
            title = sub.get("title") or "Unknown"
            items.append(
                MediaItem(
                    provider="moviebox",
                    id=str(sub.get("subjectId") or ""),
                    title=title,
                    media_type="series" if _dig(sub, "subjectType") == 2 else "movie",
                    year=_s(sub, "releaseDate"),
                    poster_url=_dig(sub, "cover", "url"),
                    season_count=_as_int(sub.get("season")),
                    raw=sub,
                )
            )
        return items

    def normalize_homepage(self, tab: str, payload: Any) -> list[MediaItem]:
        items: list[MediaItem] = []
        seen: set[str] = set()
        for section in (payload.get("items") if isinstance(payload, dict) else []) or []:
            for sub in section.get("subjects") or []:
                sid = str(sub.get("subjectId") or "")
                if not sid or sid in seen:
                    continue
                seen.add(sid)
                items.append(
                    MediaItem(
                        provider="moviebox",
                        id=sid,
                        title=sub.get("title") or "Unknown",
                        media_type="series" if _dig(sub, "subjectType") == 2 else "movie",
                        year=_s(sub, "releaseDate"),
                        poster_url=_dig(sub, "cover", "url"),
                        imdb_rating=_s(sub, "imdbRate") or _s(sub, "imdbRatingValue"),
                        season_count=_as_int(sub.get("season")),
                        raw=sub,
                    )
                )
        return items

    def normalize_details(self, subject_id: str, payload: Any) -> MediaDetails:
        stype = _dig(payload, "subjectType", "stype") or 1
        seasons: list[Season] = []
        season_rows = _dig(payload, "seasons", "seasons") or []
        for row in season_rows or []:
            number = _as_int(row.get("se")) or 0
            eps = row.get("episodeNumbers") or []
            seasons.append(
                Season(
                    number=number,
                    episodes=[
                        Episode(season=number, number=_as_int(e) or i + 1)
                        for i, e in enumerate(eps)
                    ],
                )
            )
        return MediaDetails(
            provider="moviebox",
            id=subject_id,
            title=_s(payload, "title") or "Unknown",
            media_type="series" if int(stype) == 2 else "movie",
            year=_s(payload, "releaseDate"),
            description=_s(payload, "description"),
            tagline=_s(payload, "tagline"),
            imdb_rating=_s(payload, "imdbRatingValue"),
            director=_s(payload, "director"),
            stars=_s(payload, "stars"),
            prints=_s(payload, "prints"),
            audios=_s(payload, "audios"),
            poster_url=_dig(payload, "cover", "url"),
            genres=[
                g.strip() for g in (payload.get("genre") or "").split(",") if g.strip()
            ],
            seasons=seasons,
        )

    def normalize_streams(
        self, subject_id: str, payload: Any, season: int = 0, episode: int = 0
    ) -> list[Stream]:
        raw_list = payload.get("list") if isinstance(payload, dict) else payload
        streams: list[Stream] = []
        for item in raw_list or []:
            streams.append(
                Stream(
                    provider="moviebox",
                    filename=_s(item, "fileName") or _s(item, "title") or "",
                    quality=f"{_dig(item, 'resolution')}p" if _dig(item, "resolution") else None,
                    codec=_s(item, "codecName"),
                    language=_s(item, "language"),
                    size_bytes=_as_int(item.get("size")),
                    season=_as_int(item.get("se")),
                    episode=_as_int(item.get("ep")),
                    resource_id=_s(item, "resourceId"),
                    mirrors=[
                        SourceMirror(
                            label=_s(item, "uploadBy") or "MovieBox",
                            resolver_url=_s(item, "resourceLink"),
                            direct_file=bool(_s(item, "resourceLink")),
                        )
                    ],
                )
            )
        return streams

    def normalize_captions(self, payload: Any) -> list[SubtitleTrack]:
        tracks = []
        for cap in _dig(payload, "extCaptions") or []:
            url = _s(cap, "url")
            if url:
                tracks.append(SubtitleTrack(name=_s(cap, "lanName") or "Unknown", url=url))
        return tracks


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def _dig(value, *keys):
    for key in keys:
        if isinstance(value, dict):
            if key not in value:
                return None
            value = value[key]
        elif isinstance(value, list):
            if not isinstance(key, int) or not 0 <= key < len(value):
                return None
            value = value[key]
        else:
            return None
    return value


def _s(value: dict, key: str) -> Optional[str]:
    v = value.get(key)
    if isinstance(v, str):
        return v
    return None if v is None else str(v)


def _as_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
