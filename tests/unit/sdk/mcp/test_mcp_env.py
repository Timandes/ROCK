import asyncio
import importlib
import inspect
import logging
import sys
from copy import deepcopy
from types import ModuleType

import pytest


class RecordingRuntime:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class OrderedRecordingRuntime(RecordingRuntime):
    def __init__(self, events: list[str]):
        super().__init__()
        self.events = events

    async def stop(self):
        self.events.append("runtime_stop")
        await super().stop()


class FailingStopRuntime:
    async def stop(self):
        raise RuntimeError("stop failed")


class RecordingStartRuntime:
    def __init__(self, sandbox=None):
        self.sandbox = sandbox or object()
        self.started_servers = None
        self.received_before_launch = None
        self.start_calls = 0
        self.stop_calls = 0

    async def start(
        self,
        servers,
        before_launch=None,
        on_start_failure=None,
    ):
        del on_start_failure
        self.start_calls += 1
        if self.start_calls > 1:
            raise RuntimeError("runtime has already been started")
        self.started_servers = deepcopy(servers)
        self.received_before_launch = before_launch
        if before_launch is not None:
            await before_launch(self.sandbox)
        return {name: f"https://example.test/{name}/sse" for name in sorted(servers)}

    async def stop(self):
        if self.sandbox is not None:
            self.stop_calls += 1
            self.sandbox = None


class CleanupRecordingStartRuntime(RecordingStartRuntime):
    async def start(
        self,
        servers,
        before_launch=None,
        on_start_failure=None,
    ):
        self.started_servers = deepcopy(servers)
        self.received_before_launch = before_launch
        try:
            if before_launch is not None:
                await before_launch(self.sandbox)
        except Exception:
            if on_start_failure is not None:
                try:
                    await on_start_failure()
                except Exception:
                    pass
            await self.stop()
            raise
        return {name: f"https://example.test/{name}/sse" for name in sorted(servers)}


class CancellingStartRuntime(RecordingStartRuntime):
    async def start(
        self,
        servers,
        before_launch=None,
        on_start_failure=None,
    ):
        del servers, before_launch, on_start_failure
        raise asyncio.CancelledError


class BlockingStartRuntime(RecordingStartRuntime):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()

    async def start(
        self,
        servers,
        before_launch=None,
        on_start_failure=None,
    ):
        del on_start_failure
        self.start_calls += 1
        if self.start_calls > 1:
            raise RuntimeError("runtime has already been started")
        self.started_servers = deepcopy(servers)
        self.received_before_launch = before_launch
        if before_launch is not None:
            await before_launch(self.sandbox)
        self.entered.set()
        await self.resume.wait()
        return {name: f"https://example.test/{name}/sse" for name in sorted(servers)}


class OrderedCleanupRecordingStartRuntime(CleanupRecordingStartRuntime):
    def __init__(self, events: list[str]):
        super().__init__()
        self.events = events

    async def stop(self):
        if self.sandbox is not None:
            self.events.append("runtime_stop")
        await super().stop()


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


class ReturningInitResultLifecycle(RecordingDataLifecycle):
    def init(self, data: dict) -> dict:
        super().init(data)
        return {"initialized": True, "result": deepcopy(data)}


class AsyncReturningInitResultLifecycle(AsyncRecordingDataLifecycle):
    async def init(self, data: dict) -> dict:
        await super().init(data)
        return {"initialized": True, "result": deepcopy(data)}


class DeferredInitResultLifecycle(RecordingDataLifecycle):
    def init(self, data: dict) -> dict:
        super().init(data)
        return {"initialized": False, "result": {}}


class EmptyInitResultLifecycle(RecordingDataLifecycle):
    def init(self, data: dict) -> dict:
        super().init(data)
        return {}


class FakeSandboxAware:
    def set_sandbox(self, sandbox) -> None:
        self.sandbox = sandbox


