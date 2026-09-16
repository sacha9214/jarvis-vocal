"""Pont local : les extensions Jarvis (navigateur, éditeur de code) se connectent ici par WebSocket.

Serveur sur 127.0.0.1, port fixe (les extensions le connaissent depuis leur installation), deux routes :
- `/bridge` pour les extensions de navigateur : l'en-tête Origin, qu'une page web ne peut pas falsifier,
  doit être celui d'une extension ;
- `/editor` pour l'extension VS Code : l'en-tête Origin doit être ABSENT (Node n'en envoie pas, un
  navigateur en envoie toujours un), ce qu'aucune page web ne peut obtenir.
Dans les deux cas, le client présente le jeton écrit sur cette machine. Si plusieurs clients d'un même
type sont reliés, Jarvis parle à celui qui a eu le focus en dernier.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

LOG = logging.getLogger("jarvis.bridge")
BROWSER, EDITOR = "browser", "editor"
_EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://", "extension://")
_LOCAL_CLIENTS = {"127.0.0.1", "::1"}
_LABELS = {BROWSER: "Navigateur", EDITOR: "Éditeur"}


class BrowserUnavailable(RuntimeError):
    """Aucun client relié de ce type, ou il ne répond pas."""


@dataclass
class Client:
    websocket: WebSocket
    kind: str
    name: str
    loop: asyncio.AbstractEventLoop
    focused: bool = False
    focused_at: float = field(default_factory=time.time)
    pending: dict[int, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)


class BrowserBridge:
    def __init__(self, token: str, on_change: Callable[[str, list[str]], None] | None = None):
        self.token = token
        self.on_change = on_change
        self.server = None
        self._clients: list[Client] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.app = Starlette(routes=[WebSocketRoute("/bridge", self._browser_endpoint),
                                     WebSocketRoute("/editor", self._editor_endpoint)])

    def names(self, kind: str = BROWSER) -> list[str]:
        with self._lock:
            return [client.name for client in self._clients if client.kind == kind]

    def browsers(self) -> list[str]:
        return self.names(BROWSER)

    def editors(self) -> list[str]:
        return self.names(EDITOR)

    def _changed(self, kind: str) -> None:
        if self.on_change:
            self.on_change(kind, self.names(kind))

    async def _browser_endpoint(self, websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin", "")
        await self._serve(websocket, BROWSER, origin.startswith(_EXTENSION_ORIGINS))

    async def _editor_endpoint(self, websocket: WebSocket) -> None:
        await self._serve(websocket, EDITOR, "origin" not in websocket.headers)

    async def _serve(self, websocket: WebSocket, kind: str, origin_ok: bool) -> None:
        host = websocket.client.host if websocket.client else ""
        if not origin_ok or host not in _LOCAL_CLIENTS:
            await websocket.close(code=4403)
            return
        await websocket.accept()
        try:
            hello = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        except (TimeoutError, WebSocketDisconnect, ValueError):
            await websocket.close(code=4400)
            return
        token = str(hello.get("token", "")) if isinstance(hello, dict) else ""
        if hello.get("type") != "hello" or not secrets.compare_digest(token.encode(), self.token.encode()):
            await websocket.close(code=4401)
            return
        name = str(hello.get("browser") or hello.get("app") or _LABELS[kind])
        client = Client(websocket, kind, name, asyncio.get_running_loop(), focused=bool(hello.get("focused")))
        with self._lock:
            self._clients.append(client)
        LOG.info("%s relié à Jarvis : %s", _LABELS[kind], client.name)
        self._changed(kind)
        try:
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "focus":
                    client.focused = bool(message.get("focused"))
                    if client.focused:
                        client.focused_at = time.time()
                elif "id" in message:
                    future = client.pending.pop(message["id"], None)
                    if future is not None and not future.done():
                        future.set_result(message)
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            with self._lock:
                if client in self._clients:
                    self._clients.remove(client)
            for future in client.pending.values():
                if not future.done():
                    future.set_exception(BrowserUnavailable(f"{_LABELS[kind]} déconnecté."))
            LOG.info("%s déconnecté : %s", _LABELS[kind], client.name)
            self._changed(kind)

    def pick(self, kind: str = BROWSER) -> Client | None:
        with self._lock:
            clients = [client for client in self._clients if client.kind == kind]
            if not clients:
                return None
            focused = [client for client in clients if client.focused]
            return max(focused or clients, key=lambda client: client.focused_at)

    def call(self, action: str, params: dict[str, Any] | None = None, timeout: float = 8.0,
             kind: str = BROWSER) -> dict[str, Any]:
        """Appelé depuis un fil quelconque (outil) ; exécuté sur la boucle du serveur."""
        client = self.pick(kind)
        if client is None:
            if kind == EDITOR:
                raise BrowserUnavailable("Aucun éditeur n'est relié à Jarvis : installe l'extension VS Code avec "
                                         "`jarvis code`.")
            raise BrowserUnavailable("Aucun navigateur n'est relié à Jarvis : installe l'extension avec "
                                     "`jarvis extension`.")
        future = asyncio.run_coroutine_threadsafe(self._request(client, action, params or {}, timeout), client.loop)
        return future.result(timeout + 2)

    async def _request(self, client: Client, action: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        request_id = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = client.loop.create_future()
        client.pending[request_id] = future
        try:
            await client.websocket.send_json({"id": request_id, "action": action, "params": params})
            message = await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise BrowserUnavailable(f"{_LABELS[client.kind]} n'a pas répondu à temps.") from None
        finally:
            client.pending.pop(request_id, None)
        if not message.get("ok"):
            raise RuntimeError(message.get("error") or f"Action refusée par {_LABELS[client.kind].lower()}.")
        return message.get("result") or {}


def start_bridge(token: str, port: int,
                 on_change: Callable[[str, list[str]], None] | None = None) -> BrowserBridge | None:
    from ..server import LocalServer

    bridge = BrowserBridge(token, on_change)
    try:
        bridge.server = LocalServer(bridge.app, token, port).start()
    except (OSError, RuntimeError) as exc:
        LOG.warning("Pont des extensions indisponible sur le port %d (%s) : pilotage du navigateur et de "
                    "l'éditeur désactivé.", port, exc)
        return None
    return bridge
