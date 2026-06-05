import asyncio
import os

import httpx
import pytest

from rock.actions import Command
from rock.sdk.mcp import McpEnv

pytestmark = pytest.mark.integration


def calculator_server_config() -> dict:
    return {
        "command": "uvx",
        "args": [
            "mcp-server-calculator==0.2.0",
        ],
    }


def require_rock_credentials():
    missing = [name for name in ("ROCK_API_KEY", "ROCK_USER_ID") if not os.getenv(name, "").strip()]
    if missing:
        pytest.skip(f"Missing ROCK credentials: {', '.join(missing)}")


async def run_real_rock_calculator_server_case():
    require_rock_credentials()
    env = McpEnv(servers={"calculator": calculator_server_config()})

    try:
        await env.start()
        assert env.is_alive() is True

        env.init({})
        urls = env.get_urls()

        assert set(urls) == {"calculator"}
        assert urls["calculator"].endswith("/calculator/sse")

        headers = {"XRL-Authorization": f"Bearer {os.environ['ROCK_API_KEY']}"}
        with httpx.stream("GET", urls["calculator"], headers=headers, timeout=10.0) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")
    finally:
        if env.is_alive():
            await env.release()

    assert env.is_alive() is False


def test_mcp_env_starts_real_rock_calculator_server_and_returns_sse_url():
    asyncio.run(run_real_rock_calculator_server_case())


async def run_real_rock_async_before_launch_case():
    require_rock_credentials()
    env = McpEnv(servers={"calculator": calculator_server_config()})
    marker_path = "/data/scaffoldhub-before-launch-async.txt"
    seen_sandbox_ids: list[str] = []

    async def before_launch(sandbox):
        seen_sandbox_ids.append(sandbox.sandbox_id)
        await sandbox.write_file_by_path(content="async hook was here", path=marker_path)

    try:
        await env.start(before_launch=before_launch)
        env.init({})

        assert env.is_alive() is True
        assert env.sandbox is not None
        assert seen_sandbox_ids == [env.sandbox.sandbox_id]

        result = await env.sandbox.execute(Command(command=["bash", "-c", f"test -f {marker_path}"]))
        assert result.exit_code == 0
        assert env.get_urls()["calculator"].endswith("/calculator/sse")
    finally:
        if env.is_alive():
            await env.release()


def test_mcp_env_async_before_launch_receives_real_sandbox_and_runs_before_health_check():
    asyncio.run(run_real_rock_async_before_launch_case())


async def run_real_rock_sync_before_launch_case():
    require_rock_credentials()
    env = McpEnv(servers={"calculator": calculator_server_config()})
    seen_sandbox_ids: list[str] = []

    def before_launch(sandbox):
        seen_sandbox_ids.append(sandbox.sandbox_id)

    try:
        await env.start(before_launch=before_launch)
        env.init({})

        assert env.is_alive() is True
        assert env.sandbox is not None
        assert seen_sandbox_ids == [env.sandbox.sandbox_id]
    finally:
        if env.is_alive():
            await env.release()


def test_mcp_env_sync_before_launch_receives_real_sandbox():
    asyncio.run(run_real_rock_sync_before_launch_case())
