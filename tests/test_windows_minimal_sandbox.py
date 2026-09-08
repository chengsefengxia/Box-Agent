from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from box_agent.tools import jupyter_tool as mod


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
@pytest.mark.parametrize("profile", ["windows-minimal-v1", ""])
def test_description_changes_only_for_windows_profile(monkeypatch, platform, profile):
    monkeypatch.setattr(mod.sys, "platform", platform)
    monkeypatch.setenv("BOX_AGENT_RUNTIME_PROFILE", profile)
    minimal = platform == "win32" and profile == "windows-minimal-v1"
    description = mod.JupyterSandboxTool().description
    assert ("NOT guaranteed" in description) == minimal
    assert ("Pre-installed packages: pandas" in description) != minimal


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
@pytest.mark.parametrize("profile", ["windows-minimal-v1", ""])
def test_first_kernel_package_path_is_windows_profile_only(
    monkeypatch, tmp_path, platform, profile
):
    monkeypatch.setattr(mod.sys, "platform", platform)
    monkeypatch.delenv("BOX_AGENT_RUNTIME_PROFILE", raising=False)
    missing = tmp_path / "not-created-yet"
    monkeypatch.setattr(mod, "RUNTIME_PACKAGES_DIR", missing)
    env = mod.SandboxEnvironment(
        base_dir=tmp_path, runtime_env={"BOX_AGENT_RUNTIME_PROFILE": profile}
    )
    env._bundled_override = True
    spec = env.get_kernel_spec()
    if platform == "win32" and profile == "windows-minimal-v1":
        assert spec["env"]["PYTHONPATH"] == str(missing)
    else:
        assert "env" not in spec
    assert not missing.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
@pytest.mark.parametrize("profile", ["windows-minimal-v1", ""])
async def test_optional_install_constraints_are_windows_profile_only(
    monkeypatch, tmp_path, platform, profile
):
    monkeypatch.setattr(mod.sys, "platform", platform)
    monkeypatch.setenv("BOX_AGENT_RUNTIME_PROFILE", profile)
    monkeypatch.setattr(mod, "RUNTIME_PACKAGES_DIR", tmp_path / "packages")
    env = mod.SandboxEnvironment(base_dir=tmp_path)
    env.python_path = tmp_path / "runtime/python/python.exe"
    constraints = tmp_path / "python-core-constraints.txt"
    constraints.write_text("ipykernel==6.29.5\n", encoding="utf-8")
    captured = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def create(*args, **kwargs):
        captured.extend(args)
        return Process()

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", create)
    ok, _ = await env._runtime_python_install(["tabulate"], enforce_allowlist=True)
    assert ok
    if platform == "win32" and profile == "windows-minimal-v1":
        assert captured[captured.index("--constraint") + 1] == str(constraints)
    else:
        assert "--constraint" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
@pytest.mark.parametrize("profile", ["windows-minimal-v1", ""])
async def test_missing_package_retry_invalidates_cache_only_for_windows_profile(
    monkeypatch, tmp_path, platform, profile
):
    monkeypatch.setattr(mod.sys, "platform", platform)
    monkeypatch.delenv("BOX_AGENT_RUNTIME_PROFILE", raising=False)
    tool = mod.JupyterSandboxTool(runtime_env={"BOX_AGENT_RUNTIME_PROFILE": profile})
    session = SimpleNamespace(workspace=tmp_path, is_alive=lambda: True)
    monkeypatch.setattr(tool, "_sessions", {"test": session})
    env = SimpleNamespace(
        ensure_ready=AsyncMock(), install_packages=AsyncMock(return_value=(True, ""))
    )
    monkeypatch.setattr(tool, "_get_sandbox_env", lambda: env)
    execute = Mock(side_effect=[
        ("", [], "ModuleNotFoundError: No module named 'tabulate'"), ("ok", [], None)
    ])
    monkeypatch.setattr(tool, "_execute_session_code", execute)

    result = await tool.execute(code="import tabulate", session_id="test")

    assert result.success
    env.install_packages.assert_awaited_once_with(["tabulate"])
    assert execute.call_count == 2
    retried_code = execute.call_args.args[1]
    minimal = platform == "win32" and profile == "windows-minimal-v1"
    assert ("importlib.invalidate_caches()" in retried_code) == minimal
    assert retried_code.endswith("import tabulate")


@pytest.mark.asyncio
async def test_failed_optional_install_remains_a_tool_error_without_retry_loop(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(mod.sys, "platform", "win32")
    tool = mod.JupyterSandboxTool(runtime_env={"BOX_AGENT_RUNTIME_PROFILE": "windows-minimal-v1"})
    session = SimpleNamespace(workspace=tmp_path, is_alive=lambda: True)
    monkeypatch.setattr(tool, "_sessions", {"test": session})
    env = SimpleNamespace(
        ensure_ready=AsyncMock(), install_packages=AsyncMock(return_value=(False, "offline"))
    )
    monkeypatch.setattr(tool, "_get_sandbox_env", lambda: env)
    execute = Mock(return_value=("", [], "ModuleNotFoundError: No module named 'tabulate'"))
    monkeypatch.setattr(tool, "_execute_session_code", execute)

    result = await tool.execute(code="import tabulate", session_id="test")

    assert not result.success
    assert "ModuleNotFoundError" in result.error
    env.install_packages.assert_awaited_once_with(["tabulate"])
    assert execute.call_count == 1
    assert tool._sessions["test"] is session
