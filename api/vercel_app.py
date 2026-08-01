"""Vercel serverless entrypoint.

Vercel's Python runtime auto-detects ASGI/WSGI applications exposed as `app`
from modules in `api/`. Every route is served as a single serverless function.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app

handler = app
