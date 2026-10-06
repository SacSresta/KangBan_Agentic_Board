"""One process owns the local database, queue worker, and graph checkpointer."""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .contracts import Status
from .engine import Engine
from .storage import Store

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_ORIGINS = {"http://127.0.0.1:3000", "http://localhost:3000", "http://127.0.0.1:8765"}


class ProcessLock:
    """Prevent multiple backend processes from sharing a queue/checkpointer."""
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                self.file.write(b"0")
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Another Kanban backend already owns this data directory") from None

    def close(self):
        self.file.close()


async def consume(app: FastAPI):
    store, engine = app.state.store, app.state.engine
    while True:
        run = None
        try:
            queue = store.queue_state()
            run = store.get_run(queue.active_run_id) if queue.active_run_id else None
            if run is None and queue.enabled:
                run = store.claim_next()
            if run and run.outcome == "running":
                await engine.execute(run.id)
                continue
            app.state.wake.clear()
            try:
                await asyncio.wait_for(app.state.wake.wait(), timeout=0.5)
            except TimeoutError:
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            # Avoid persisting provider exception strings that may contain credentials.
            if run is not None:
                store.update_run(run.id, outcome="blocked", interruption_reason="Worker failed; inspect retained worktree before resuming")
                store.set_ticket_status(run.ticket_id, Status.BLOCKED)
                store.add_activity(run.id, "System", "error", "Worker failed; worktree and journal retained for inspection")
            await asyncio.sleep(0.5)


def create_app(data_dir: Path | None = None, serve_ui: bool = True) -> FastAPI:
    # Use only the application's .env; never search a selected source repository.
    # Explicit process environment values take precedence over file values.
    load_dotenv(ROOT / ".env", override=False)
    data_dir = Path(data_dir or os.environ.get("KANBAN_DATA_DIR", ROOT / ".data")).resolve()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lock = ProcessLock(data_dir / "backend.lock")
        worker = None
        engine = None
        try:
            store = Store(data_dir)
            store.recover()
            engine = Engine(store, data_dir)
            await engine.start()
            app.state.store = store
            app.state.engine = engine
            app.state.wake = asyncio.Event()
            worker = asyncio.create_task(consume(app), name="sequential-queue")
            yield
        finally:
            if worker:
                app.state.store.set_queue_enabled(False)
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
            if engine:
                await engine.shutdown()
            lock.close()

    app = FastAPI(title="Kanban Agent Board", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=sorted(ALLOWED_ORIGINS), allow_methods=["GET", "POST", "PUT", "PATCH"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def local_browser_only(request: Request, call_next):
        # CORS alone does not stop cross-origin form submissions to a local service.
        origin = request.headers.get("origin")
        host = request.headers.get("host", "").split(":")[0]
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "Local host required"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"detail": "Local application origin required"}, status_code=403)
        return await call_next(request)

    app.include_router(router)
    export = ROOT / "frontend" / "out"
    if serve_ui and export.is_dir():
        app.mount("/", StaticFiles(directory=export, html=True), name="frontend")
    return app