class RecordingEnvLifecycle(FakeSandboxAware):
    def __init__(
        self,
        *,
        name: str = "env",
        alive: bool = True,
        events: list[str] | None = None,
    ):
        self.name = name
        self.alive = alive
        self.events = events if events is not None else []
        self.sandbox = None
        self.start_configs = []
        self.release_calls = 0

    def set_sandbox(self, sandbox) -> None:
        self.sandbox = sandbox
        self.events.append(f"{self.name}:env_set_sandbox")

    async def start(self, server_config: dict) -> None:
        self.start_configs.append(deepcopy(server_config))
        self.events.append(f"{self.name}:env_start")

    async def is_alive(self) -> bool:
        self.events.append(f"{self.name}:env_is_alive")
        return self.alive

    async def release(self) -> None:
        self.release_calls += 1
        self.events.append(f"{self.name}:env_release")


class FailingReleaseEnvLifecycle(RecordingEnvLifecycle):
    def __init__(self, name: str, events: list[str]):
        super().__init__(name=name, events=events)

    async def release(self) -> None:
        self.release_calls += 1
        self.events.append(f"{self.name}:env_release")
        raise RuntimeError(f"{self.name} release failed")


class FailingStartEnvLifecycle(RecordingEnvLifecycle):
    async def start(self, server_config: dict) -> None:
        del server_config
        self.events.append(f"{self.name}:env_start")
        raise RuntimeError("environment start failed")


class SequencedAliveEnvLifecycle(RecordingEnvLifecycle):
    def __init__(self, name: str, alive: bool, calls: list[str]):
        super().__init__(alive=alive)
        self.name = name
        self.calls = calls

    async def is_alive(self) -> bool:
        self.calls.append(self.name)
        return self.alive


class FailingAliveEnvLifecycle(RecordingEnvLifecycle):
    async def is_alive(self) -> bool:
        raise RuntimeError("environment status failed")


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
        self.provide_calls: list[str] = []
        self.release_active_leases_calls = 0

    def provide(self, platform: str) -> dict:
        self.provide_calls.append(platform)
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
        self.auth_provider.provide(lifecycle_type)
        lifecycle = RecordingDataLifecycle()
        self.created[lifecycle_type] = lifecycle
        return lifecycle


class MultiPlatformAuthProvider(FakeAuthProvider):
    def __init__(self):
        super().__init__()
        self.auth["second"] = {"TOKEN": "second-token"}


class FailingSecondDataLifecycleFactory(FakeDataLifecycleFactory):
    def supports(self, lifecycle_type: str) -> bool:
        return lifecycle_type in {"slack", "second"}

    def create(self, lifecycle_type: str):
        if lifecycle_type == "second":
            self.auth_provider.provide(lifecycle_type)
            raise RuntimeError("failed to create second")
        return super().create(lifecycle_type)


class FakeEnvLifecycleFactory:
    def __init__(self):
        self.created = {}

    def supports(self, lifecycle_type: str) -> bool:
        return lifecycle_type == "slack"

    def create(self, lifecycle_type: str):
        if lifecycle_type != "slack":
            raise ValueError(f"Unsupported environment lifecycle type: {lifecycle_type}")
        lifecycle = RecordingEnvLifecycle()
        self.created[lifecycle_type] = lifecycle
        return lifecycle


class FailingEnvLifecycleFactory(FakeEnvLifecycleFactory):
    def supports(self, lifecycle_type: str) -> bool:
        return True

    def create(self, lifecycle_type: str):
        raise RuntimeError(f"failed to create {lifecycle_type}")


class LifecycleConstructionInterrupted(BaseException):
    pass


class InterruptingEnvLifecycleFactory(FakeEnvLifecycleFactory):
    def create(self, lifecycle_type: str):
        raise LifecycleConstructionInterrupted(f"interrupted while creating {lifecycle_type}")


class FailingReleaseAuthProvider(FakeAuthProvider):
    def release_active_leases(self) -> None:
        self.release_active_leases_calls += 1
        raise RuntimeError("database release failed")


class FailingResolutionAuthProvider(FakeAuthProvider):
    def provide(self, platform: str) -> dict:
        if platform == "github":
            raise RuntimeError("config resolution failed")
        return super().provide(platform)


