"""One-command source-checkout demo, serving the Vite build on the API origin."""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import file_digest, sha256
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

from autocan.api import create_app

if TYPE_CHECKING:
    from autocan.live_scoring import LiveModel

_STAMP = ".autocan-frontend-v1"


def _fingerprint(root: Path, paths: list[Path], *context: object) -> str:
    digest = sha256(json.dumps(context, sort_keys=True).encode())
    for path in sorted(paths):
        if path.is_file():
            digest.update(os.fsencode(path.relative_to(root)) + b"\0")
            with path.open("rb") as stream:
                digest.update(file_digest(stream, "sha256").digest())
    return digest.hexdigest()


def _matches(stamp: Path, fingerprint: str) -> bool:
    try:
        return stamp.read_bytes() == fingerprint.encode()
    except FileNotFoundError:
        return False


@contextmanager
def _frontend_lock(path: Path) -> Iterator[None]:
    # The OS releases the lock even if a launch is interrupted.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if sys.platform == "win32":
            import msvcrt

            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _ensure_frontend(frontend: Path, npm: str, node: str, env: dict[str, str]) -> int:
    modules = frontend / "node_modules"
    dist = frontend / "dist"
    install_stamp = modules / _STAMP
    build_stamp = dist / _STAMP
    toolchain = []
    for executable in (node, npm):
        path = Path(executable).resolve()
        stat = path.stat()
        toolchain.append((str(path), stat.st_size, stat.st_mtime_ns))
    npm_env = {
        key: value
        for key, value in env.items()
        if key.lower().startswith("npm_config_") or key == "NODE_ENV"
    }
    dependency_inputs = _fingerprint(
        frontend,
        [frontend / name for name in ("package.json", "package-lock.json", ".npmrc")],
        toolchain,
        npm_env,
    )

    def installed_dependencies() -> str:
        return _fingerprint(frontend, [modules / ".package-lock.json"], dependency_inputs)

    bin_suffix = ".cmd" if sys.platform == "win32" else ""
    has_build_tools = all(
        (modules / ".bin" / (name + bin_suffix)).is_file() for name in ("tsc", "vite")
    )
    if not has_build_tools or not _matches(install_stamp, installed_dependencies()):
        install_stamp.unlink(missing_ok=True)
        build_stamp.unlink(missing_ok=True)
        install = subprocess.run(
            [npm, "ci", "--no-audit", "--no-fund"], cwd=frontend, env=env, check=False
        )
        if install.returncode:
            return install.returncode
        install_stamp.write_text(installed_dependencies(), encoding="ascii")

    build_env = {key: value for key, value in env.items() if key.startswith("VITE_")}
    source_inputs = _fingerprint(
        frontend,
        [
            frontend / "index.html",
            *frontend.glob("vite.config.*"),
            *frontend.glob("tsconfig*.json"),
            *(frontend / "src").rglob("*"),
            *(frontend / "public").rglob("*"),
            *(
                frontend / name
                for name in (".env", ".env.local", ".env.production", ".env.production.local")
            ),
        ],
        installed_dependencies(),
        build_env,
    )

    def built_assets() -> str:
        return _fingerprint(
            frontend, [path for path in dist.rglob("*") if path != build_stamp], source_inputs
        )

    if (dist / "index.html").is_file() and _matches(build_stamp, built_assets()):
        return 0
    build_stamp.unlink(missing_ok=True)
    build = subprocess.run([npm, "run", "build"], cwd=frontend, env=env, check=False)
    if build.returncode:
        return build.returncode
    if not (dist / "index.html").is_file():
        raise ValueError("frontend build did not produce dist/index.html")
    build_stamp.write_text(built_assets(), encoding="ascii")
    return 0


def launch(*, port: int = 8000, live_model: "LiveModel | None" = None) -> int:
    if not 1024 <= port <= 65535:
        raise ValueError("port must be in [1024, 65535]")
    frontend = Path.cwd() / "frontend"
    if not (frontend / "package-lock.json").is_file():
        raise ValueError("run studio from a source checkout containing frontend/package-lock.json")
    npm = shutil.which("npm")
    node = shutil.which("node")
    if npm is None or node is None:
        raise ValueError("Node.js >=22.12 and npm are required to build the dashboard")
    # Use a project-local cache; no global npm installation or configuration changes.
    env = {**os.environ, "npm_config_cache": str(Path.cwd() / ".cache/npm")}
    with _frontend_lock(frontend.parent / ".cache" / "studio-frontend.lock"):
        result = _ensure_frontend(frontend, npm, node, env)
    if result:
        return result
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
