"""Serve the production UI with disposable recordings for browser acceptance."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from autocan.api import create_app

live_model = None
if model_root := os.environ.get("AUTOCAN_E2E_MODEL_ROOT"):
    from autocan.live_scoring import load_live_model

    root = Path(model_root)
    live_model = load_live_model(root / "experiment", root / "prepared", root / "source")

with TemporaryDirectory(prefix="autocan-e2e-") as recordings:
    uvicorn.run(
        create_app(
            data_root=Path(recordings), static_root=Path("frontend/dist"), live_model=live_model
        ),
        host="127.0.0.1",
        port=8765,
    )