def install_fake_scaffoldhub(
    monkeypatch,
    *,
    include_sandbox_aware: bool = True,
    auth_provider_class=FakeAuthProvider,
    data_lifecycle_factory_class=FakeDataLifecycleFactory,
    env_lifecycle_factory_class=FakeEnvLifecycleFactory,
):
    scaffoldhub = ModuleType("scaffoldhub")
    auth = ModuleType("scaffoldhub.auth")
    tools = ModuleType("scaffoldhub.tools")
    base = ModuleType("scaffoldhub.tools.base")
    auth.AuthProvider = auth_provider_class
    base.DataLifecycleFactory = data_lifecycle_factory_class
    base.EnvLifecycleFactory = env_lifecycle_factory_class
    if include_sandbox_aware:
        base.SandboxAware = FakeSandboxAware

    monkeypatch.setitem(sys.modules, "scaffoldhub", scaffoldhub)
    monkeypatch.setitem(sys.modules, "scaffoldhub.auth", auth)
    monkeypatch.setitem(sys.modules, "scaffoldhub.tools", tools)
    monkeypatch.setitem(sys.modules, "scaffoldhub.tools.base", base)


def reload_mcp_env(
    monkeypatch,
    *,
    include_sandbox_aware: bool = True,
    auth_provider_class=FakeAuthProvider,
    data_lifecycle_factory_class=FakeDataLifecycleFactory,
    env_lifecycle_factory_class=FakeEnvLifecycleFactory,
):
    install_fake_scaffoldhub(
        monkeypatch,
        include_sandbox_aware=include_sandbox_aware,
        auth_provider_class=auth_provider_class,
        data_lifecycle_factory_class=data_lifecycle_factory_class,
        env_lifecycle_factory_class=env_lifecycle_factory_class,
    )
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

    assert asyncio.run(env.is_alive()) is False
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
    assert env.auth_provider.provide_calls == ["slack"]
    assert env.auth_provider.release_active_leases_calls == 0


def test_mcp_env_constructor_releases_auth_when_later_data_lifecycle_creation_fails(monkeypatch):
    mcp_env = reload_mcp_env(
        monkeypatch,
        auth_provider_class=MultiPlatformAuthProvider,
        data_lifecycle_factory_class=FailingSecondDataLifecycleFactory,
    )

    with pytest.raises(RuntimeError, match="failed to create second"):
        mcp_env.McpEnv(servers={"slack": slack_server_config(), "second": {}})

    auth_provider = FakeDataLifecycleFactory.last_auth_provider
    assert auth_provider.provide_calls == ["slack", "second"]
    assert auth_provider.release_active_leases_calls == 1


def test_mcp_env_constructs_registered_environment_lifecycle(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)

    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    assert isinstance(env.env_lifecycle_factory, FakeEnvLifecycleFactory)
    assert isinstance(env.env_lifecycles["slack"], RecordingEnvLifecycle)


def test_mcp_env_skips_unregistered_environment_lifecycle(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)

    env = mcp_env.McpEnv(servers={"github": {"command": "github-mcp-server"}})

    assert env.env_lifecycles == {}


def test_mcp_env_constructor_releases_auth_when_environment_lifecycle_creation_fails(monkeypatch):
    mcp_env = reload_mcp_env(
        monkeypatch,
        env_lifecycle_factory_class=FailingEnvLifecycleFactory,
    )

    with pytest.raises(RuntimeError, match="failed to create slack"):
        mcp_env.McpEnv(servers={"slack": slack_server_config()})

    auth_provider = FakeDataLifecycleFactory.last_auth_provider
    assert auth_provider.provide_calls == ["slack"]
    assert auth_provider.release_active_leases_calls == 1


