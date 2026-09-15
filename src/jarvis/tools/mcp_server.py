"""Outils de Jarvis exposés en MCP au moteur Claude : chaque appel passe par le même
ToolExecutor que la voix (niveaux N1/N2/N3, confirmation vocale)."""
from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer

from . import Tool, ToolExecutor


def _as_async(executor: ToolExecutor, tool_: Tool) -> Callable[..., Awaitable[str]]:
    @functools.wraps(tool_.handler)
    async def call(**arguments: Any) -> str:
        # Dans un fil à part : une confirmation vocale peut attendre plusieurs secondes sans
        # bloquer la boucle du serveur.
        return await anyio.to_thread.run_sync(executor.run, tool_.name, arguments)
    return call


def build_mcp(executor: ToolExecutor) -> MCPServer:
    server = MCPServer(name="jarvis", instructions="Actions sur l'ordinateur de l'utilisateur, via Jarvis.")
    for tool_ in executor.tools():
        server.add_tool(_as_async(executor, tool_), name=tool_.name, description=tool_.description)
    return server
