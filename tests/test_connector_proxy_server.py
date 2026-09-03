import json
from pathlib import Path
from types import SimpleNamespace

from box_agent.mcp_servers.connector_proxy_server import (
    _proxy_tool,
    build_desired_servers,
    parse_args,
)


def _write(path: Path, servers: dict) -> None:
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_build_desired_servers_keeps_physical_owners_and_runtime_ids(tmp_path: Path) -> None:
    system = tmp_path / "system.json"
    connector = tmp_path / "connector.json"
    user = tmp_path / "user.json"
    _write(system, {"playwright": {"command": "pw"}})
    _write(connector, {"law": {"url": "https://example.test", "_connectorId": "pkulaw"}})
    _write(user, {"mine": {"command": "mine"}})

    desired = build_desired_servers(system, connector, user)

    assert [(item.owner, item.config_id) for item in desired] == [
        ("system", "system:playwright"),
        ("connector", "connector:pkulaw"),
        ("user", "custom-mcp:mine"),
    ]


def test_parse_args_defaults_auth_file_next_to_status(tmp_path: Path) -> None:
    status = tmp_path / "connector-proxy-status.json"
    args = parse_args(
        [
            "--system-config", str(tmp_path / "system.json"),
            "--connector-config", str(tmp_path / "connector.json"),
            "--user-config", str(tmp_path / "user.json"),
            "--status-file", str(status),
        ]
    )
    assert args.auth_file == tmp_path / "auth.json"


def test_proxy_tool_preserves_upstream_deferred_loading_policy(tmp_path: Path) -> None:
    system = tmp_path / "system.json"
    connector = tmp_path / "connector.json"
    user = tmp_path / "user.json"
    _write(system, {})
    _write(connector, {"law": {"url": "https://example.test", "alwaysLoad": False}})
    _write(user, {})
    upstream = build_desired_servers(system, connector, user)[0]

    exposed = _proxy_tool(
        upstream,
        SimpleNamespace(name="mcp__law__search", description="Search", parameters={}),
    )

    assert exposed.meta == {
        "boxAgent": {
            "alwaysLoad": False,
            "configId": "connector:law",
            "connectorId": "law",
            "owner": "connector",
            "upstreamServer": "law",
        }
    }
