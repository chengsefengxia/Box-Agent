"""Regression coverage for the Windows-specific runtime builder."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

from scripts import build_runtime, build_win_runtime


def test_windows_builder_uses_shared_pyinstaller_contract() -> None:
    hidden = build_win_runtime._windows_pyinstaller_hidden_imports()
    collect = build_win_runtime._windows_pyinstaller_collect_args()

    assert hidden == build_runtime.pyinstaller_hidden_imports(
        external_python_sandbox=True
    )
    assert collect == build_runtime.pyinstaller_collect_args(
        external_python_sandbox=True
    )
    assert "box_agent.mcp_servers" in hidden
    assert "box_agent.mcp_servers.web_extract" in hidden
    assert "box_agent.mcp_servers.web_extract_server" in hidden
    assert "PIL.Image" in hidden
    assert "ipykernel" not in hidden
    assert "sklearn" not in collect


def test_windows_legacy_bundle_remains_explicitly_available() -> None:
    assert build_win_runtime._windows_pyinstaller_hidden_imports(
        external_python_sandbox=False
    ) == build_runtime.pyinstaller_hidden_imports(external_python_sandbox=False)
    assert build_win_runtime._windows_pyinstaller_collect_args(
        external_python_sandbox=False
    ) == build_runtime.pyinstaller_collect_args(external_python_sandbox=False)


def test_windows_pyinstaller_command_includes_web_extract_server(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: list[str] = []

    def fake_run(command, **kwargs):
        captured.extend(command)
        dist_path = Path(command[command.index("--distpath") + 1])
        output = dist_path / "box-agent-acp"
        output.mkdir(parents=True)
        (output / "box-agent-acp.exe").write_bytes(b"exe")
        return CompletedProcess(command, 0)

    monkeypatch.setattr(build_win_runtime.subprocess, "run", fake_run)
    bin_dir = tmp_path / "box-agent-runtime" / "bin"

    build_win_runtime._run_pyinstaller(bin_dir)

    hidden_pairs = list(zip(captured, captured[1:]))
    assert (
        "--hidden-import",
        "box_agent.mcp_servers.web_extract_server",
    ) in hidden_pairs
    assert (bin_dir / "box-agent-acp.exe").is_file()
    assert ("--exclude-module", "ipykernel") in hidden_pairs
    assert ("--exclude-module", "sklearn") in hidden_pairs
    assert ("--hidden-import", "PIL.Image") in hidden_pairs

    add_data_values = [
        captured[index + 1]
        for index, value in enumerate(captured[:-1])
        if value == "--add-data"
    ]
    assert any("box_agent/skills/browser-use" in value for value in add_data_values)
    assert not any(
        value.split(build_win_runtime.os.pathsep, 1)[0].endswith("box_agent\\skills")
        or value.split(build_win_runtime.os.pathsep, 1)[0].endswith("box_agent/skills")
        for value in add_data_values
    )


def test_windows_manifest_advertises_bundled_web_extract_mcp(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "box-agent-runtime"
    runtime_dir.mkdir()

    build_win_runtime._write_manifest(runtime_dir, "0.9.7")

    manifest = json.loads(
        (runtime_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["platform"] == "win32"
    assert manifest["arch"] == "x64"
    assert manifest["entry"] == "bin/box-agent-acp.exe"
    assert manifest["managed_mcp_config_version"] == 1
    assert manifest["connector_skill_sources_version"] == 1
    assert manifest["mcp_multi_source_version"] == 1
    assert "connector_mcp_proxy_version" not in manifest
    assert manifest["external_python_sandbox"] is True
    assert manifest["bundled_stable_runtimes"] == []
    assert manifest["mcp_servers"] == {
        "box-agent-web-extract": {
            "entry": "bin/box-agent-acp.exe",
            "args": ["--web-extract-mcp"],
            "transport": "stdio",
        }
    }
    assert (runtime_dir / "VERSION").read_text(encoding="utf-8") == "0.9.7\n"


def test_windows_legacy_manifest_lists_its_bundled_tools(tmp_path: Path) -> None:
    build_win_runtime._write_manifest(
        tmp_path, "0.9.7", external_python_sandbox=False
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["external_python_sandbox"] is False
    assert manifest["bundled_stable_runtimes"] == ["portable_git", "python", "node"]
