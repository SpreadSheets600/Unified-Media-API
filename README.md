<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688" alt="FastAPI">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
</p>

<h1 align="center">Unified API</h1>

<p align="center">
  One normalized REST API that powers every data source of
  <a href="https://github.com/mesamirh/MovieBox-Tui">MovieBox-TUI</a>:
  <b>movies, series, subtitles, live TV, and update feeds</b> — behind a single,
  documented, deploy-ready FastAPI service.
</p>

## Why?

MovieBox-TUI talks to four very different backends: a **signed private BFF
API** (MovieBox/OneRoom), an **HTML scraped catalog** (4KHDHub), **M3U live-TV
feeds** (IPTV-org), and the **GitHub API**. Each has its own auth scheme, data
shape, and failure modes.

This project wraps all four into one consistent API so a single client
implementation can power an entire app — search, browse, details, playback,
subtitles, and live TV — without touching any provider internals.

| Provider | Auth / technique | Exposed by |
| --- | --- | --- |
| MovieBox | HMAC-MD5 signed requests + session token | Search, tabs, details, streams, subtitles, play |
| 4KHDHub | HTML scraping + mirror resolution chain | Search, details, releases, play |
| IPTV-org | Public M3U / JSON feeds (24h cache) | Categories, languages, countries, channels |
| GitHub | Public releases API | Update metadata + latest version |

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

Everything is self-contained — no databases, no external services required.
The MovieBox session token is bootstrapped automatically on first request.

## Documentation

Interactive, always up-to-date docs are served by the app itself:

- **Swagger UI** — `http://localhost:8000/docs` (try every endpoint live)
- **ReDoc** — `http://localhost:8000/redoc`
- **OpenAPI JSON** — `http://localhost:8000/openapi.json`

### Endpoint reference

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/search?q=…&provider=moviebox\|fourkhdhub\|all` | Unified search |
| `GET` | `/suggest?q=…` | MovieBox typeahead (top 8) |
| `GET` | `/discover?tab=home\|movies\|shows\|anime` | Browse tabs |
| `GET` | `/details/{provider}/{id}` | Full metadata + seasons |
| `GET` | `/streams/{provider}/{id}` | Sources; `?season=&episode=`, `?resolution=` |
| `GET` | `/play/{provider}/{id}` | Playable URL; `?resolve=true` for 4KHDHub |
| `GET` | `/subtitles/{id}?resource_id=…` | Subtitle tracks |
| `GET` | `/tv/categories` `/tv/languages` `/tv/countries` | IPTV option lists |
| `GET` | `/tv/channels?category=…\|language=…\|country=…\|custom_url=…` | Parsed M3U channels |
| `GET` | `/updates` `/updates/latest` | GitHub release metadata |
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
| --- | --- | --- |
| `MOVIEBOX_FOURKHDHUB_URL` | `https://4khdhub.one/` | Override the 4KHDHub base URL |
| `GITHUB_TOKEN` | — | GitHub API token (avoids rate limits) |
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
│       └── github_releases.py   # GitHub releases proxy
├── requirements.txt
└── README.md
```

## Deployment

### Hosting options

| Platform | Python/FastAPI support | Free tier | Notes |
| --- | --- | --- | --- |
| **Vercel** | Yes (serverless) | Yes | Best choice for a lightweight public API; cold starts ~1s |
| **Render** | Yes (full server) | Yes | Full uvicorn process; sleeps after ~15 min idle |
| **Fly.io** | Yes (full server) | Trial credits | Best for heavy workloads |
| **Railway** | Yes (full server) | Trial credits | Easy CLI deploys |
| **Netlify** | **No** | — | Functions are TS/JS/Go only; Python is build-time only — cannot run this |

> **Vercel quick deploy** — the app is compatible with Vercel's Python
> runtime as-is. Link the repo in the dashboard, set the framework to
> *Other* → *Python*, and the `app.main:app` entrypoint is detected
> automatically. See [`vercel.json`](vercel.json) for the recommended config.

> **Render quick deploy** — create a *Web Service* from the repo; Render
> auto-detects the Python env, installs `requirements.txt`, and runs the
> start command from [`render.yaml`](render.yaml) (`uvicorn app.main:app`).

### Running behind a reverse proxy

The app is stateless and scales horizontally; only the MovieBox session token
is kept in memory per instance. Put it behind nginx/Caddy or any PaaS load
balancer and terminate TLS there.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `502` from 4KHDHub endpoints | The site is behind Cloudflare; a browser User-Agent is used, but heavy scraping may be challenged. Retry after a moment. |
| IPTV lists empty | Namespace cache may be stale — `DELETE /cache/iptv-org` then retry. |

## License

MIT — see [LICENSE](LICENSE).
