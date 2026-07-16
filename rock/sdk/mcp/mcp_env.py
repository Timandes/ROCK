from __future__ import annotations

import asyncio
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
        from scaffoldhub.tools.base import DataLifecycleFactory, EnvLifecycleFactory
    except ImportError as error:
        raise ImportError("rock.sdk.mcp requires scaffoldhub. Install it with `pip install 'rl-rock[mcp]'`.") from error

    try:
        from scaffoldhub.tools.base import SandboxAware
    except ImportError:
        SandboxAware = None

    return AuthProvider, DataLifecycleFactory, EnvLifecycleFactory, SandboxAware


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
        self._starting = False
        self.urls = {}
        self.servers = deepcopy(servers)
        self.resolved_servers = {}
        (
            auth_provider_class,
            data_lifecycle_factory_class,
            env_lifecycle_factory_class,
            sandbox_aware_class,
        ) = _load_scaffoldhub_components()
        self.auth_provider = auth_provider_class()
        self.data_lifecycle_factory = data_lifecycle_factory_class(auth_provider=self.auth_provider)
        self.env_lifecycle_factory = env_lifecycle_factory_class()
        self.sandbox_aware_class = sandbox_aware_class
        self._rock_runtime = RockRuntime(options=_snapshot_runtime_options(runtime_options))
        self.data_lifecycles, self.env_lifecycles = self._create_lifecycles()

    def _create_lifecycles(self) -> tuple[dict[str, Any], dict[str, Any]]:
        data_lifecycles: dict[str, Any] = {}
        env_lifecycles: dict[str, Any] = {}
        try:
            for lifecycle_type in self.servers:
                if self.data_lifecycle_factory.supports(lifecycle_type):
                    data_lifecycles[lifecycle_type] = self.data_lifecycle_factory.create(lifecycle_type)
                if self.env_lifecycle_factory.supports(lifecycle_type):
                    env_lifecycles[lifecycle_type] = self.env_lifecycle_factory.create(lifecycle_type)
        except BaseException:
            try:
                self.auth_provider.release_active_leases()
            except BaseException as error:
                logger.warning(
                    "Failed to release MCP auth leases after lifecycle construction failure: %s",
                    error,
                )
            raise

        return data_lifecycles, env_lifecycles

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
        if self.running or self._starting:
            raise RuntimeError("MCP environment has already been started")

        self._starting = True
        try:
            self.running = False
            self.urls = {}
            start_failure_cleanup_started = False

            async def cleanup_before_runtime_stop() -> None:
                nonlocal start_failure_cleanup_started
                start_failure_cleanup_started = True
                await self.release()

            try:
                self.resolved_servers = {
                    server_name: self._resolve_server_config(server_name, server_config)
                    for server_name, server_config in self.servers.items()
                }
                urls = await self._rock_runtime.start(
                    self.resolved_servers,
                    before_launch=self._compose_before_launch(before_launch),
                    on_start_failure=cleanup_before_runtime_stop,
                )
            except (asyncio.CancelledError, Exception):
                if not start_failure_cleanup_started:
                    try:
                        await self.release()
                    except Exception as cleanup_error:
                        logger.warning(
                            "Failed to clean up MCP resources after startup failure: %s",
                            cleanup_error,
                        )
                raise

            self.urls = urls
            self.running = True
        finally:
            self._starting = False

    async def is_alive(self, key: str | None = None) -> bool:
        """Return one environment status or aggregate all configured statuses."""
        if key is not None:
            lifecycle = self.env_lifecycles.get(key)
            if lifecycle is None:
                return True
            return await lifecycle.is_alive()

        results = []
        for lifecycle in self.env_lifecycles.values():
            results.append(await lifecycle.is_alive())
        return self.running and all(results)

    async def init(self, data: dict) -> dict:
        """
        Initialize MCP environment data.

        Args:
            data: Layered environment data, such as ``{"slack": {...}}``.

        Returns:
            Layered init results returned by configured data lifecycle init calls.

        Raises:
            TypeError: Raised when data is not a dict or an intersecting
                lifecycle value is not a dict.
        """
        if not isinstance(data, dict):
            raise TypeError("data must be a dict")

        init_results = {}
        for lifecycle_type in self.data_lifecycles.keys() & data.keys():
            lifecycle_data = data[lifecycle_type]
            if not isinstance(lifecycle_data, dict):
                raise TypeError(f"data for {lifecycle_type} must be a dict")
            result = self.data_lifecycles[lifecycle_type].init(lifecycle_data)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                init_results[lifecycle_type] = result
        return init_results

    async def reset(self, keys: list[str] | None = None) -> None:
        """
        Reset configured MCP environment data.

        Args:
            keys: Optional lifecycle names to reset. When omitted, all configured
                data lifecycles are reset. An empty list performs no resets.

        This only delegates to configured data lifecycles. It does not stop the
        ROCK runtime, clear URLs, or change the recorded running state. Unknown
        lifecycle names are ignored.
        """
        selected_keys = None if keys is None else set(keys)
        for lifecycle_type, lifecycle in self.data_lifecycles.items():
            if selected_keys is not None and lifecycle_type not in selected_keys:
                continue
            result = lifecycle.reset()
            if inspect.isawaitable(result):
                await result

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

    async def dump(self, query: dict | None = None) -> dict:
        """
        Export current MCP environment data.

        Args:
            query: Optional per-lifecycle query dict, keyed by lifecycle type.
                For example ``{"woocommerce": {"type": "products", "fields": "id,name"}}``
                triggers a live API read instead of a local snapshot.

        Returns:
            Layered data returned by configured data lifecycles.
        """
        dumped_data = {}
        for lifecycle_type, lifecycle in self.data_lifecycles.items():
            lifecycle_query = (query or {}).get(lifecycle_type)
            lifecycle_data = lifecycle.dump(lifecycle_query)
            if inspect.isawaitable(lifecycle_data):
                lifecycle_data = await lifecycle_data
            if lifecycle_data != {}:
                dumped_data[lifecycle_type] = lifecycle_data
        return dumped_data

    async def release(self) -> None:
        """Release environment hooks, runtime resources, and auth leases."""
        env_release_error: Exception | None = None
        for lifecycle_type, lifecycle in self.env_lifecycles.items():
            try:
                await lifecycle.release()
            except Exception as error:
                logger.warning(
                    "Failed to release environment lifecycle %s: %s",
                    lifecycle_type,
                    error,
                )
                if env_release_error is None:
                    env_release_error = error

        auth_release_error: Exception | None = None
        try:
            if self.running or self._rock_runtime.sandbox is not None:
                try:
                    await self._rock_runtime.stop()
                except Exception as error:
                    logger.warning(
                        "Failed to stop ROCK runtime during release: %s",
                        error,
                    )

            try:
                self.auth_provider.release_active_leases()
            except Exception as error:
                auth_release_error = error
        finally:
            self.running = False
            self.urls = {}
            self.resolved_servers = {}

        if auth_release_error is not None:
            if env_release_error is not None:
                logger.warning(
                    "Environment lifecycle release also failed: %s",
                    env_release_error,
                )
            raise RuntimeError("Failed to release MCP auth leases") from auth_release_error

        if env_release_error is not None:
            raise env_release_error

    def _compose_before_launch(
        self,
        before_launch: BeforeLaunchHook | None,
    ) -> BeforeLaunchHook:
        async def composed_before_launch(sandbox: Sandbox) -> None:
            self._inject_sandbox_into_lifecycles(self.data_lifecycles, sandbox)
            self._inject_sandbox_into_lifecycles(self.env_lifecycles, sandbox)
            await self._start_env_lifecycles()

            if before_launch is None:
                return

            result = before_launch(sandbox)
            if inspect.isawaitable(result):
                await result

        return composed_before_launch

    def _inject_sandbox_into_lifecycles(
        self,
        lifecycles: dict[str, Any],
        sandbox: Sandbox,
    ) -> None:
        sandbox_aware_class = self.sandbox_aware_class
        if sandbox_aware_class is None:
            return

        for lifecycle in lifecycles.values():
            if isinstance(lifecycle, sandbox_aware_class):
                lifecycle.set_sandbox(sandbox)

    async def _start_env_lifecycles(self) -> None:
        for lifecycle_type, lifecycle in self.env_lifecycles.items():
            await lifecycle.start(self.resolved_servers[lifecycle_type])

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
