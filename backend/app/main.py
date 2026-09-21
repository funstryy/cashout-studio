from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi import Response
from fastapi.responses import FileResponse, JSONResponse

from .api.routes_browser import router as browser_router
from .api.routes_collab import router as collab_router
from .api.routes_coproducer import router as coproducer_router
from .api.routes_engine import router as engine_router
from .api.routes_effects import router as effects_router
from .api.routes_treblo import router as treblo_router
from .api.routes_lora_dataset import router as lora_dataset_router
from .api.routes_midi import router as midi_router
from .api.routes_orchestrator import router as orchestrator_router
from .api.routes_projects import router as projects_router
from .api.routes_proxy import router as proxy_router
from .api.routes_stems import router as stems_router
from .api.routes_system import router as system_router
from .api.routes_plugins import router as plugins_router
from .api.routes_separation import router as separation_router
from .api.routes_spotify import router as spotify_router
from .api.routes_tracks import router as tracks_router
from .api.routes_users import router as users_router
from .api.routes_voices import router as voices_router
from .api.routes_yue2_upload import router as yue2_upload_router
from . import collab, native_engine
from .config import FRONTEND_DIST_DIR, VOICES_DIR
from .orchestrator.manager import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # audiocpp_server is launched with --voice-dir pointing here, so the
    # folder has to exist before the first model switch, not just before the
    # first voice import.
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    yield
    # Close the session's listener first. Engine shutdown below can take the
    # better part of a minute, and leaving a port open on the VPN while the
    # app is on its way out invites a guest to connect to a studio that is
    # already tearing itself down.
    collab.session.stop()
    await collab.close_listener()
    # A child process holding an exclusive audio device open after the
    # studio has gone is a device nothing else on the machine can use.
    native_engine.engine.stop()
    # Every engine, not just the selected one: both can be up at once now,
    # and anything left behind keeps holding VRAM or CPU after we exit.
    await manager.stop_all()


app = FastAPI(title="Cashout Studio", lifespan=lifespan)

app.include_router(orchestrator_router)
app.include_router(spotify_router)
app.include_router(system_router)
app.include_router(browser_router)
app.include_router(collab_router)
app.include_router(engine_router)
app.include_router(coproducer_router)
app.include_router(effects_router)
app.include_router(treblo_router)
app.include_router(tracks_router)
app.include_router(stems_router)
app.include_router(midi_router)
app.include_router(projects_router)
app.include_router(lora_dataset_router)
app.include_router(voices_router)
app.include_router(separation_router)
app.include_router(plugins_router)
app.include_router(users_router)
# Registered before proxy_router's catch-all so this exact path wins.
app.include_router(yue2_upload_router)
app.include_router(proxy_router)
@app.middleware("http")
async def confine_remote_callers(request: Request, call_next):
    """Everything is loopback-only unless a live session says otherwise.

    The studio was built for a single trusted user: the browser endpoint
    reads the disk, the track endpoints delete files, and profiles are a
    convenience rather than a boundary. Collaboration is the first thing
    that puts the port on a network, so the rule is inverted here - a caller
    from another machine is refused by default, and the collab module names
    the handful of read-only paths a guest genuinely needs.
    """
    client = request.client.host if request.client else ""
    if not client or client.startswith("127.") or client in ("::1", "localhost"):
        return await call_next(request)

    token = request.headers.get("x-collab-token") or request.query_params.get("ct") or ""
    path = request.url.path

    # A guest's page lives on their own machine, so every one of these is a
    # cross-origin request and the browser will not hand the bytes to the
    # page without permission - measured the hard way: an httpx client
    # fetches the audio happily and a browser refuses the same URL. The
    # token is what grants access here, not the origin, so "*" is both
    # accurate and no weaker than the check above.
    cors = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "X-User-Id, Content-Type",
        "Access-Control-Max-Age": "600",
    }

    # The refusal carries the headers too. Without them the browser cannot
    # show the page a status at all - it reports an opaque network failure,
    # and a guest with an expired code gets told to check their VPN.
    def refuse() -> JSONResponse:
        return JSONResponse(
            {"detail": "this studio is not sharing with you"}, status_code=403, headers=cors,
        )

    if request.method == "OPTIONS":
        # A preflight has no body and no session of its own, so it is judged
        # on the request it is announcing. The real request still has to
        # carry the token.
        intended = request.headers.get("access-control-request-method", "GET")
        return Response(status_code=204, headers=cors)             if collab.remote_request_allowed(intended, path, token) else refuse()

    if not collab.remote_request_allowed(request.method, path, token):
        return refuse()

    response = await call_next(request)
    response.headers.update(cors)
    return response


if FRONTEND_DIST_DIR.exists():
    # Registered last so the API routes above always win. Serves a real file
    # from dist/ when one exists at that path (hashed JS/CSS under /assets,
    # favicon, etc.), otherwise falls back to index.html for the Vue router
    # to handle client-side (so a hard refresh on /ace-step still works).
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        candidate = FRONTEND_DIST_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST_DIR / "index.html")
