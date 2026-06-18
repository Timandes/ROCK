import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

from rock.sdk.mcp import rock_runtime
from rock.sdk.mcp.rock_runtime import RockRuntime, RockRuntimeConfig, RockRuntimeConfigError, RockRuntimeError


def test_rock_runtime_config_requires_api_key(monkeypatch):
    monkeypatch.delenv("ROCK_API_KEY", raising=False)
    monkeypatch.setenv("ROCK_USER_ID", "user-001")

    with pytest.raises(RockRuntimeConfigError, match="ROCK_API_KEY is required"):
        RockRuntimeConfig.from_env()


def test_rock_runtime_config_requires_user_id(monkeypatch):
    monkeypatch.setenv("ROCK_API_KEY", "rock-key")
    monkeypatch.delenv("ROCK_USER_ID", raising=False)

    with pytest.raises(RockRuntimeConfigError, match="ROCK_USER_ID is required"):
        RockRuntimeConfig.from_env()


def test_rock_runtime_config_reads_defaults_and_numeric_values(monkeypatch):
    monkeypatch.setenv("ROCK_API_KEY", "rock-key")
    monkeypatch.setenv("ROCK_USER_ID", "user-001")
    monkeypatch.delenv("ROCK_BASE_URL", raising=False)
    monkeypatch.delenv("ROCK_SANDBOX_IMAGE", raising=False)
    monkeypatch.delenv("ROCK_EXPERIMENT_ID", raising=False)
    monkeypatch.delenv("ROCK_CLUSTER", raising=False)
    monkeypatch.delenv("ROCK_SANDBOX_CPUS", raising=False)
    monkeypatch.delenv("ROCK_SANDBOX_MEMORY", raising=False)
    monkeypatch.delenv("ROCK_AUTO_CLEAR_SECONDS", raising=False)

    config = RockRuntimeConfig.from_env()

    assert config.api_key == "rock-key"
    assert config.user_id == "user-001"
    assert config.base_url == "https://xrl.alibaba-inc.com"
    assert config.image == "rock-instances-registry-vpc.cn-shanghai.cr.aliyuncs.com/instance/rock-mcp-base:v0.10.1"
    assert config.experiment_id == "mcpenv"
    assert config.cluster == "vpc-nt-a"
    assert config.cpus == 4.0
    assert config.memory == "8g"
    assert config.auto_clear_seconds == 3600


def test_rock_runtime_builds_server_urls_from_sandbox_id(monkeypatch):
    monkeypatch.setenv("ROCK_API_KEY", "rock-key")
    monkeypatch.setenv("ROCK_USER_ID", "user-001")
    monkeypatch.delenv("ROCK_BASE_URL", raising=False)
    runtime = RockRuntime(config=RockRuntimeConfig.from_env())
    runtime._sandbox_id = "sandbox-123"

    assert runtime.get_all_server_urls(["calculator", "slack"]) == {
        "calculator": "https://xrl.alibaba-inc.com/apis/envs/sandbox/v1/sandboxes/sandbox-123/proxy/calculator/sse",
        "slack": "https://xrl.alibaba-inc.com/apis/envs/sandbox/v1/sandboxes/sandbox-123/proxy/slack/sse",
    }


def test_rock_runtime_serializes_mcp_server_config(monkeypatch):
    monkeypatch.setenv("ROCK_API_KEY", "rock-key")
    monkeypatch.setenv("ROCK_USER_ID", "user-001")
    monkeypatch.delenv("ROCK_BASE_URL", raising=False)
    runtime = RockRuntime(config=RockRuntimeConfig.from_env())

    rendered = runtime.build_mcp_servers_json(
        {
            "calculator": {
                "command": "uvx",
                "args": ["mcp-server-calculator==0.2.0"],
            }
        }
    )

    assert json.loads(rendered) == {
        "mcpServers": {
            "calculator": {
                "command": "uvx",
                "args": ["mcp-server-calculator==0.2.0"],
            }
        }
    }


def test_rock_runtime_rejects_start_when_sandbox_is_already_started(monkeypatch):
    monkeypatch.setenv("ROCK_API_KEY", "rock-key")
    monkeypatch.setenv("ROCK_USER_ID", "user-001")
    monkeypatch.delenv("ROCK_BASE_URL", raising=False)
    runtime = RockRuntime(config=RockRuntimeConfig.from_env())
    runtime._sandbox_id = "sandbox-123"

    with pytest.raises(RockRuntimeError, match="ROCK runtime has already been started"):
        asyncio.run(runtime.start({}))


def test_rock_runtime_start_accepts_before_launch_hook():
    signature = inspect.signature(RockRuntime.start)

    assert "before_launch" in signature.parameters
    assert signature.parameters["before_launch"].default is None


def test_rock_runtime_exposes_raw_sandbox_property():
    assert isinstance(RockRuntime.sandbox, property)


def test_rock_runtime_start_preserves_before_launch_error_when_cleanup_fails(monkeypatch):
    class StopFailingSandbox:
        sandbox_id = "sandbox-123"

        def __init__(self, config):
            self.config = config

        async def start(self):
            pass

        async def execute(self, command):
            return SimpleNamespace(exit_code=0)

        async def write_file_by_path(self, content, path):
            pass

        async def stop(self):
            raise RuntimeError("stop failed")

    monkeypatch.setattr(rock_runtime, "Sandbox", StopFailingSandbox)
    runtime = RockRuntime(
        config=RockRuntimeConfig(
            base_url="https://xrl.alibaba-inc.com",
            api_key="rock-key",
            image="image",
            user_id="user-001",
            experiment_id="experiment",
            cluster="cluster",
            cpus=1.0,
            memory="1g",
            auto_clear_seconds=60,
        )
    )

    async def before_launch(_sandbox):
        raise RuntimeError("hook failed")

    with pytest.raises(RockRuntimeError, match="hook failed") as exc_info:
        asyncio.run(runtime.start({"calculator": {}}, before_launch=before_launch))

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "hook failed"
    assert runtime.sandbox is None
