from __future__ import annotations

import json
from pathlib import Path

import pytest

from box_agent.tools import mcp_loader
from box_agent.tools.mcp_sources import McpConfigSource, configured_mcp_sources, resolve_mcp_sources


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
                "_connectorName": "北大法宝",
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
    assert resolved.servers["law"].connector_name == "北大法宝"
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


def test_reserved_official_name_is_rejected_even_when_connector_is_disconnected(
    tmp_path: Path,
) -> None:
    user = tmp_path / "mcp.json"
    _write(user, {"pkulaw": {"url": "https://evil.test/mcp"}})

    resolved = resolve_mcp_sources(
        (configured_mcp_sources(str(user))[0],),
        reserved_names={"pkulaw"},
    )

    assert "pkulaw" not in resolved.servers
    assert resolved.conflicts == ("user:pkulaw uses a protected server name",)


def test_connector_source_requires_a_normalized_connector_id(tmp_path: Path) -> None:
    connector = tmp_path / "connector" / "mcp.json"
    _write(connector, {"law": {"url": "https://example.test/mcp", "_connectorId": " PKULAW "}})

    source = McpConfigSource("connector", connector)
    resolved = resolve_mcp_sources((source,))
    assert resolved.servers["law"].connector_id == "pkulaw"

    _write(connector, {"law": {"url": "https://example.test/mcp", "_connectorId": "bad.id"}})
    with pytest.raises(ValueError, match="invalid _connectorId"):
        resolve_mcp_sources((source,))


def test_runtime_connector_source_override_does_not_require_a_physical_file(
    tmp_path: Path,
) -> None:
    user = tmp_path / "mcp.json"
    _write(user, {"mine": {"command": "mine"}})
    connector = McpConfigSource("connector", Path("<runtime:connector>"))

    resolved = resolve_mcp_sources(
        (connector, McpConfigSource("user", user)),
        source_server_overrides={
            "connector": {
                "law": {
                    "url": "https://example.test/mcp",
                    "_connectorId": "pkulaw",
                    "_connectorName": "北大法宝",
                }
            }
        },
    )

    assert resolved.servers["law"].owner == "connector"
    assert resolved.servers["law"].source_path == "<runtime:connector>"
    assert resolved.servers["mine"].owner == "user"


def test_loader_registers_runtime_connector_source_without_connector_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = tmp_path / "mcp.json"
    _write(user, {"mine": {"command": "mine"}})
    monkeypatch.setenv("BOX_AGENT_USER_MCP_CONFIG_PATH", str(user))
    monkeypatch.delenv("BOX_AGENT_SYSTEM_MCP_CONFIG_PATH", raising=False)
    monkeypatch.delenv("BOX_AGENT_CONNECTOR_MCP_CONFIG_PATH", raising=False)
    monkeypatch.delenv("BOX_AGENT_RESERVED_MCP_SERVER_NAMES", raising=False)
    monkeypatch.setattr(
        mcp_loader,
        "_mcp_source_overrides",
        {
            "connector": {
                "law": {
                    "url": "https://example.test/mcp",
                    "_connectorId": "pkulaw",
                }
            }
        },
    )

    resolved = mcp_loader._resolve_registered_sources(str(user))

    assert resolved["law"].owner == "connector"
    assert resolved["law"].source_path == "<runtime:connector>"
    assert resolved["mine"].owner == "user"


@pytest.mark.asyncio
async def test_replace_runtime_connector_source_reconciles_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_loader, "_mcp_source_overrides", {})
    captured: list[str | None] = []

    async def reconcile(source: str | None) -> dict:
        captured.append(source)
        return {"success": True, "source": source, "results": []}

    monkeypatch.setattr(mcp_loader, "_reconcile_mcp_sources_locked", reconcile)

    result = await mcp_loader.replace_mcp_source(
        "connector",
        {
            "mcpServers": {
                "law": {
                    "url": "https://example.test/mcp",
                    "_connectorId": "pkulaw",
                }
            }
        },
    )

    assert result["success"] is True
    assert captured == ["connector"]
    assert set(mcp_loader._mcp_source_overrides["connector"]) == {"law"}
