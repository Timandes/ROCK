from __future__ import annotations

import inspect
import logging
from copy import deepcopy
from typing import Any

from rock.sdk.mcp.rock_runtime import BeforeLaunchHook, RockRuntime, RockRuntimeOptions
from rock.sdk.sandbox.client import Sandbox

logger = logging.getLogger(__name__)


def _load_scaffoldhub_components():
    try:
        from scaffoldhub.auth import AuthProvider
        from scaffoldhub.tools.base import DataLifecycleFactory
    except ImportError as error:
        raise ImportError("rock.sdk.mcp requires scaffoldhub. Install it with `pip install 'rl-rock[mcp]'`.") from error

    try:
        from scaffoldhub.tools.base import SandboxAware
    except ImportError:
        SandboxAware = None

    return AuthProvider, DataLifecycleFactory, SandboxAware


def _snapshot_runtime_options(options: RockRuntimeOptions | None) -> RockRuntimeOptions:
    if options is None:
        return RockRuntimeOptions()
    return RockRuntimeOptions(
        health_check_retries=options.health_check_retries,
        health_check_interval_seconds=options.health_check_interval_seconds,
        http_timeout_seconds=options.http_timeout_seconds,
    )


class McpEnv:
    """
    MCP environment manager.

    It resolves MCP server configs, starts real ROCK sandboxes, delegates data
    lifecycle operations to ScaffoldHub resources, exposes server URLs, and
    releases runtime resources.
    """

    def __init__(self, servers: dict | None = None, runtime_options: RockRuntimeOptions | None = None):
        """
        Create an uninitialized MCP environment.

        Args:
            servers: Non-empty MCP server config. Top-level keys are server or
                lifecycle types, such as ``slack``.
            runtime_options: Optional ROCK runtime health-check options. The
                values are snapshotted during construction.

        Raises:
            TypeError: Raised when servers is not a dict.
            ValueError: Raised when servers is None or empty.
        """
        if servers is None:
            raise ValueError("servers must be a non-empty dict")
        if not isinstance(servers, dict):
            raise TypeError("servers must be a dict")
        if not servers:
            raise ValueError("servers must be a non-empty dict")

        self.running = False
        self.urls = {}
        self.servers = deepcopy(servers)
        self.resolved_servers = {}
        auth_provider_class, data_lifecycle_factory_class, sandbox_aware_class = _load_scaffoldhub_components()
        self.auth_provider = auth_provider_class()
        self.data_lifecycle_factory = data_lifecycle_factory_class(auth_provider=self.auth_provider)
        self.sandbox_aware_class = sandbox_aware_class
        self.data_lifecycles: dict[str, Any] = {}
        self._rock_runtime = RockRuntime(options=_snapshot_runtime_options(runtime_options))

        for lifecycle_type in self.servers:
            if self.data_lifecycle_factory.supports(lifecycle_type):
                self.data_lifecycles[lifecycle_type] = self.data_lifecycle_factory.create(lifecycle_type)

    @property
    def sandbox(self) -> Sandbox | None:
        """
        Return the raw ROCK Sandbox object for advanced pre-launch integrations.

        This is intentionally a low-level escape hatch for callers that need
        direct ROCK SDK access while the higher-level lifecycle API evolves.
        """
        return self._rock_runtime.sandbox

    async def start(self, before_launch: BeforeLaunchHook | None = None) -> None:
        """
        Start a real ROCK MCP sandbox.

        The environment is marked running only after sandbox creation, optional
        pre-launch callback execution, MCP server launch, and SSE health checks
        all succeed.
        """
        self.running = False
        self.urls = {}
        self.resolved_servers = {
            server_name: self._resolve_server_config(server_name, server_config)
            for server_name, server_config in self.servers.items()
        }
        urls = await self._rock_runtime.start(
            self.resolved_servers,
            before_launch=self._compose_before_launch(before_launch),
        )
        self.urls = urls
        self.running = True

    def is_alive(self) -> bool:
        """
        Return the runtime state recorded by this facade.

        Returns:
            True after successful start, false after release.
        """
        return self.running

    def init(self, data: dict):
        """
        Initialize MCP environment data.

        Args:
            data: Layered environment data, such as ``{"slack": {...}}``.

        Raises:
            TypeError: Raised when data is not a dict or an intersecting
                lifecycle value is not a dict.
        """
        if not isinstance(data, dict):
            raise TypeError("data must be a dict")

        for lifecycle_type in self.data_lifecycles.keys() & data.keys():
            lifecycle_data = data[lifecycle_type]
            if not isinstance(lifecycle_data, dict):
                raise TypeError(f"data for {lifecycle_type} must be a dict")
            self.data_lifecycles[lifecycle_type].init(lifecycle_data)

    def reset(self) -> None:
        """
        Reset configured MCP environment data.

        This only delegates to configured data lifecycles. It does not stop the
        ROCK runtime, clear URLs, or change the recorded running state.
        """
        for lifecycle in self.data_lifecycles.values():
            lifecycle.reset()

    def get_urls(self) -> dict:
        """
        Get MCP server URLs.

        Returns:
            A defensive copy of the server URL mapping.

        Raises:
            RuntimeError: Raised when the runtime has not started successfully.
        """
        if not self.running:
            raise RuntimeError("McpEnv has not been started")

        return deepcopy(self.urls)

    def dump(self) -> dict:
        """
        Export current MCP environment data.

        Returns:
            Layered data returned by configured data lifecycles.
        """
        dumped_data = {}
        for lifecycle_type, lifecycle in self.data_lifecycles.items():
            lifecycle_data = lifecycle.dump()
            if lifecycle_data != {}:
                dumped_data[lifecycle_type] = lifecycle_data
        return dumped_data

    async def release(self):
        """
        Release the physical MCP runtime and any active ScaffoldHub auth leases.

        Data cleanup is intentionally handled by explicit reset calls.
        """
        auth_release_error: Exception | None = None
        try:
            if self.running:
                try:
                    await self._rock_runtime.stop()
                except Exception as error:
                    logger.warning("Failed to stop ROCK runtime during release: %s", error)

            try:
                self.auth_provider.release_active_leases()
            except Exception as error:
                auth_release_error = error
        finally:
            self.running = False
            self.urls = {}
            self.resolved_servers = {}

        if auth_release_error is not None:
            raise RuntimeError("Failed to release MCP auth leases") from auth_release_error

    def _compose_before_launch(self, before_launch: BeforeLaunchHook | None) -> BeforeLaunchHook:
        async def composed_before_launch(sandbox: Sandbox) -> None:
            self._inject_sandbox_into_lifecycles(sandbox)

            # 自动调用所有 lifecycle 的 before_launch 方法
            for lifecycle in self.data_lifecycles.values():
                hook = getattr(lifecycle, "before_launch", None)
                if callable(hook):
                    result = hook(sandbox)
                    if inspect.isawaitable(result):
                        await result

            # 再执行用户传入的 before_launch hook
            if before_launch is not None:
                result = before_launch(sandbox)
                if inspect.isawaitable(result):
                    await result

        return composed_before_launch

    def _inject_sandbox_into_lifecycles(self, sandbox: Sandbox) -> None:
        sandbox_aware_class = self.sandbox_aware_class
        if sandbox_aware_class is None:
            return

        for lifecycle in self.data_lifecycles.values():
            if isinstance(lifecycle, sandbox_aware_class):
                lifecycle.set_sandbox(sandbox)

    def _resolve_server_config(self, server_name: str, server_config: Any) -> Any:
        if not isinstance(server_config, dict):
            return deepcopy(server_config)

        resolved_config = deepcopy(server_config)
        env = resolved_config.get("env")
        if not isinstance(env, dict):
            return resolved_config

        auth = self._server_auth(server_name)
        resolved_config["env"] = {
            env_key: self._resolve_env_value(env_value, auth) for env_key, env_value in env.items()
        }
        return resolved_config

    def _server_auth(self, server_name: str) -> dict:
        try:
            return self.auth_provider.provide(server_name)
        except ValueError:
            return {}

    def _resolve_env_value(self, value: Any, auth: dict) -> Any:
        if not isinstance(value, str):
            return value
        if not value.startswith("${") or not value.endswith("}"):
            return value

        auth_key = value[2:-1]
        if not auth_key:
            return value
        return auth.get(auth_key, value)
