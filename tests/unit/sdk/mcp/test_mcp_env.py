import asyncio
import importlib
import inspect
import sys
from copy import deepcopy
from types import ModuleType

import pytest


class RecordingRuntime:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class FailingStopRuntime:
    async def stop(self):
        raise RuntimeError("stop failed")


class RecordingDataLifecycle:
    def __init__(self):
        self.initialized_data = {}
        self.reset_calls = 0

    def init(self, data: dict) -> None:
        self.initialized_data = deepcopy(data)

    def dump(self) -> dict:
        return deepcopy(self.initialized_data)

    def reset(self) -> None:
        self.reset_calls += 1
        self.initialized_data = {}


class FakeAuthProvider:
    def __init__(self):
        self.auth = {
            "slack": {
                "SLACK_MCP_XOXP_TOKEN": "xoxp-test-token",
                "SLACK_MCP_XOXB_TOKEN": "xoxb-test-token",
            }
        }
        self.release_active_leases_calls = 0

    def provide(self, platform: str) -> dict:
        if platform not in self.auth:
            raise ValueError(f"Unsupported platform: {platform}")
        return self.auth[platform]

    def release_active_leases(self) -> None:
        self.release_active_leases_calls += 1


class FakeDataLifecycleFactory:
    last_auth_provider = None

    def __init__(self, auth_provider=None):
        self.auth_provider = auth_provider or FakeAuthProvider()
        FakeDataLifecycleFactory.last_auth_provider = self.auth_provider
        self.created = {}

    def supports(self, lifecycle_type: str) -> bool:
        return lifecycle_type == "slack"

    def create(self, lifecycle_type: str):
        if lifecycle_type != "slack":
            raise ValueError(f"Unsupported data lifecycle type: {lifecycle_type}")
        lifecycle = RecordingDataLifecycle()
        self.created[lifecycle_type] = lifecycle
        return lifecycle


class FailingReleaseAuthProvider(FakeAuthProvider):
    def release_active_leases(self) -> None:
        self.release_active_leases_calls += 1
        raise RuntimeError("database release failed")


def install_fake_scaffoldhub(monkeypatch):
    scaffoldhub = ModuleType("scaffoldhub")
    auth = ModuleType("scaffoldhub.auth")
    tools = ModuleType("scaffoldhub.tools")
    base = ModuleType("scaffoldhub.tools.base")
    auth.AuthProvider = FakeAuthProvider
    base.DataLifecycleFactory = FakeDataLifecycleFactory

    monkeypatch.setitem(sys.modules, "scaffoldhub", scaffoldhub)
    monkeypatch.setitem(sys.modules, "scaffoldhub.auth", auth)
    monkeypatch.setitem(sys.modules, "scaffoldhub.tools", tools)
    monkeypatch.setitem(sys.modules, "scaffoldhub.tools.base", base)


def reload_mcp_env(monkeypatch):
    install_fake_scaffoldhub(monkeypatch)
    sys.modules.pop("rock.sdk.mcp.mcp_env", None)
    module = importlib.import_module("rock.sdk.mcp.mcp_env")
    return importlib.reload(module)


def slack_server_config() -> dict:
    return {
        "command": "npx",
        "args": [
            "slack-mcp-server@1.1.23",
            "--transport",
            "stdio",
        ],
        "env": {
            "SLACK_MCP_XOXP_TOKEN": "${SLACK_MCP_XOXP_TOKEN}",
            "SLACK_MCP_XOXB_TOKEN": "${SLACK_MCP_XOXB_TOKEN}",
            "STATIC_VALUE": "unchanged",
            "PARTIAL_TEMPLATE": "token-${SLACK_MCP_XOXP_TOKEN}",
            "UNKNOWN_PLACEHOLDER": "${missing_token}",
        },
    }


