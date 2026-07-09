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


class RecordingStartRuntime:
    def __init__(self, sandbox=None):
        self.sandbox = sandbox or object()
        self.started_servers = None
        self.received_before_launch = None
        self.stop_calls = 0

    async def start(self, servers, before_launch=None):
        self.started_servers = deepcopy(servers)
        self.received_before_launch = before_launch
        if before_launch is not None:
            await before_launch(self.sandbox)
        return {name: f"https://example.test/{name}/sse" for name in sorted(servers)}

    async def stop(self):
        self.stop_calls += 1


class CleanupRecordingStartRuntime(RecordingStartRuntime):
    async def start(self, servers, before_launch=None):
        self.started_servers = deepcopy(servers)
        self.received_before_launch = before_launch
        try:
            if before_launch is not None:
                await before_launch(self.sandbox)
        except Exception:
            await self.stop()
            raise
        return {name: f"https://example.test/{name}/sse" for name in sorted(servers)}


class RecordingDataLifecycle:
    def __init__(self):
        self.initialized_data = {}
        self.reset_calls = 0
        self.dump_queries: list = []

    def init(self, data: dict) -> None:
        self.initialized_data = deepcopy(data)

    def dump(self, query=None) -> dict:
        self.dump_queries.append(query)
        return deepcopy(self.initialized_data)

    def reset(self) -> None:
        self.reset_calls += 1
        self.initialized_data = {}


class AsyncRecordingDataLifecycle:
    """Async counterpart of RecordingDataLifecycle for testing isawaitable() branches."""

    def __init__(self):
        self.initialized_data = {}
        self.reset_calls = 0
        self.dump_queries: list = []

    async def init(self, data: dict) -> None:
        self.initialized_data = deepcopy(data)

    async def dump(self, query=None) -> dict:
        self.dump_queries.append(query)
        return deepcopy(self.initialized_data)

    async def reset(self) -> None:
        self.reset_calls += 1
        self.initialized_data = {}


class FakeSandboxAware:
    def set_sandbox(self, sandbox) -> None:
        self.sandbox = sandbox


class RecordingSandboxAwareLifecycle(RecordingDataLifecycle, FakeSandboxAware):
    def __init__(self):
        super().__init__()
        self.sandbox = None
        self.events: list[str] = []

    def set_sandbox(self, sandbox) -> None:
        self.events.append("set_sandbox")
        self.sandbox = sandbox


class FailingSandboxAwareLifecycle(RecordingDataLifecycle, FakeSandboxAware):
    def set_sandbox(self, sandbox) -> None:
        raise RuntimeError("sandbox injection failed")


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


def install_fake_scaffoldhub(monkeypatch, *, include_sandbox_aware: bool = True):
    scaffoldhub = ModuleType("scaffoldhub")
    auth = ModuleType("scaffoldhub.auth")
    tools = ModuleType("scaffoldhub.tools")
    base = ModuleType("scaffoldhub.tools.base")
    auth.AuthProvider = FakeAuthProvider
    base.DataLifecycleFactory = FakeDataLifecycleFactory
    if include_sandbox_aware:
        base.SandboxAware = FakeSandboxAware

    monkeypatch.setitem(sys.modules, "scaffoldhub", scaffoldhub)
    monkeypatch.setitem(sys.modules, "scaffoldhub.auth", auth)
    monkeypatch.setitem(sys.modules, "scaffoldhub.tools", tools)
    monkeypatch.setitem(sys.modules, "scaffoldhub.tools.base", base)


def reload_mcp_env(monkeypatch, *, include_sandbox_aware: bool = True):
    install_fake_scaffoldhub(monkeypatch, include_sandbox_aware=include_sandbox_aware)
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
    assert asyncio.run(env.dump()) == {}

    asyncio.run(env.init(data))

    dumped_data = asyncio.run(env.dump())

    assert dumped_data == expected_data
    assert not hasattr(env, "data")

    dumped_data["slack"]["channels"].append("alerts")

    assert asyncio.run(env.dump()) == expected_data
    assert data == expected_data

    asyncio.run(env.release())

    assert env.is_alive() is False
    assert env.urls == {}
    assert env.data_lifecycles["slack"] is lifecycle
    assert env.resolved_servers == {}


def test_mcp_env_constructor_rejects_missing_or_empty_servers(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)

    with pytest.raises(ValueError, match="servers must be a non-empty dict"):
        mcp_env.McpEnv()

    with pytest.raises(ValueError, match="servers must be a non-empty dict"):
        mcp_env.McpEnv(servers=None)

    with pytest.raises(ValueError, match="servers must be a non-empty dict"):
        mcp_env.McpEnv(servers={})


