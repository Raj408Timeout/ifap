"""ASGI entry point: `uvicorn ifap.main:app`."""

from ifap.api.app import create_app

app = create_app()
