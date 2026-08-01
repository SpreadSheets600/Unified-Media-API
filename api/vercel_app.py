"""Vercel serverless entrypoint.

Vercel's Python runtime scans modules in `api/` for a WSGI/ASGI application
object. This module exposes the FastAPI app so every route is served as a
single serverless function (see `vercel.json`).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app

handler = app