def test_mcp_env_constructor_preserves_creation_error_when_auth_release_fails(monkeypatch, caplog):
    mcp_env = reload_mcp_env(
        monkeypatch,
        auth_provider_class=FailingReleaseAuthProvider,
        env_lifecycle_factory_class=FailingEnvLifecycleFactory,
    )

    with caplog.at_level(logging.WARNING, logger=mcp_env.__name__):
        with pytest.raises(RuntimeError, match="failed to create slack"):
            mcp_env.McpEnv(servers={"slack": slack_server_config()})

    auth_provider = FakeDataLifecycleFactory.last_auth_provider
    assert auth_provider.release_active_leases_calls == 1
    assert "Failed to release MCP auth leases after lifecycle construction failure" in caplog.text
    assert "database release failed" in caplog.text


def test_mcp_env_constructor_releases_auth_on_base_exception(monkeypatch):
    mcp_env = reload_mcp_env(
        monkeypatch,
        env_lifecycle_factory_class=InterruptingEnvLifecycleFactory,
    )

    with pytest.raises(LifecycleConstructionInterrupted, match="interrupted while creating slack"):
        mcp_env.McpEnv(servers={"slack": slack_server_config()})

    auth_provider = FakeDataLifecycleFactory.last_auth_provider
    assert auth_provider.provide_calls == ["slack"]
    assert auth_provider.release_active_leases_calls == 1


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

    assert asyncio.run(env.is_alive()) is False
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

    assert asyncio.run(env.is_alive()) is False
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
    assert asyncio.run(env.is_alive()) is True
    assert env.urls == {"slack": "https://example.test/slack/sse"}
    assert env.resolved_servers == {"slack": slack_server_config()}


