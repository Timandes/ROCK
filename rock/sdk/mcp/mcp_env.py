from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from rock.sdk.mcp.rock_runtime import BeforeLaunchHook, RockRuntime
from rock.sdk.sandbox.client import Sandbox

logger = logging.getLogger(__name__)


def _load_data_lifecycle_factory():
    try:
        from scaffoldhub.tools.base import DataLifecycleFactory
    except ImportError as error:
        raise ImportError(
            "rock.sdk.mcp requires scaffoldhub. Install it with `pip install 'rl-rock[mcp]'`."
        ) from error
    return DataLifecycleFactory


class McpEnv:
    """
    MCP environment manager.

    It resolves MCP server configs, starts real ROCK sandboxes, delegates data
    lifecycle operations to ScaffoldHub resources, exposes server URLs, and
    releases runtime resources.
    """

    def __init__(self, servers: dict | None = None):
        """
        Create an uninitialized MCP environment.

        Args:
            servers: MCP server config. Top-level keys are server or lifecycle
                types, such as ``slack``.

        Raises:
            TypeError: Raised when servers is neither dict nor None.
        """
        if servers is None:
            servers = {}
        if not isinstance(servers, dict):
            raise TypeError("servers must be a dict")

        self.running = False
        self.urls = {}
        self.servers = deepcopy(servers)
        self.resolved_servers = {}
        data_lifecycle_factory = _load_data_lifecycle_factory()
        self.data_lifecycle_factory = data_lifecycle_factory()
        self.data_lifecycles: dict[str, Any] = {}
        self._rock_runtime = RockRuntime()

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
            before_launch=before_launch,
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
        Release the physical MCP runtime.

        Data cleanup is intentionally handled by explicit reset calls.
        """
        try:
            if self.running:
                try:
                    await self._rock_runtime.stop()
                except Exception as error:
                    logger.warning("Failed to stop ROCK runtime during release: %s", error)
        finally:
            self.running = False
            self.urls = {}
            self.resolved_servers = {}

    def _resolve_server_config(self, server_name: str, server_config: Any) -> Any:
        if not isinstance(server_config, dict):
            return deepcopy(server_config)

        resolved_config = deepcopy(server_config)
        env = resolved_config.get("env")
        if not isinstance(env, dict):
            return resolved_config

        auth = self._server_auth(server_name)
        resolved_config["env"] = {
            env_key: self._resolve_env_value(env_value, auth)
            for env_key, env_value in env.items()
        }
        return resolved_config

    def _server_auth(self, server_name: str) -> dict:
        auth_provider = getattr(self.data_lifecycle_factory, "auth_provider", None)
        if auth_provider is None:
            return {}

        try:
            return auth_provider.provide(server_name)
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
