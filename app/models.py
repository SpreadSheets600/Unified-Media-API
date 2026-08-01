"""Pydantic models: the normalized, unified response shapes for every endpoint."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class MediaItem(BaseModel):
    provider: str                      # "moviebox" | "fourkhdhub"
    id: str                            # provider-specific id (subjectId / slug path)
    title: str
    media_type: str                    # "movie" | "series"
    year: Optional[str] = None
    poster_url: Optional[str] = None
    imdb_rating: Optional[str] = None
    season_count: Optional[int] = None
    raw: Optional[dict] = None         # raw provider payload (optional passthrough)


class Episode(BaseModel):
    season: int
    number: int
    title: Optional[str] = None


class Season(BaseModel):
    number: int
    episodes: List[Episode] = []


class MediaDetails(BaseModel):
    provider: str
    id: str
    title: str
    media_type: str
    year: Optional[str] = None
    description: Optional[str] = None
    tagline: Optional[str] = None
    imdb_rating: Optional[str] = None
    director: Optional[str] = None
    stars: Optional[str] = None
    prints: Optional[str] = None
    audios: Optional[str] = None
    poster_url: Optional[str] = None
    genres: List[str] = []
    seasons: List[Season] = []


class SourceMirror(BaseModel):
    label: str
    resolver_url: str
    headers: List[List[str]] = []      # list of [name, value]
    direct_file: bool = False


class Stream(BaseModel):
    provider: str
    filename: str
    quality: Optional[str] = None
    codec: Optional[str] = None
    language: Optional[str] = None
    size_bytes: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    resource_id: Optional[str] = None
    mirrors: List[SourceMirror] = []


class PlaybackSource(BaseModel):
    provider: str
    url: str
    headers: List[List[str]] = []      # list of [name, value]
    subtitle: Optional[str] = None
    source_label: str


class SubtitleTrack(BaseModel):
    name: str
    url: str


class Channel(BaseModel):
    id: str
    name: str
    logo: str = ""
    group: str = ""
    stream_url: str


class ListOption(BaseModel):
    name: str
    code: str


class SearchResponse(BaseModel):
    query: str
    provider: str
    items: List[MediaItem]


class StreamsResponse(BaseModel):
    provider: str
    id: str
    season: int = 0
    episode: int = 0
    resolutions: List[int] = []
    streams: List[Stream]