def test_mcp_env_reset_only_resets_selected_lifecycles(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    slack_lifecycle = RecordingDataLifecycle()
    git_lifecycle = RecordingDataLifecycle()
    code_executor_lifecycle = AsyncRecordingDataLifecycle()
    env.data_lifecycles = {
        "slack": slack_lifecycle,
        "git": git_lifecycle,
        "code-executor": code_executor_lifecycle,
    }

    asyncio.run(env.reset(keys=["git", "code-executor"]))

    assert slack_lifecycle.reset_calls == 0
    assert git_lifecycle.reset_calls == 1
    assert code_executor_lifecycle.reset_calls == 1


def test_mcp_env_reset_with_empty_keys_is_noop(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle

    asyncio.run(env.reset(keys=[]))

    assert lifecycle.reset_calls == 0


def test_mcp_env_reset_ignores_unknown_keys(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle

    asyncio.run(env.reset(keys=["unknown"]))

    assert lifecycle.reset_calls == 0


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

    assert asyncio.run(env.is_alive()) is False
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
    assert asyncio.run(env.is_alive()) is False
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

    assert asyncio.run(env.is_alive()) is False
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
    assert asyncio.run(env.is_alive()) is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_release_runs_environment_before_runtime(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    events: list[str] = []
    lifecycle = RecordingEnvLifecycle(events=events)
    env.env_lifecycles = {"slack": lifecycle}
    env._rock_runtime = OrderedRecordingRuntime(events)
    env.running = True

    asyncio.run(env.release())

    assert events == ["env:env_release", "runtime_stop"]
    assert env.auth_provider.release_active_leases_calls == 1


def test_mcp_env_release_continues_after_environment_failure(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    events: list[str] = []
    first = FailingReleaseEnvLifecycle("first", events)
    second = RecordingEnvLifecycle(events=events)
    env.env_lifecycles = {"first": first, "second": second}
    runtime = OrderedRecordingRuntime(events)
    env._rock_runtime = runtime
    env.running = True

    with pytest.raises(RuntimeError, match="first release failed"):
        asyncio.run(env.release())

    assert events == [
        "first:env_release",
        "env:env_release",
        "runtime_stop",
    ]
    assert second.release_calls == 1
    assert runtime.stopped is True
    assert env.auth_provider.release_active_leases_calls == 1
    assert env.running is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_release_attempts_all_failing_environments_and_raises_first(
    monkeypatch,
):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    events: list[str] = []
    first = FailingReleaseEnvLifecycle("first", events)
    second = FailingReleaseEnvLifecycle("second", events)
    env.env_lifecycles = {"first": first, "second": second}

    with pytest.raises(RuntimeError, match="first release failed"):
        asyncio.run(env.release())

    assert events == ["first:env_release", "second:env_release"]
    assert first.release_calls == 1
    assert second.release_calls == 1
    assert env.auth_provider.release_active_leases_calls == 1


def test_mcp_env_release_prefers_auth_error_over_environment_error(
    monkeypatch,
    caplog,
):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    events: list[str] = []
    env.env_lifecycles = {"slack": FailingReleaseEnvLifecycle("slack", events)}
    env.auth_provider = FailingReleaseAuthProvider()
    env.data_lifecycle_factory.auth_provider = env.auth_provider
    env._rock_runtime = OrderedRecordingRuntime(events)
    env.running = True

    with pytest.raises(RuntimeError, match="Failed to release MCP auth leases"):
        asyncio.run(env.release())

    assert "slack release failed" in caplog.text
    assert env.running is False


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
    assert asyncio.run(env.is_alive()) is False
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
    assert asyncio.run(env.is_alive()) is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_start_accepts_before_launch_hook(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    signature = inspect.signature(mcp_env.McpEnv.start)

    assert "before_launch" in signature.parameters
    assert signature.parameters["before_launch"].default is None


def test_mcp_env_rejects_duplicate_start_without_changing_running_state(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    runtime = RecordingStartRuntime()
    lifecycle = env.env_lifecycles["slack"]
    env._rock_runtime = runtime

    asyncio.run(env.start())
    urls = deepcopy(env.urls)
    resolved_servers = deepcopy(env.resolved_servers)

    with pytest.raises(RuntimeError, match="already been started"):
        asyncio.run(env.start())

    assert runtime.start_calls == 1
    assert runtime.stop_calls == 0
    assert lifecycle.release_calls == 0
    assert env.auth_provider.release_active_leases_calls == 0
    assert env.running is True
    assert env.urls == urls
    assert env.resolved_servers == resolved_servers


def test_mcp_env_start_cancellation_releases_all_resources(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    runtime = CancellingStartRuntime()
    lifecycle = env.env_lifecycles["slack"]
    env._rock_runtime = runtime

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(env.start())

    assert lifecycle.release_calls == 1
    assert runtime.stop_calls == 1
    assert env.auth_provider.release_active_leases_calls == 1
    assert env.running is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_rejects_concurrent_start_without_cleaning_up_first_start(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    runtime = BlockingStartRuntime()
    lifecycle = env.env_lifecycles["slack"]
    env._rock_runtime = runtime

    async def run_concurrent_starts():
        first_start = asyncio.create_task(env.start())
        await runtime.entered.wait()
        try:
            with pytest.raises(RuntimeError, match="already been started"):
                await env.start()
            state_during_first_start = (
                runtime.start_calls,
                runtime.stop_calls,
                lifecycle.release_calls,
                env.auth_provider.release_active_leases_calls,
            )
        finally:
            runtime.resume.set()
            await first_start
        return state_during_first_start

    assert asyncio.run(run_concurrent_starts()) == (1, 0, 0, 0)
    assert env.running is True
    assert env.get_urls() == {"slack": "https://example.test/slack/sse"}


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
    assert asyncio.run(env.is_alive()) is True
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
    assert asyncio.run(env.is_alive()) is True


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
    assert asyncio.run(env.is_alive()) is True


def test_mcp_env_start_propagates_sandbox_injection_failure_and_runtime_cleans_up(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    runtime = CleanupRecordingStartRuntime()
    env.data_lifecycles["slack"] = FailingSandboxAwareLifecycle()
    env._rock_runtime = runtime

    with pytest.raises(RuntimeError, match="sandbox injection failed"):
        asyncio.run(env.start())

    assert runtime.stop_calls == 1
    assert asyncio.run(env.is_alive()) is False
    assert env.urls == {}


def test_mcp_env_start_runs_environment_lifecycle_before_user_hook(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    events: list[str] = []
    lifecycle = RecordingEnvLifecycle(events=events)
    env.env_lifecycles["slack"] = lifecycle
    sandbox = object()
    env._rock_runtime = RecordingStartRuntime(sandbox=sandbox)

    async def before_launch(received_sandbox):
        assert received_sandbox is sandbox
        events.append("user_before_launch")

    asyncio.run(env.start(before_launch=before_launch))

    assert lifecycle.sandbox is sandbox
    assert events == [
        "env:env_set_sandbox",
        "env:env_start",
        "user_before_launch",
    ]


def test_mcp_env_start_passes_resolved_server_config(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = env.env_lifecycles["slack"]
    env._rock_runtime = RecordingStartRuntime()

    asyncio.run(env.start())

    assert lifecycle.start_configs == [env.resolved_servers["slack"]]
    assert lifecycle.start_configs[0]["env"]["SLACK_MCP_XOXP_TOKEN"] == ("xoxp-test-token")


def test_mcp_env_start_runs_environment_lifecycles_in_server_order(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(
        servers={
            "first": {"command": "first"},
            "second": {"command": "second"},
        }
    )
    events: list[str] = []
    first = RecordingEnvLifecycle(name="first", events=events)
    second = RecordingEnvLifecycle(name="second", events=events)
    env.env_lifecycles = {"first": first, "second": second}
    env._rock_runtime = RecordingStartRuntime()

    asyncio.run(env.start())

    assert events == [
        "first:env_set_sandbox",
        "second:env_set_sandbox",
        "first:env_start",
        "second:env_start",
    ]


def test_mcp_env_start_propagates_environment_failure_and_runtime_cleans_up(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    runtime = CleanupRecordingStartRuntime()
    env.env_lifecycles["slack"] = FailingStartEnvLifecycle()
    env._rock_runtime = runtime

    with pytest.raises(RuntimeError, match="environment start failed"):
        asyncio.run(env.start())

    assert runtime.stop_calls == 1
    assert env.running is False
    assert env.urls == {}


def test_mcp_env_start_rolls_back_started_environments_when_later_start_fails(
    monkeypatch,
):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(
        servers={
            "first": {"command": "first"},
            "second": {"command": "second"},
        }
    )
    events: list[str] = []
    first = RecordingEnvLifecycle(name="first", events=events)
    second = FailingStartEnvLifecycle(name="second", events=events)
    env.env_lifecycles = {"first": first, "second": second}
    runtime = CleanupRecordingStartRuntime()
    env._rock_runtime = runtime

    with pytest.raises(RuntimeError, match="environment start failed"):
        asyncio.run(env.start())

    assert events == [
        "first:env_set_sandbox",
        "second:env_set_sandbox",
        "first:env_start",
        "second:env_start",
        "first:env_release",
        "second:env_release",
    ]
    assert runtime.stop_calls == 1
    assert env.auth_provider.release_active_leases_calls == 1
    assert env.running is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_start_releases_environment_before_runtime_stops(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    events: list[str] = []
    env.env_lifecycles["slack"] = FailingStartEnvLifecycle(events=events)
    env._rock_runtime = OrderedCleanupRecordingStartRuntime(events)

    with pytest.raises(RuntimeError, match="environment start failed"):
        asyncio.run(env.start())

    assert events == [
        "env:env_set_sandbox",
        "env:env_start",
        "env:env_release",
        "runtime_stop",
    ]


def test_mcp_env_start_rolls_back_environment_when_user_hook_fails(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    events: list[str] = []
    lifecycle = RecordingEnvLifecycle(events=events)
    env.env_lifecycles = {"slack": lifecycle}
    runtime = CleanupRecordingStartRuntime()
    env._rock_runtime = runtime

    async def failing_before_launch(sandbox):
        del sandbox
        events.append("user_before_launch")
        raise RuntimeError("user hook failed")

    with pytest.raises(RuntimeError, match="user hook failed"):
        asyncio.run(env.start(before_launch=failing_before_launch))

    assert events == [
        "env:env_set_sandbox",
        "env:env_start",
        "user_before_launch",
        "env:env_release",
    ]
    assert runtime.stop_calls == 1
    assert env.auth_provider.release_active_leases_calls == 1
    assert env.running is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_start_preserves_original_error_when_rollback_fails(
    monkeypatch,
    caplog,
):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    events: list[str] = []
    lifecycle = FailingReleaseEnvLifecycle("slack", events)
    env.env_lifecycles = {"slack": lifecycle}
    runtime = CleanupRecordingStartRuntime()
    env._rock_runtime = runtime
    start_error = RuntimeError("original start failed")

    async def failing_before_launch(sandbox):
        del sandbox
        raise start_error

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(env.start(before_launch=failing_before_launch))

    assert exc_info.value is start_error
    assert lifecycle.release_calls == 1
    assert runtime.stop_calls == 1
    assert env.auth_provider.release_active_leases_calls == 1
    assert "slack release failed" in caplog.text
    assert env.running is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_start_rolls_back_auth_leases_when_config_resolution_fails(
    monkeypatch,
):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(
        servers={
            "slack": slack_server_config(),
            "github": {
                "command": "github-mcp-server",
                "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
            },
        }
    )
    auth_provider = FailingResolutionAuthProvider()
    env.auth_provider = auth_provider
    env.data_lifecycle_factory.auth_provider = auth_provider
    runtime = RecordingStartRuntime()
    env._rock_runtime = runtime

    with pytest.raises(RuntimeError, match="config resolution failed"):
        asyncio.run(env.start())

    assert runtime.started_servers is None
    assert env.env_lifecycles["slack"].release_calls == 1
    assert auth_provider.release_active_leases_calls == 1
    assert env.running is False
    assert env.urls == {}
    assert env.resolved_servers == {}


def test_mcp_env_is_alive_returns_single_environment_value(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    env.env_lifecycles["slack"].alive = False
    env.running = True

    assert asyncio.run(env.is_alive("slack")) is False


def test_mcp_env_is_alive_returns_true_for_unknown_environment(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    env.running = False

    assert asyncio.run(env.is_alive("github")) is True


def test_mcp_env_is_alive_aggregates_all_without_short_circuit(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    calls: list[str] = []
    env.env_lifecycles = {
        "first": SequencedAliveEnvLifecycle("first", False, calls),
        "second": SequencedAliveEnvLifecycle("second", True, calls),
    }
    env.running = True

    assert asyncio.run(env.is_alive()) is False
    assert calls == ["first", "second"]


def test_mcp_env_is_alive_without_environment_lifecycle_uses_running(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"github": {"command": "github-mcp-server"}})
    env.running = True

    assert asyncio.run(env.is_alive()) is True


def test_mcp_env_is_alive_propagates_environment_error(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    env.env_lifecycles["slack"] = FailingAliveEnvLifecycle()

    with pytest.raises(RuntimeError, match="environment status failed"):
        asyncio.run(env.is_alive())


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


def test_mcp_env_init_returns_lifecycle_results(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config(), "github": slack_server_config()})
    env.data_lifecycles["slack"] = ReturningInitResultLifecycle()
    env.data_lifecycles["github"] = AsyncReturningInitResultLifecycle()

    result = asyncio.run(env.init({"slack": {"key": "sync"}, "github": {"key": "async"}}))

    assert result == {
        "slack": {"initialized": True, "result": {"key": "sync"}},
        "github": {"initialized": True, "result": {"key": "async"}},
    }


def test_mcp_env_init_keeps_deferred_init_result(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    env.data_lifecycles["slack"] = DeferredInitResultLifecycle()

    result = asyncio.run(env.init({"slack": {"reset": []}}))

    assert result == {"slack": {"initialized": False, "result": {}}}


def test_mcp_env_init_keeps_empty_lifecycle_result(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    env.data_lifecycles["slack"] = EmptyInitResultLifecycle()

    result = asyncio.run(env.init({"slack": {"reset": []}}))

    assert result == {"slack": {}}


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
