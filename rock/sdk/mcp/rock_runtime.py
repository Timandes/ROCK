from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from rock.actions import Command
from rock.sdk.sandbox.client import Sandbox
from rock.sdk.sandbox.config import SandboxConfig

BeforeLaunchHook = Callable[[Sandbox], Awaitable[None] | None]

logger = logging.getLogger(__name__)


class RockRuntimeError(RuntimeError):
    """Raised when the ROCK runtime cannot start, health check, or stop cleanly."""


class RockRuntimeConfigError(RockRuntimeError):
    """Raised when required ROCK runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class RockRuntimeConfig:
    base_url: str
    api_key: str
    image: str
    user_id: str
    experiment_id: str
    cluster: str
    cpus: float
    memory: str
    auto_clear_seconds: int

    @classmethod
    def from_env(cls) -> RockRuntimeConfig:
        api_key = os.getenv("ROCK_API_KEY", "").strip()
        if not api_key:
            raise RockRuntimeConfigError("ROCK_API_KEY is required")

        user_id = os.getenv("ROCK_USER_ID", "").strip()
        if not user_id:
            raise RockRuntimeConfigError("ROCK_USER_ID is required")

        try:
            cpus = float(os.getenv("ROCK_SANDBOX_CPUS", "4"))
        except ValueError as error:
            raise RockRuntimeConfigError("ROCK_SANDBOX_CPUS must be a number") from error

        try:
            auto_clear_seconds = int(os.getenv("ROCK_AUTO_CLEAR_SECONDS", "3600"))
        except ValueError as error:
            raise RockRuntimeConfigError("ROCK_AUTO_CLEAR_SECONDS must be an integer") from error

        return cls(
            base_url=os.getenv("ROCK_BASE_URL", "https://xrl.alibaba-inc.com").rstrip("/"),
            api_key=api_key,
            image=os.getenv(
                "ROCK_SANDBOX_IMAGE",
                "rock-instances-registry-vpc.cn-shanghai.cr.aliyuncs.com/instance/rock-mcp-base:v0.13.0",
            ),
            user_id=user_id,
            experiment_id=os.getenv("ROCK_EXPERIMENT_ID", "mcpenv"),
            cluster=os.getenv("ROCK_CLUSTER", "vpc-nt-a"),
            cpus=cpus,
            memory=os.getenv("ROCK_SANDBOX_MEMORY", "8g"),
            auto_clear_seconds=auto_clear_seconds,
        )


class RockRuntime:
    def __init__(
        self,
        config: RockRuntimeConfig | None = None,
        *,
        health_check_retries: int = 10,
        health_check_interval_seconds: float = 10.0,
        http_timeout_seconds: float = 10.0,
    ):
        self.config = config
        self.health_check_retries = health_check_retries
        self.health_check_interval_seconds = health_check_interval_seconds
        self.http_timeout_seconds = http_timeout_seconds
        self._sandbox: Sandbox | None = None
        self._sandbox_id: str | None = None
        self._started = False

    @property
    def sandbox_id(self) -> str | None:
        if self._sandbox is not None:
            return self._sandbox.sandbox_id
        return self._sandbox_id

    @property
    def sandbox(self) -> Sandbox | None:
        return self._sandbox

    @property
    def sse_headers(self) -> dict[str, str]:
        config = self._require_config()
        return {"XRL-Authorization": f"Bearer {config.api_key}"}

    def build_mcp_servers_json(self, servers: dict[str, Any]) -> str:
        return json.dumps({"mcpServers": servers}, indent=2)

    def get_server_url(self, server_name: str) -> str:
        sandbox_id = self.sandbox_id
        if not sandbox_id:
            raise RockRuntimeError("ROCK sandbox has not been started")

        config = self._require_config()
        return f"{config.base_url}/apis/envs/sandbox/v1/sandboxes/{sandbox_id}/proxy/{server_name}/sse"

    def get_all_server_urls(self, server_names: Iterable[str]) -> dict[str, str]:
        return {name: self.get_server_url(name) for name in server_names}

    async def start(
        self,
        servers: dict[str, Any],
        before_launch: BeforeLaunchHook | None = None,
    ) -> dict[str, str]:
        if self._started or self._sandbox is not None or self._sandbox_id is not None:
            raise RockRuntimeError("ROCK runtime has already been started")

        config = self._require_config()
        self._started = False
        self._sandbox = Sandbox(
            SandboxConfig(
                base_url=config.base_url,
                extra_headers=self.sse_headers,
                image=config.image,
                user_id=config.user_id,
                experiment_id=config.experiment_id,
                cluster=config.cluster,
                auto_clear_seconds=config.auto_clear_seconds,
                cpus=config.cpus,
                memory=config.memory,
            )
        )

        try:
            await self._sandbox.start()
            self._sandbox_id = self._sandbox.sandbox_id
            await self._prepare_directories()
            await self._write_mcp_config(servers)
            await self._run_before_launch_hook(before_launch)
            await self._launch_servers()
            await self._health_check(sorted(servers.keys()))
            self._started = True
            return self.get_all_server_urls(sorted(servers.keys()))
        except Exception as error:
            try:
                await self.stop()
            except Exception as cleanup_error:
                logger.warning("Failed to stop ROCK runtime after startup failure: %s", cleanup_error)
            if isinstance(error, RockRuntimeError):
                raise
            raise RockRuntimeError(f"Failed to start ROCK runtime: {error}") from error

    async def stop(self) -> None:
        sandbox = self._sandbox
        self._started = False
        self._sandbox = None
        self._sandbox_id = None
        if sandbox is not None:
            await sandbox.stop()

    async def _prepare_directories(self) -> None:
        sandbox = self._require_sandbox()
        result = await sandbox.execute(Command(command=["bash", "-c", "mkdir -p /app/workspace /data"]))
        if result.exit_code != 0:
            raise RockRuntimeError(f"Failed to prepare sandbox directories: {result}")

    async def _write_mcp_config(self, servers: dict[str, Any]) -> None:
        sandbox = self._require_sandbox()
        await sandbox.write_file_by_path(
            content=self.build_mcp_servers_json(servers),
            path="/app/mcp-servers.json",
        )

    async def _run_before_launch_hook(
        self,
        before_launch: BeforeLaunchHook | None,
    ) -> None:
        if before_launch is None:
            return

        result = before_launch(self._require_sandbox())
        if inspect.isawaitable(result):
            await result

    async def _launch_servers(self) -> None:
        sandbox = self._require_sandbox()
        result = await sandbox.execute(Command(command=["bash", "-c", "bash /app/launch.sh > /tmp/launch.log 2>&1 &"]))
        if result.exit_code != 0:
            raise RockRuntimeError(f"Failed to launch MCP servers: {result}")

    async def _health_check(self, server_names: list[str]) -> None:
        pending = set(server_names)
        if not pending:
            return

        async def check_one(client: httpx.AsyncClient, server_name: str) -> tuple[str, bool, str]:
            url = self.get_server_url(server_name)
            try:
                async with client.stream("GET", url, headers=self.sse_headers) as response:
                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" in content_type:
                        return server_name, True, "SSE stream ready"
                    return server_name, False, f"status={response.status_code}, content-type={content_type}"
            except Exception as error:
                return server_name, False, str(error)

        last_details: dict[str, str] = {}
        for attempt in range(self.health_check_retries):
            async with httpx.AsyncClient(timeout=self.http_timeout_seconds) as client:
                results = await asyncio.gather(*(check_one(client, name) for name in sorted(pending)))

            for server_name, ok, detail in results:
                if ok:
                    pending.discard(server_name)
                else:
                    last_details[server_name] = detail

            if not pending:
                return

            if attempt < self.health_check_retries - 1:
                await asyncio.sleep(self.health_check_interval_seconds)

        detail = ", ".join(f"{name}: {last_details.get(name, 'not ready')}" for name in sorted(pending))
        raise RockRuntimeError(f"ROCK MCP server health check failed: {detail}")

    def _require_config(self) -> RockRuntimeConfig:
        if self.config is None:
            self.config = RockRuntimeConfig.from_env()
        return self.config

    def _require_sandbox(self) -> Sandbox:
        if self._sandbox is None:
            raise RockRuntimeError("ROCK sandbox has not been started")
        return self._sandbox

    async def dump_sandbox_logs(self) -> dict[str, str]:
        """Dump sandbox logs for debugging when startup fails."""
        sandbox = self._sandbox
        if sandbox is None:
            return {}

        results: dict[str, str] = {}
        for label, path in [
            ("launch", "/tmp/launch.log"),
            ("github", "/app/config/dynamic/logs/github.log"),
            ("dynamic_config", "/app/config/dynamic/github.json"),
        ]:
            try:
                result = await sandbox.execute(
                    Command(command=["bash", "-c", f"cat {path} 2>/dev/null || echo '(no {label}.log)'"]),
                )
                results[label] = result.stdout
            except Exception as error:
                results[label] = f"(failed to read: {error})"

        try:
            result = await sandbox.execute(
                Command(
                    command=[
                        "bash",
                        "-c",
                        (
                            "ls -la /usr/local/bin/github-mcp-server 2>/dev/null; "
                            "echo '---'; ps aux 2>/dev/null || ps -ef 2>/dev/null || echo '(no ps)'"
                        ),
                    ]
                ),
            )
            results["process_info"] = result.stdout
        except Exception as error:
            results["process_info"] = f"(failed to read: {error})"

        return results

    async def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload a file to the sandbox."""
        from rock.actions import UploadRequest

        sandbox = self._require_sandbox()
        request = UploadRequest(source_path=local_path, target_path=remote_path)
        result = await sandbox.upload(request)
        if not result.success:
            raise RockRuntimeError(f"Upload failed: {result.message}")
        logger.info("Uploaded %s -> %s", local_path, remote_path)

    async def read_file(self, remote_path: str) -> str:
        """Read a file from the sandbox."""
        from rock.actions import ReadFileRequest

        sandbox = self._require_sandbox()
        request = ReadFileRequest(path=remote_path)
        result = await sandbox.read_file(request)
        return result.content
