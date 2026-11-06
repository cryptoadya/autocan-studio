"""One-command source-checkout demo, serving the Vite build on the API origin."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

from autocan.api import create_app

if TYPE_CHECKING:
    from autocan.live_scoring import LiveModel


def launch(*, port: int = 8000, live_model: "LiveModel | None" = None) -> int:
    if not 1024 <= port <= 65535:
        raise ValueError("port must be in [1024, 65535]")
    frontend = Path.cwd() / "frontend"
    if not (frontend / "package-lock.json").is_file():
        raise ValueError("run studio from a source checkout containing frontend/package-lock.json")
    npm = shutil.which("npm")
    if npm is None:
        raise ValueError("Node.js >=22.12 and npm are required to build the dashboard")
    # Use a project-local cache; no global npm installation or configuration changes.
    env = {**os.environ, "npm_config_cache": str(Path.cwd() / ".cache/npm")}
    for command in ([npm, "ci", "--no-audit", "--no-fund"], [npm, "run", "build"]):
        result = subprocess.run(command, cwd=frontend, env=env, check=False)
        if result.returncode:
            return result.returncode
    print(f"AutoCAN Studio: http://127.0.0.1:{port} (Ctrl+C to stop)", flush=True)
    uvicorn.run(
        create_app(static_root=frontend / "dist", live_model=live_model),
        host="127.0.0.1",
        port=port,
        limit_concurrency=32,
        ws_max_size=4096,
        ws_max_queue=8,
    )
    return 0
