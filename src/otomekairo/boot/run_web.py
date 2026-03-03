"""Web server launcher."""

from __future__ import annotations

import os

import uvicorn


# Block: Uvicorn launcher
def main() -> None:
    host = os.environ.get("OTOMEKAIRO_HOST", "127.0.0.1")
    port = int(os.environ.get("OTOMEKAIRO_PORT", "8000"))
    uvicorn.run(
        "otomekairo.web.app:create_app",
        factory=True,
        host=host,
        port=port,
    )
