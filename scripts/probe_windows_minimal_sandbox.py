"""Isolated execute_code probe against an explicitly supplied Windows host Python."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from box_agent.tools import jupyter_tool as mod


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise RuntimeError("Windows-only probe")
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    mod.RUNTIME_PACKAGES_DIR = workspace / "packages"
    mod.SANDBOX_BASE_DIR = workspace / "sandbox"
    mod.JupyterSandboxTool._sandbox_env = None
    mod.JupyterSandboxTool._sessions = {}
    tool = mod.JupyterSandboxTool(workspace_dir=workspace, use_output_dir=False, runtime_env={
        "BOX_AGENT_SANDBOX_PYTHON": str(Path(args.python).resolve()),
        "BOX_AGENT_RUNTIME_PROFILE": "windows-minimal-v1",
    })
    try:
        first = await tool.execute(code="print(6 * 7)", session_id="offline-probe")
        assert first.success and "42" in first.content, first
        # Only this optional scenario probe needs a network wheel download.
        optional = await tool.execute(code="import tabulate; print(tabulate.tabulate([[1, 2]]))", session_id="offline-probe")
        assert optional.success, optional
        again = await tool.execute(code="print(tabulate.tabulate([[3, 4]]))", session_id="offline-probe")
        assert again.success, again
        print(json.dumps({"kernel": first.success, "first_optional_install": optional.success, "optional_reuse": again.success, "workspace": str(workspace), "implementation": mod.__file__}))
    finally:
        for session in list(tool._sessions.values()):
            await session.stop()


if __name__ == "__main__":
    asyncio.run(main())
