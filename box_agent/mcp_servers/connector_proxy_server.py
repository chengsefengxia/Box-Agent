"""Aggregate system, official Connector, and user MCP sources behind one server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from box_agent.auth import request_auth_headers
from box_agent.tools.mcp_loader import (
    MCPServerConnection,
    MCPTool,
    _determine_connection_type,
    _dynamic_bearer_auth_for_url,
)

Owner = Literal["system", "connector", "user"]


@dataclass(frozen=True)
class DesiredServer:
    config_id: str
    owner: Owner
    connector_id: str | None
    name: str
    config: dict[str, Any]


def _read_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"mcpServers": {}}
    if not isinstance(value, dict) or not isinstance(value.get("mcpServers", {}), dict):
        raise ValueError(f"Invalid MCP config: {path}")
    return value


def build_desired_servers(
    system_config: Path,
    connector_config: Path,
    user_config: Path,
) -> list[DesiredServer]:
    desired: list[DesiredServer] = []
    sources: tuple[tuple[Owner, Path], ...] = (
        ("system", system_config),
        ("connector", connector_config),
        ("user", user_config),
    )
    for owner, source in sources:
        for name, raw in _read_config(source).get("mcpServers", {}).items():
            if not isinstance(raw, dict):
                continue
            connector_id = str(raw.get("_connectorId") or "").strip() or None
            if owner == "connector" and not connector_id:
                connector_id = name
            if owner == "connector":
                config_id = f"connector:{connector_id}"
            elif owner == "user":
                config_id = f"custom-mcp:{name}"
            else:
                config_id = f"system:{name}"
            desired.append(
                DesiredServer(
                    config_id=config_id,
                    owner=owner,
                    connector_id=connector_id,
                    name=name,
                    config=raw,
                )
            )
    return desired


def _status(server: DesiredServer, state: str, **updates: Any) -> dict[str, Any]:
    config = server.config
    return {
        "configId": server.config_id,
        "owner": server.owner,
        "connectorId": server.connector_id,
        "name": server.name,
        "state": state,
        "transport": str(config.get("url") or config.get("command") or ""),
        "toolCount": 0,
        "tools": [],
        "error": None,
        **updates,
    }


def _write_status(path: Path, statuses: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(statuses, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _proxy_tool(server: DesiredServer, tool: MCPTool) -> types.Tool:
    metadata = {
        "boxAgent": {
            "alwaysLoad": bool(server.config.get("alwaysLoad", False)),
            "configId": server.config_id,
            "connectorId": server.connector_id,
            "owner": server.owner,
            "upstreamServer": server.name,
        }
    }
    return types.Tool(
        name=tool.name,
        description=tool.description,
        inputSchema=tool.parameters,
        **{"_meta": metadata},
    )


def _connection(server: DesiredServer, auth_file: str) -> MCPServerConnection:
    config = server.config
    connection_type = _determine_connection_type(config)
    url = config.get("url")
    headers = config.get("headers", {}) if isinstance(config.get("headers", {}), dict) else {}
    auth = _dynamic_bearer_auth_for_url(
        url=url,
        headers=headers,
        auth_file=auth_file,
        auth_token="",
    )
    connection_headers = headers if auth is not None else request_auth_headers(
        auth_file=auth_file,
        explicit_token="",
        existing=headers,
        url=url,
    )
    return MCPServerConnection(
        name=server.name,
        connection_type=connection_type,
        command=config.get("command"),
        args=config.get("args", []),
        env=config.get("env", {}),
        url=url,
        headers=connection_headers,
        auth=auth,
        connect_timeout=config.get("connect_timeout"),
        execute_timeout=config.get("execute_timeout"),
        sse_read_timeout=config.get("sse_read_timeout"),
        always_load=True,
    )


async def run_proxy(
    *,
    system_config: Path,
    connector_config: Path,
    user_config: Path,
    status_file: Path,
    auth_file: Path,
) -> None:
    desired = build_desired_servers(system_config, connector_config, user_config)
    statuses = [
        _status(item, "disabled" if item.config.get("disabled", False) else "connecting")
        for item in desired
    ]
    _write_status(status_file, statuses)

    active = [item for item in desired if not item.config.get("disabled", False)]
    connections = [_connection(item, str(auth_file)) for item in active]
    results = await asyncio.gather(*(item.connect() for item in connections), return_exceptions=True)

    route_candidates: dict[str, list[tuple[DesiredServer, MCPTool]]] = {}
    status_by_name = {(item.owner, item.name): status for item, status in zip(desired, statuses)}
    connected: list[MCPServerConnection] = []
    for item, connection, result in zip(active, connections, results):
        current = status_by_name[(item.owner, item.name)]
        if result is True:
            connected.append(connection)
            current.update(
                state="connected",
                toolCount=len(connection.tools),
                tools=[tool.name for tool in connection.tools],
            )
            for tool in connection.tools:
                route_candidates.setdefault(tool.name, []).append((item, tool))
        else:
            error = str(result) if isinstance(result, BaseException) else connection.last_error
            current.update(state="failed", error=error or "MCP handshake failed")

    routes: dict[str, tuple[DesiredServer, MCPTool]] = {}
    for tool_name, candidates in route_candidates.items():
        if len(candidates) == 1:
            routes[tool_name] = candidates[0]
            continue
        identities = ", ".join(item.config_id for item, _ in candidates)
        for item, _ in candidates:
            current = status_by_name[(item.owner, item.name)]
            current["state"] = "failed"
            current["error"] = f"Tool name conflict: {tool_name} ({identities})"
            current["tools"] = [name for name in current["tools"] if name != tool_name]
            current["toolCount"] = len(current["tools"])

    _write_status(status_file, statuses)
    server = Server("connector-proxy", version="1")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [_proxy_tool(upstream, tool) for upstream, tool in routes.values()]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        route = routes.get(name)
        if route is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown or ambiguous tool: {name}")],
                isError=True,
            )
        _, tool = route
        result = await tool.execute(**arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.content or result.error or "")],
            isError=not result.success,
        )

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        await asyncio.gather(*(item.disconnect() for item in connected), return_exceptions=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="connector-proxy-mcp")
    parser.add_argument("--system-config", type=Path, required=True)
    parser.add_argument("--connector-config", type=Path, required=True)
    parser.add_argument("--user-config", type=Path, required=True)
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--auth-file", type=Path)
    args = parser.parse_args(argv)
    if args.auth_file is None:
        args.auth_file = args.status_file.parent / "auth.json"
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    asyncio.run(
        run_proxy(
            system_config=args.system_config,
            connector_config=args.connector_config,
            user_config=args.user_config,
            status_file=args.status_file,
            auth_file=args.auth_file,
        )
    )


if __name__ == "__main__":
    main()
