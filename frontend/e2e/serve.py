"""Serve the production UI with disposable recordings for browser acceptance."""

from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from autocan.api import create_app

with TemporaryDirectory(prefix="autocan-e2e-") as recordings:
    uvicorn.run(
        create_app(data_root=Path(recordings), static_root=Path("frontend/dist")),
        host="127.0.0.1",
        port=8765,
    )
