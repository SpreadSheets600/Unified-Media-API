<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688" alt="FastAPI">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
</p>

<h1 align="center">Unified API</h1>

<p align="center">
  One normalized REST API for <b>movies, series, subtitles, live TV, and update
  feeds</b> - behind a single, documented, deploy-ready FastAPI service.
</p>

## Why?

This API talks to three very different backends: a **signed private BFF
API**, an **HTML scraped catalog** (4KHDHub), and **M3U live-TV feeds**
(IPTV-org). Each has its own auth scheme, data shape, and failure modes.

This project wraps them all into one consistent API so a single client
implementation can power an entire app - search, browse, details, playback,
subtitles, and live TV - without touching any provider internals.

| Provider | Auth / technique | Exposed by |
| - | - | - |
| MovieBox | HMAC-MD5 signed requests + session token | Search, tabs, details, streams, subtitles, play |
| 4KHDHub | HTML scraping + mirror resolution chain | Search, details, releases, play |
| IPTV-org | Public M3U / JSON feeds (24h cache) | Categories, languages, countries, channels |

## Quick start

```bash
# 1. Create the environment (uv or pip)
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
# or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. Run the server
.venv/bin/python -m uvicorn app.main:app --port 8000

# 3. Open the docs
open http://localhost:8000/docs
```

Everything is self-contained - no databases, no external services required.
The session token is bootstrapped automatically on first request.

## Documentation

Interactive, always up-to-date docs are served by the app itself:

- **Swagger UI** - `http://localhost:8000/docs` (try every endpoint live)
- **ReDoc** - `http://localhost:8000/redoc`
- **OpenAPI JSON** - `http://localhost:8000/openapi.json`

### Endpoint reference

| Method | Endpoint | Description |
| - | - | - |
| `GET` | `/search?q=…&provider=moviebox\|fourkhdhub\|all` | Unified search |
| `GET` | `/suggest?q=…` | Typeahead suggestions (top 8) |
| `GET` | `/discover?tab=home\|movies\|shows\|anime` | Browse tabs |
| `GET` | `/details/{provider}/{id}` | Full metadata + seasons |
| `GET` | `/streams/{provider}/{id}` | Sources; `?season=&episode=`, `?resolution=` |
| `GET` | `/play/{provider}/{id}` | Playable URL; `?resolve=true` for 4KHDHub |
| `GET` | `/subtitles/{id}?resource_id=…` | Subtitle tracks |
| `GET` | `/tv/categories` `/tv/languages` `/tv/countries` | IPTV option lists |
| `GET` | `/tv/channels?category=…\|language=…\|country=…\|custom_url=…` | Parsed M3U channels |
| `DELETE` | `/cache/{namespace}` | Clear the response cache |

### Example

```bash
curl "http://localhost:8000/search?q=inception&provider=moviebox"

# →
{
  "query": "inception",
  "provider": "moviebox",
  "items": [
    {
      "provider": "moviebox",
      "id": "6391474290696802080",
      "title": "Inception",
      "media_type": "movie",
      "year": "2010-07-16",
      "poster_url": "https://pbcdn.aoneroom.com/image/…",
      "imdb_rating": "8.8"
    }
  ]
}
```

## Configuration

Configuration is done via environment variables:

| Variable | Default | Purpose |
| - | - | - |
| `MOVIEBOX_FOURKHDHUB_URL` | `https://4khdhub.one/` | Override the 4KHDHub base URL |
| `UNIFIED_API_CACHE_DIR` | system temp dir | Where the TTL cache is stored |

## Project layout

```
.
├── app/
│   ├── main.py                  # FastAPI app: routes, tags, docs
│   ├── models.py                # Normalized Pydantic response models
│   ├── cache.py                 # TTL file cache
│   └── providers/
│       ├── moviebox.py          # Signed BFF client (HMAC-MD5 + session token)
│       ├── fourkhdhub.py        # Scraper + mirror resolver/preflight chain
│       ├── iptvorg.py           # IPTV-org feeds + M3U parsing
├── requirements.txt
└── README.md
```

## Deployment

### Deploy on Vercel

The app is compatible with Vercel's Python runtime as-is. Link the repo in
the Vercel dashboard, set the framework to *Other* - *Python*, and the
`app.main:app` entrypoint is detected automatically. See
[`vercel.json`](vercel.json) for the recommended config.

```bash
# or via the Vercel CLI
npm i -g vercel
vercel
```

## Troubleshooting

| Symptom | Cause / fix |
| - | - |
| `502 moviebox: all hosts exhausted` | All MovieBox hosts rejected the request - retry; hosts rotate automatically. If persistent, the signing secret or device fingerprint may have rotated upstream. |
| `502` from 4KHDHub endpoints | The site is behind Cloudflare; a browser User-Agent is used, but heavy scraping may be challenged. Retry after a moment. |
| `407 / 441` inside provider logs | The upstream rejected the signature or session token - restart the server to force a fresh bootstrap. |
| IPTV lists empty | Namespace cache may be stale - `DELETE /cache/iptv-org` then retry. |

## Acknowledgments

This project is based on the
[MovieBox TUI](https://github.com/mesamirh/MovieBox-Tui) project - the
provider APIs, signing logic, and data flows are taken from that project.
