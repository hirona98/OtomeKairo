"""FastAPI server factory."""

from __future__ import annotations

from fastapi import FastAPI

from otomekairo.web.app import create_app


# Block: App factory export
def build_fastapi_app() -> FastAPI:
    return create_app()