def test_mcp_env_init_dump_and_release_with_declared_server(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle
    data = {
        "slack": {
            "seed_fixture_path": "src/scaffoldhub/tools/slack/slack_mcp_eval_fixture.json",
            "channels": ["general", "random"],
            "users": {
                "u001": "alice",
                "u002": "bob",
            },
        },
    }
    expected_data = deepcopy(data)

    assert "slack" in env.data_lifecycles
    assert env.dump() == {}

    env.init(data)

    dumped_data = env.dump()

    assert dumped_data == expected_data
    assert not hasattr(env, "data")

    dumped_data["slack"]["channels"].append("alerts")

    assert env.dump() == expected_data
    assert data == expected_data

    asyncio.run(env.release())

    assert env.is_alive() is False
    assert env.urls == {}
    assert env.data_lifecycles["slack"] is lifecycle
    assert env.resolved_servers == {}


def test_mcp_env_constructor_requires_servers_dict(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)

    with pytest.raises(TypeError, match="servers must be a dict"):
        mcp_env.McpEnv(servers=[])


def test_mcp_env_owns_auth_provider_and_passes_it_to_lifecycle_factory(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)

    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    assert isinstance(env.auth_provider, FakeAuthProvider)
    assert FakeDataLifecycleFactory.last_auth_provider is env.auth_provider
    assert env.data_lifecycle_factory.auth_provider is env.auth_provider


def test_mcp_env_constructor_reports_missing_scaffoldhub(monkeypatch):
    sys.modules.pop("scaffoldhub.tools.base", None)
    sys.modules.pop("rock.sdk.mcp.mcp_env", None)
    module = importlib.import_module("rock.sdk.mcp.mcp_env")
    module = importlib.reload(module)

    def raise_missing_dependency():
        raise ImportError("rock.sdk.mcp requires scaffoldhub. Install it with `pip install 'rl-rock[mcp]'`.")

    monkeypatch.setattr(module, "_load_scaffoldhub_components", raise_missing_dependency)

    with pytest.raises(ImportError, match=r"rl-rock\[mcp\]"):
        module.McpEnv()


def test_mcp_env_resolves_server_env_placeholders_without_starting_runtime(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    resolved = env._resolve_server_config("slack", slack_server_config())

    assert env.is_alive() is False
    assert resolved == {
        "command": "npx",
        "args": [
            "slack-mcp-server@1.1.23",
            "--transport",
            "stdio",
        ],
        "env": {
            "SLACK_MCP_XOXP_TOKEN": "xoxp-test-token",
            "SLACK_MCP_XOXB_TOKEN": "xoxb-test-token",
            "STATIC_VALUE": "unchanged",
            "PARTIAL_TEMPLATE": "token-${SLACK_MCP_XOXP_TOKEN}",
            "UNKNOWN_PLACEHOLDER": "${missing_token}",
        },
    }
    assert env.servers["slack"]["env"]["SLACK_MCP_XOXP_TOKEN"] == "${SLACK_MCP_XOXP_TOKEN}"


def test_mcp_env_resolution_keeps_placeholders_when_auth_is_unavailable(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(
        servers={
            "github": {
                "command": "github-mcp-server",
                "env": {
                    "GITHUB_TOKEN": "${github_token}",
                },
            }
        }
    )

    resolved = env._resolve_server_config("github", env.servers["github"])

    assert env.is_alive() is False
    assert env.data_lifecycles == {}
    assert resolved == {
        "command": "github-mcp-server",
        "env": {
            "GITHUB_TOKEN": "${github_token}",
        },
    }


def test_mcp_env_init_with_no_servers_allows_empty_data_but_not_urls_before_start(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv()

    env.init({})

    assert env.dump() == {}
    with pytest.raises(RuntimeError, match="McpEnv has not been started"):
        env.get_urls()

    asyncio.run(env.release())

    assert env.is_alive() is False


def test_mcp_env_init_requires_dict(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv()

    with pytest.raises(TypeError, match="data must be a dict"):
        env.init([])


def test_mcp_env_init_ignores_data_keys_missing_from_servers(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    env.init({"github": {"repositories": ["example"]}})

    assert env.dump() == {}


def test_mcp_env_init_ignores_server_keys_missing_from_data(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    env.init({})

    assert env.dump() == {}


def test_mcp_env_init_rejects_non_dict_values_only_for_intersection_keys(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    with pytest.raises(TypeError, match="data for slack must be a dict"):
        env.init({"slack": []})

    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    env.init({"github": []})

    assert env.dump() == {}


def test_mcp_env_reset_delegates_to_configured_lifecycles_without_stopping_runtime(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle
    env.running = True
    env.urls = {"slack": "https://example.test/slack/sse"}
    env.resolved_servers = {"slack": slack_server_config()}
    env.init({"slack": {"channels": ["general"]}})

    env.reset()

    assert lifecycle.reset_calls == 1
    assert env.dump() == {}
    assert env.is_alive() is True
    assert env.urls == {"slack": "https://example.test/slack/sse"}
    assert env.resolved_servers == {"slack": slack_server_config()}


def test_mcp_env_reset_does_not_require_prior_init(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle

    env.reset()

    assert lifecycle.reset_calls == 1
    assert env.dump() == {}


def test_mcp_env_dump_before_init_returns_empty_data(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    assert env.dump() == {}


def test_mcp_env_get_urls_requires_started_runtime_not_data_init(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    env.running = True
    env.urls = {"slack": "https://example.test/slack/sse"}

    assert env.get_urls() == {"slack": "https://example.test/slack/sse"}


def test_mcp_env_release_without_start_or_init_is_noop(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    asyncio.run(env.release())

    assert env.is_alive() is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_release_after_start_before_init_stops_runtime_and_preserves_lifecycles(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    runtime = RecordingRuntime()
    lifecycle = RecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle
    env._rock_runtime = runtime
    env.running = True
    env.urls = {"slack": "https://example.test/slack/sse"}
    env.resolved_servers = {"slack": slack_server_config()}

    asyncio.run(env.release())

    assert runtime.stopped is True
    assert env.is_alive() is False
    assert env.urls == {}
    assert env.resolved_servers == {}
    env.reset()
    assert lifecycle.reset_calls == 1


def test_mcp_env_release_preserves_lifecycles_when_runtime_stop_fails(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle
    env._rock_runtime = FailingStopRuntime()
    env.running = True
    env.urls = {"slack": "https://example.test/slack/sse"}
    env.resolved_servers = {"slack": slack_server_config()}

    asyncio.run(env.release())

    assert env.is_alive() is False
    assert env.urls == {}
    assert env.resolved_servers == {}
    env.reset()
    assert lifecycle.reset_calls == 1


def test_mcp_env_release_releases_auth_leases(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    runtime = RecordingRuntime()
    env._rock_runtime = runtime
    env.running = True
    env.urls = {"slack": "https://example.test/slack/sse"}
    env.resolved_servers = {"slack": slack_server_config()}

    asyncio.run(env.release())

    assert runtime.stopped is True
    assert env.auth_provider.release_active_leases_calls == 1
    assert env.is_alive() is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_release_retries_auth_release_when_runtime_is_not_running(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    env.running = False

    asyncio.run(env.release())
    asyncio.run(env.release())

    assert env.auth_provider.release_active_leases_calls == 2


def test_mcp_env_release_raises_when_auth_release_fails_and_clears_state(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    failing_provider = FailingReleaseAuthProvider()
    env.auth_provider = failing_provider
    env.data_lifecycle_factory.auth_provider = failing_provider
    env._rock_runtime = RecordingRuntime()
    env.running = True
    env.urls = {"slack": "https://example.test/slack/sse"}
    env.resolved_servers = {"slack": slack_server_config()}

    with pytest.raises(RuntimeError, match="Failed to release MCP auth leases") as exc_info:
        asyncio.run(env.release())

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "database release failed"
    assert failing_provider.release_active_leases_calls == 1
    assert env.is_alive() is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_release_still_releases_auth_when_runtime_stop_fails(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    env._rock_runtime = FailingStopRuntime()
    env.running = True
    env.urls = {"slack": "https://example.test/slack/sse"}
    env.resolved_servers = {"slack": slack_server_config()}

    asyncio.run(env.release())

    assert env.auth_provider.release_active_leases_calls == 1
    assert env.is_alive() is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_start_accepts_before_launch_hook(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    signature = inspect.signature(mcp_env.McpEnv.start)

    assert "before_launch" in signature.parameters
    assert signature.parameters["before_launch"].default is None


def test_mcp_env_exposes_raw_sandbox_property(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)

    assert isinstance(mcp_env.McpEnv.sandbox, property)
