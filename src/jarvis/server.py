"""Serveur HTTP local de Jarvis : outils MCP du moteur Claude, et interface.

N'écoute que 127.0.0.1, refuse tout nom d'hôte étranger (attaque par DNS rebinding) et exige
le jeton aléatoire de la session : en-tête Bearer pour MCP, cookie de session pour l'interface
(posé à l'ouverture de la fenêtre), et en-tête X-Jarvis sur toute écriture (contre le CSRF).
"""
from __future__ import annotations

import logging
import secrets
import socket
import sys
import threading
import time
from http.cookies import CookieError, SimpleCookie
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

import uvicorn
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .tools import ToolExecutor
from .tools.mcp_server import build_mcp

if TYPE_CHECKING:
    from .ui.controller import Controller

LOG = logging.getLogger("jarvis.server")
SESSION_COOKIE = "jarvis_session"
_LOCAL_HOSTS = {"127.0.0.1", "localhost"}
_LOCAL_CLIENTS = {"127.0.0.1", "::1"}


class Gateway:
    def __init__(self, mcp_app: ASGIApp, ui_app: ASGIApp | None, token: str):
        self.mcp_app = mcp_app
        self.ui_app = ui_app
        self.token = token

    def _matches(self, value: str) -> bool:
        return secrets.compare_digest(value.encode("utf-8"), self.token.encode("utf-8"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.mcp_app(scope, receive, send)
            return
        if scope["type"] not in ("http", "websocket"):
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "").rsplit(":", 1)[0]
        client = (scope.get("client") or ("",))[0]
        if host not in _LOCAL_HOSTS or client not in _LOCAL_CLIENTS:
            await _deny(scope, receive, send)
            return
        authorization = headers.get("authorization", "")
        bearer = authorization.startswith("Bearer ") and self._matches(authorization[7:])
        path = scope["path"]

        if path == "/mcp" or path.startswith("/mcp/"):
            if not bearer:
                await _deny(scope, receive, send)
                return
            await self.mcp_app(scope, receive, send)
            return

        if self.ui_app is None:
            await PlainTextResponse("Introuvable.", status_code=404)(scope, receive, send)
            return
        authorized = bearer or self._session_ok(headers.get("cookie", ""))
        if not authorized and path == "/":
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            authorized = self._matches((query.get("t") or [""])[0])
        if not authorized or (scope.get("method", "GET") not in ("GET", "HEAD") and headers.get("x-jarvis") != "1"):
            await _deny(scope, receive, send)
            return
        await self.ui_app(scope, receive, send)

    def _session_ok(self, cookie_header: str) -> bool:
        try:
            morsel = SimpleCookie(cookie_header).get(SESSION_COOKIE)
        except CookieError:
            return False
        return morsel is not None and self._matches(morsel.value)


async def _deny(scope: Scope, receive: Receive, send: Send) -> None:
    await PlainTextResponse("Accès refusé.", status_code=403)(scope, receive, send)


class LocalServer:
    def __init__(self, app: ASGIApp, token: str, port: int = 0):
        self.token = token
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform != "win32":   # sous Windows, SO_REUSEADDR laisserait un autre programme s'y greffer
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", port))
        self.port = self._socket.getsockname()[1]
        self._server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
        self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [self._socket]},
                                        name="serveur-local", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def mcp_url(self) -> str:
        return f"{self.url}/mcp"

    @property
    def ui_url(self) -> str:
        return f"{self.url}/?t={self.token}"

    def start(self, timeout: float = 10.0) -> LocalServer:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("Le serveur local de Jarvis n'a pas démarré.")
            time.sleep(0.02)
        LOG.debug("Serveur local sur %s", self.url)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def start_server(executor: ToolExecutor, port: int = 0, controller: Controller | None = None) -> LocalServer:
    token = secrets.token_urlsafe(32)
    mcp_app = build_mcp(executor).streamable_http_app(streamable_http_path="/mcp", json_response=True,
                                                      stateless_http=True, host="127.0.0.1")
    ui_app = None
    if controller is not None:
        from .ui.web import build_ui_app
        ui_app = build_ui_app(controller, token)
    return LocalServer(Gateway(mcp_app, ui_app, token), token, port).start()
