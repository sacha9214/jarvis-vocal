"""Application web de l'interface : page, fichiers statiques, API et flux d'événements (SSE)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from .controller import Controller

STATIC = (Path(__file__).parent / "static").resolve()
SESSION_COOKIE = "jarvis_session"


def build_ui_app(controller: Controller, token: str) -> Starlette:
    async def index(request: Request) -> Response:
        response = FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", path="/")
        return response

    async def static(request: Request) -> Response:
        target = (STATIC / request.path_params["path"]).resolve()
        if STATIC not in target.parents or not target.is_file():
            return PlainTextResponse("Introuvable.", status_code=404)
        return FileResponse(target, headers={"Cache-Control": "no-cache"})

    async def state(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(controller.state))

    async def machine_status(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(controller.status))

    async def update_config(request: Request) -> Response:
        try:
            body = await request.json()
            result = await run_in_threadpool(controller.update_config, body.get("updates"))
        except (ValueError, TypeError, AttributeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    async def action(request: Request) -> Response:
        try:
            body = await request.json()
            result = await run_in_threadpool(controller.action, str(body.get("action")), body.get("value"))
        except (ValueError, TypeError, AttributeError, RuntimeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    async def events(request: Request) -> Response:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)

        def push(event: dict[str, Any]) -> None:
            def put() -> None:
                if not queue.full():     # client lent : on saute des niveaux audio plutôt que de gonfler
                    queue.put_nowait(event)
            try:
                loop.call_soon_threadsafe(put)
            except RuntimeError:
                pass

        unsubscribe = controller.bus.subscribe(push)

        async def stream():
            try:
                yield ": connecté\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            finally:
                unsubscribe()

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return Starlette(routes=[
        Route("/", index),
        Route("/static/{path:path}", static),
        Route("/api/state", state),
        Route("/api/status", machine_status),
        Route("/api/config", update_config, methods=["POST"]),
        Route("/api/action", action, methods=["POST"]),
        Route("/api/events", events),
    ])
