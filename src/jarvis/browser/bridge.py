"""Pont navigateur : l'extension Jarvis se connecte ici par WebSocket.

Serveur sur 127.0.0.1, port fixe (l'extension le connaît depuis son installation). Seules les
extensions sont acceptées : l'en-tête Origin, qu'une page web ne peut pas falsifier, doit être celui
d'une extension, et chaque extension présente le jeton écrit dans sa configuration.
Si plusieurs navigateurs sont reliés, Jarvis parle à celui qui a eu le focus en dernier.
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

LOG = logging.getLogger("jarvis.browser")
_EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://", "extension://")
_LOCAL_CLIENTS = {"127.0.0.1", "::1"}


class BrowserUnavailable(RuntimeError):
    """Aucun navigateur relié, ou il ne répond pas."""


@dataclass
class Client:
    websocket: WebSocket
    browser: str
    loop: asyncio.AbstractEventLoop
    focused: bool = False
    focused_at: float = field(default_factory=time.time)
    pending: dict[int, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)


class BrowserBridge:
    def __init__(self, token: str, on_change: Callable[[list[str]], None] | None = None):
        self.token = token
        self.on_change = on_change
        self.server = None
        self._clients: list[Client] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.app = Starlette(routes=[WebSocketRoute("/bridge", self._endpoint)])

    def browsers(self) -> list[str]:
        with self._lock:
            return [client.browser for client in self._clients]

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(self.browsers())

    async def _endpoint(self, websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin", "")
        host = websocket.client.host if websocket.client else ""
        if not origin.startswith(_EXTENSION_ORIGINS) or host not in _LOCAL_CLIENTS:
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
        client = Client(websocket, str(hello.get("browser") or "Navigateur"), asyncio.get_running_loop(),
                        focused=bool(hello.get("focused")))
        with self._lock:
            self._clients.append(client)
        LOG.info("Navigateur relié à Jarvis : %s", client.browser)
        self._changed()
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
                    future.set_exception(BrowserUnavailable("Le navigateur s'est déconnecté."))
            LOG.info("Navigateur déconnecté : %s", client.browser)
            self._changed()

    def pick(self) -> Client | None:
        with self._lock:
            if not self._clients:
                return None
            focused = [client for client in self._clients if client.focused]
            return max(focused or self._clients, key=lambda client: client.focused_at)

    def call(self, action: str, params: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
        """Appelé depuis un fil quelconque (outil) ; exécuté sur la boucle du serveur."""
        client = self.pick()
        if client is None:
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
            raise BrowserUnavailable("Le navigateur n'a pas répondu à temps.") from None
        finally:
            client.pending.pop(request_id, None)
        if not message.get("ok"):
            raise RuntimeError(message.get("error") or "Action refusée par le navigateur.")
        return message.get("result") or {}


def start_bridge(token: str, port: int, on_change: Callable[[list[str]], None] | None = None) -> BrowserBridge | None:
    from ..server import LocalServer

    bridge = BrowserBridge(token, on_change)
    try:
        bridge.server = LocalServer(bridge.app, token, port).start()
    except (OSError, RuntimeError) as exc:
        LOG.warning("Pont navigateur indisponible sur le port %d (%s) : pilotage du navigateur désactivé.", port, exc)
        return None
    return bridge
