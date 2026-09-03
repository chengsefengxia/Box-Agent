from __future__ import annotations

import json
from pathlib import Path

from box_agent.tools.mcp_sources import configured_mcp_sources, resolve_mcp_sources


def _write(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_configured_sources_keep_standalone_single_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BOX_AGENT_USER_MCP_CONFIG_PATH", raising=False)
    monkeypatch.delenv("BOX_AGENT_SYSTEM_MCP_CONFIG_PATH", raising=False)
    monkeypatch.delenv("BOX_AGENT_CONNECTOR_MCP_CONFIG_PATH", raising=False)

    sources = configured_mcp_sources(str(tmp_path / "mcp.json"))

    assert [(source.owner, source.path) for source in sources] == [
        ("user", tmp_path / "mcp.json")
    ]


def test_resolve_sources_preserves_owner_and_blocks_user_shadowing(
    monkeypatch, tmp_path: Path
) -> None:
    system = tmp_path / "mcp.system.json"
    connector = tmp_path / "connector" / "mcp.json"
    user = tmp_path / "mcp.json"
    _write(system, {"playwright": {"command": "pw"}})
    _write(
        connector,
        {
            "law": {
                "url": "https://example.test/mcp",
                "_connectorId": "pkulaw",
                "credentialRef": "connector:pkulaw:default",
            }
        },
    )
    _write(user, {"playwright": {"command": "shadow"}, "mine": {"command": "mine"}})
    monkeypatch.setenv("BOX_AGENT_USER_MCP_CONFIG_PATH", str(user))
    monkeypatch.setenv("BOX_AGENT_SYSTEM_MCP_CONFIG_PATH", str(system))
    monkeypatch.setenv("BOX_AGENT_CONNECTOR_MCP_CONFIG_PATH", str(connector))

    resolved = resolve_mcp_sources(configured_mcp_sources(str(user)))

    assert resolved.servers["playwright"].owner == "system"
    assert resolved.servers["law"].config_id == "connector:pkulaw"
    assert resolved.servers["law"].connector_id == "pkulaw"
    assert resolved.servers["mine"].config_id == "custom-mcp:mine"
    assert resolved.conflicts == ("user:playwright conflicts with system:playwright",)


def test_credential_version_participates_in_server_fingerprint(tmp_path: Path) -> None:
    connector = tmp_path / "mcp.json"
    _write(
        connector,
        {"law": {"url": "https://example.test", "credentialRef": "law-token"}},
    )
    sources = (configured_mcp_sources(str(connector))[0],)

    before = resolve_mcp_sources(sources, {"law-token": 1}).servers["law"]
    after = resolve_mcp_sources(sources, {"law-token": 2}).servers["law"]

    assert before.fingerprint != after.fingerprint
