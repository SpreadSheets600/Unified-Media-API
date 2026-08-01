"""Unified API — a Python (FastAPI) proxy library that exposes every source used by MovieBox-TUI
as one clean, callable REST API.

Providers:
  - MovieBox (signed private BFF API)   -> search, suggest, discover, details, streams, subtitles
  - 4KHDHub (HTML scraping)             -> search, details, releases, mirror resolution
  - IPTV-org (M3U feeds)                -> categories, languages, countries, channels
  - GitHub Releases                     -> update metadata for MovieBox-Tui
"""