@pytest.mark.parametrize("servers", [[], "slack"])
def test_mcp_env_constructor_requires_servers_dict(monkeypatch, servers):
    mcp_env = reload_mcp_env(monkeypatch)

    with pytest.raises(TypeError, match="servers must be a dict"):
        mcp_env.McpEnv(servers=servers)


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
        module.McpEnv(servers={"slack": slack_server_config()})


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


def test_mcp_env_init_requires_dict(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    with pytest.raises(TypeError, match="data must be a dict"):
        asyncio.run(env.init([]))


def test_mcp_env_init_ignores_data_keys_missing_from_servers(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    asyncio.run(env.init({"github": {"repositories": ["example"]}}))

    assert asyncio.run(env.dump()) == {}


def test_mcp_env_init_ignores_server_keys_missing_in_data(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    asyncio.run(env.init({}))

    assert asyncio.run(env.dump()) == {}


def test_mcp_env_init_rejects_non_dict_values_only_for_intersection_keys(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    with pytest.raises(TypeError, match="data for slack must be a dict"):
        asyncio.run(env.init({"slack": []}))

    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    asyncio.run(env.init({"github": []}))

    assert asyncio.run(env.dump()) == {}


def test_mcp_env_reset_delegates_to_configured_lifecycles_without_stopping_runtime(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle
    env.running = True
    env.urls = {"slack": "https://example.test/slack/sse"}
    env.resolved_servers = {"slack": slack_server_config()}
    asyncio.run(env.init({"slack": {"channels": ["general"]}}))

    asyncio.run(env.reset())

    assert lifecycle.reset_calls == 1
    assert asyncio.run(env.dump()) == {}
    assert env.is_alive() is True
    assert env.urls == {"slack": "https://example.test/slack/sse"}
    assert env.resolved_servers == {"slack": slack_server_config()}


def test_mcp_env_reset_does_not_require_prior_init(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle

    asyncio.run(env.reset())

    assert lifecycle.reset_calls == 1
    assert asyncio.run(env.dump()) == {}


def test_mcp_env_dump_before_init_returns_empty_data(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    assert asyncio.run(env.dump()) == {}


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
    asyncio.run(env.reset())
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
    asyncio.run(env.reset())
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


def test_mcp_env_start_injects_sandbox_into_sandbox_aware_lifecycle(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingSandboxAwareLifecycle()
    sandbox = object()
    runtime = RecordingStartRuntime(sandbox=sandbox)
    env.data_lifecycles["slack"] = lifecycle
    env._rock_runtime = runtime

    asyncio.run(env.start())

    assert lifecycle.sandbox is sandbox
    assert lifecycle.events == ["set_sandbox"]
    assert env.is_alive() is True
    assert env.get_urls() == {"slack": "https://example.test/slack/sse"}


def test_mcp_env_start_runs_user_before_launch_after_sandbox_injection(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingSandboxAwareLifecycle()
    sandbox = object()
    events: list[str] = []
    runtime = RecordingStartRuntime(sandbox=sandbox)
    env.data_lifecycles["slack"] = lifecycle
    env._rock_runtime = runtime

    async def before_launch(received_sandbox):
        events.extend(lifecycle.events)
        events.append("user_before_launch")
        assert received_sandbox is sandbox
        assert lifecycle.sandbox is sandbox

    asyncio.run(env.start(before_launch=before_launch))

    assert events == ["set_sandbox", "user_before_launch"]


def test_mcp_env_start_ignores_non_sandbox_aware_lifecycle(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    runtime = RecordingStartRuntime()
    env.data_lifecycles["slack"] = lifecycle
    env._rock_runtime = runtime

    asyncio.run(env.start())

    assert not hasattr(lifecycle, "sandbox")
    assert env.is_alive() is True


def test_mcp_env_start_skips_injection_when_sandbox_aware_is_unavailable(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch, include_sandbox_aware=False)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingSandboxAwareLifecycle()
    runtime = RecordingStartRuntime()
    env.data_lifecycles["slack"] = lifecycle
    env._rock_runtime = runtime

    asyncio.run(env.start())

    assert lifecycle.sandbox is None
    assert lifecycle.events == []
    assert env.is_alive() is True


def test_mcp_env_start_propagates_sandbox_injection_failure_and_runtime_cleans_up(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    runtime = CleanupRecordingStartRuntime()
    env.data_lifecycles["slack"] = FailingSandboxAwareLifecycle()
    env._rock_runtime = runtime

    with pytest.raises(RuntimeError, match="sandbox injection failed"):
        asyncio.run(env.start())

    assert runtime.stop_calls == 1
    assert env.is_alive() is False
    assert env.urls == {}


def test_mcp_env_exposes_raw_sandbox_property(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)

    assert isinstance(mcp_env.McpEnv.sandbox, property)


def test_mcp_env_accepts_runtime_options_and_passes_snapshot_to_rock_runtime(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    options = mcp_env.RockRuntimeOptions(
        health_check_retries=12,
        health_check_interval_seconds=5.0,
        http_timeout_seconds=2.5,
    )

    env = mcp_env.McpEnv(servers={"slack": slack_server_config()}, runtime_options=options)
    options.health_check_retries = 1
    options.health_check_interval_seconds = 1.0
    options.http_timeout_seconds = 1.0

    assert env._rock_runtime.options == mcp_env.RockRuntimeOptions(
        health_check_retries=12,
        health_check_interval_seconds=5.0,
        http_timeout_seconds=2.5,
    )
    assert env._rock_runtime.health_check_retries == 12
    assert env._rock_runtime.health_check_interval_seconds == 5.0
    assert env._rock_runtime.http_timeout_seconds == 2.5


def test_mcp_env_uses_default_runtime_options_when_not_provided(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)

    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    assert env._rock_runtime.options == mcp_env.RockRuntimeOptions()


def test_mcp_env_init_awaits_async_lifecycle(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = AsyncRecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle
    data = {"slack": {"channels": ["general"]}}

    asyncio.run(env.init(data))

    assert lifecycle.initialized_data == data["slack"]
    assert asyncio.run(env.dump()) == {"slack": {"channels": ["general"]}}


def test_mcp_env_dump_awaits_async_lifecycle(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = AsyncRecordingDataLifecycle()
    lifecycle.initialized_data = {"channels": ["random"]}
    env.data_lifecycles["slack"] = lifecycle

    result = asyncio.run(env.dump())

    assert result == {"slack": {"channels": ["random"]}}


def test_mcp_env_reset_awaits_async_lifecycle(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = AsyncRecordingDataLifecycle()
    lifecycle.initialized_data = {"channels": ["general"]}
    env.data_lifecycles["slack"] = lifecycle

    asyncio.run(env.reset())

    assert lifecycle.reset_calls == 1
    assert lifecycle.initialized_data == {}
    assert asyncio.run(env.dump()) == {}


def test_mcp_env_supports_mixed_sync_and_async_lifecycles(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config(), "github": slack_server_config()})
    sync_lifecycle = RecordingDataLifecycle()
    async_lifecycle = AsyncRecordingDataLifecycle()
    env.data_lifecycles["slack"] = sync_lifecycle
    env.data_lifecycles["github"] = async_lifecycle

    asyncio.run(env.init({"slack": {"key": "sync"}, "github": {"key": "async"}}))

    assert sync_lifecycle.initialized_data == {"key": "sync"}
    assert async_lifecycle.initialized_data == {"key": "async"}
    dumped = asyncio.run(env.dump())
    assert dumped == {"slack": {"key": "sync"}, "github": {"key": "async"}}

    asyncio.run(env.reset())

    assert sync_lifecycle.reset_calls == 1
    assert async_lifecycle.reset_calls == 1
    assert asyncio.run(env.dump()) == {}


def test_mcp_env_dump_without_query_passes_none_to_lifecycles(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    lifecycle.initialized_data = {"channels": ["general"]}
    env.data_lifecycles["slack"] = lifecycle

    result = asyncio.run(env.dump())

    assert result == {"slack": {"channels": ["general"]}}
    assert lifecycle.dump_queries == [None]


def test_mcp_env_dump_with_query_passes_per_lifecycle_query(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config(), "github": slack_server_config()})
    slack_lifecycle = RecordingDataLifecycle()
    slack_lifecycle.initialized_data = {"channels": ["general"]}
    github_lifecycle = RecordingDataLifecycle()
    github_lifecycle.initialized_data = {"repos": ["example"]}
    env.data_lifecycles["slack"] = slack_lifecycle
    env.data_lifecycles["github"] = github_lifecycle

    query = {"slack": {"type": "channels", "fields": "id,name"}}
    result = asyncio.run(env.dump(query=query))

    assert result == {
        "slack": {"channels": ["general"]},
        "github": {"repos": ["example"]},
    }
    assert slack_lifecycle.dump_queries == [{"type": "channels", "fields": "id,name"}]
    assert github_lifecycle.dump_queries == [None]


def test_mcp_env_dump_with_query_ignores_unknown_lifecycle_keys(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    lifecycle.initialized_data = {"channels": ["general"]}
    env.data_lifecycles["slack"] = lifecycle

    result = asyncio.run(env.dump(query={"woocommerce": {"type": "products"}}))

    assert result == {"slack": {"channels": ["general"]}}
    assert lifecycle.dump_queries == [None]


def test_mcp_env_dump_with_empty_query_dict_passes_none(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    lifecycle.initialized_data = {"channels": ["general"]}
    env.data_lifecycles["slack"] = lifecycle

    asyncio.run(env.dump(query={}))

    assert lifecycle.dump_queries == [None]
