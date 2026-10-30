"""Local REST/WS boundary. Core mutations run serially on one asyncio loop."""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from autocan.api.runtime import Session, saved_view
from autocan.traces import rerun, verify_trace


class LocalBoundary:
    """Same-origin browser access, bounded JSON bodies, no public host names."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        origin = headers.get(b"origin")
        scheme = "https" if scope["scheme"] in {"https", "wss"} else "http"
        expected = scheme.encode() + b"://" + headers.get(b"host", b"")
        if origin and origin != expected:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await PlainTextResponse("cross-origin access denied", 403)(scope, receive, send)
            return
        if scope["type"] == "http" and scope["method"] in {"POST", "PATCH"}:
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 4096:
                    await PlainTextResponse("request exceeds 4096 bytes", 413)(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break

            async def buffered_receive() -> dict[str, Any]:
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            await self.app(scope, buffered_receive, send)
        else:
            await self.app(scope, receive, send)


class CreateSimulation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile_id: str = "democar"
    seed: int = Field(default=42, ge=0, le=2**32 - 1)


class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    throttle: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    brake: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    gear: Literal["P", "R", "N", "D"] | None = None
    ignition: Literal["OFF", "ACC", "ON", "START"] | None = None
    engine_running: bool | None = None

    @model_validator(mode="after")
    def validate_controls(self) -> Self:
        if not self.model_fields_set or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("provide at least one input; null inputs are not supported")
        return self


class Fault(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    offline: bool


def create_app(*, data_root: Path = Path("data/runs"), static_root: Path | None = None) -> FastAPI:
    active: Session | None = None
    connections = 0

    async def pace() -> None:
        while True:
            await asyncio.sleep(0.05)
            if active is not None:
                try:
                    active.tick()
                except Exception as exc:
                    active.fail(exc)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker = asyncio.create_task(pace())
        try:
            yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            if active is not None:
                active.close()

    app = FastAPI(title="AutoCAN Studio", lifespan=lifespan)
    app.add_middleware(LocalBoundary)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.exception_handler(ValueError)
    async def invalid(request: Any, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    def current(identifier: str) -> Session:
        if active is None or active.id != identifier:
            raise HTTPException(404, "simulation not found")
        return active

    def run_path(identifier: str) -> Path:
        path = data_root / identifier
        if not re.fullmatch(r"[0-9a-f]{32}", identifier) or path.is_symlink() or not path.is_dir():
            raise HTTPException(404, "saved run not found")
        if active is not None and active.id == identifier and not active.stopped:
            raise HTTPException(409, "stop and save the recording first")
        return path

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "schema_version": 1}

    @app.get("/api/profiles")
    async def profiles() -> list[dict[str, str]]:
        return [{"id": "democar", "name": "DemoCar", "version": "democar-0.1.0"}]

    @app.post("/api/simulations")
    async def create(spec: CreateSimulation) -> dict[str, Any]:
        nonlocal active
        if spec.profile_id != "democar":
            raise HTTPException(422, "only democar is available in M3")
        if active is not None and not active.stopped:
            raise HTTPException(409, "stop and save the active run first")
        if active is not None:
            active.close()
        active = Session(data_root, seed=spec.seed)
        return active.snapshot()

    @app.get("/api/simulations/current")
    async def get_current() -> dict[str, Any] | None:
        return None if active is None else active.snapshot()

    @app.get("/api/simulations/{identifier}/state")
    async def state(identifier: str) -> dict[str, Any]:
        return current(identifier).snapshot()

    @app.patch("/api/simulations/{identifier}/inputs")
    async def inputs(identifier: str, spec: Inputs) -> dict[str, int]:
        return {
            "applied_at_ns": current(identifier).inputs(
                spec.throttle,
                brake=spec.brake,
                gear=spec.gear,
                ignition=spec.ignition,
                engine_running=spec.engine_running,
            )
        }

    @app.post("/api/simulations/{identifier}/faults")
    async def fault(identifier: str, spec: Fault) -> dict[str, int]:
        return {"applied_at_ns": current(identifier).offline(spec.offline)}

    @app.post("/api/simulations/{identifier}/{action}")
    async def control(identifier: str, action: str) -> dict[str, Any]:
        nonlocal active
        session = current(identifier)
        try:
            if action == "reset":
                session.stop()
                seed = int(session.sim.state()["seed"] or 0)
                session.close()
                active = Session(data_root, seed=seed)
                return active.snapshot()
            if action not in {"start", "pause", "stop"}:
                raise HTTPException(404, "unknown simulation action")
            getattr(session, action)()
            return session.snapshot()
        except (OSError, RuntimeError) as exc:
            session.fail(exc)
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/runs")
    async def runs() -> list[dict[str, Any]]:
        if not data_root.exists():
            return []
        results: list[dict[str, Any]] = []
        # Only this server's UUID run directories, never arbitrary user paths.
        for path in sorted(data_root.iterdir(), key=lambda p: p.name):
            if len(results) >= 64:
                break
            if re.fullmatch(r"[0-9a-f]{32}", path.name) and not path.is_symlink():
                if active is not None and active.id == path.name and not active.stopped:
                    continue
                try:
                    meta = await asyncio.to_thread(verify_trace, path)
                    results.append({"id": path.name, "duration_ns": meta["end_time_ns"]})
                except (ValueError, OSError):
                    continue
        return results

    @app.get("/api/runs/{identifier}/playback")
    async def read_playback(
        identifier: str, until_ns: Annotated[int | None, Query(ge=0)] = None
    ) -> dict[str, Any]:
        return await asyncio.to_thread(saved_view, run_path(identifier), until_ns)

    @app.post("/api/runs/{identifier}/rerun")
    async def recompute(identifier: str) -> dict[str, object]:
        return await asyncio.to_thread(rerun, run_path(identifier))

    @app.websocket("/ws/simulations/{identifier}")
    async def stream(ws: WebSocket, identifier: str) -> None:
        nonlocal connections
        if active is None or active.id != identifier or connections >= 8:
            await ws.close(code=1008)
            return
        session = active
        connections += 1
        try:
            await ws.accept()
            seq = 0
            last_frame = 0
            last_time = -1
            while active is session:
                snapshot = session.snapshot()
                seq += 1
                message = {
                    **snapshot,
                    "type": "snapshot" if seq == 1 else "frames_batch",
                    "schema_version": 1,
                    "seq": seq,
                    "skipped_frames": 0,
                }
                if seq > 1:
                    frames = snapshot["frames"]
                    if frames:
                        message["skipped_frames"] = max(0, frames[0]["frame_id"] - last_frame - 1)
                    message["frames"] = [f for f in frames if f["frame_id"] > last_frame]
                    message["signals"] = [
                        s for s in snapshot["signals"] if s["sim_time_ns"] > last_time
                    ]
                if snapshot["frames"]:
                    last_frame = snapshot["frames"][-1]["frame_id"]
                if snapshot["signals"]:
                    last_time = snapshot["signals"][-1]["sim_time_ns"]
                await asyncio.wait_for(ws.send_json(message), timeout=1)
                await asyncio.sleep(0.05)
            await ws.close(code=1000)
        except (WebSocketDisconnect, OSError, TimeoutError):
            pass
        finally:
            connections -= 1

    if static_root is not None:
        app.mount("/", StaticFiles(directory=static_root, html=True), name="frontend")
    return app
