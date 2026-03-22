"""Wuying (阿里云云电脑) Deployment implementation."""
import asyncio
from typing import Any

from typing_extensions import Self

from rock.actions import IsAliveResponse
from rock.deployments.abstract import AbstractDeployment
from rock.deployments.config import WuyingDeploymentConfig
from rock.deployments.hooks.abstract import CombinedDeploymentHook, DeploymentHook
from rock.logger import init_logger
from rock.rocklet.exceptions import DeploymentNotStartedError
from rock.sandbox.remote_sandbox import RemoteSandboxRuntime

logger = init_logger(__name__)


class WuyingDeployment(AbstractDeployment):
    """Deployment for Wuying (阿里云云电脑) via SSH.

    This deployment connects to a cloud desktop instance via SSH,
    starts the rocklet service, and provides a runtime interface.
    """

    def __init__(self, **kwargs: Any):
        """Initialize Wuying deployment.

        Args:
            **kwargs: Keyword arguments (see `WuyingDeploymentConfig` for details).
        """
        self._config = WuyingDeploymentConfig(**kwargs)
        self._runtime: RemoteSandboxRuntime | None = None
        self._ssh_client: Any = None  # paramiko.SSHClient
        self._hooks = CombinedDeploymentHook()

    def add_hook(self, hook: DeploymentHook):
        """Add a deployment hook."""
        self._hooks.add_hook(hook)

    @classmethod
    def from_config(cls, config: WuyingDeploymentConfig) -> Self:
        """Create deployment from configuration.

        Args:
            config: WuyingDeploymentConfig instance

        Returns:
            WuyingDeployment instance
        """
        return cls(**config.model_dump())

    @property
    def runtime(self) -> RemoteSandboxRuntime:
        """Returns the runtime if running.

        Raises:
            DeploymentNotStartedError: If the deployment was not started.
        """
        if self._runtime is None:
            raise DeploymentNotStartedError()
        return self._runtime

    async def is_alive(self) -> IsAliveResponse:
        """Checks if the runtime is alive.

        Raises:
            DeploymentNotStartedError: If the deployment was not started.
        """
        return await self.runtime.is_alive()

    async def start(self):
        """Start the deployment.

        1. Connect via SSH
        2. Start rocklet service
        3. Create RemoteSandboxRuntime
        """
        logger.info(f"Starting Wuying deployment for desktop {self._config.desktop_id}")

        # Step 1: Connect via SSH
        await self._ssh_connect()

        # Step 2: Start rocklet
        await self._start_rocklet()

        # Step 3: Create runtime
        self._runtime = RemoteSandboxRuntime(
            host=f"http://{self._config.host_ip}",
            port=self._config.proxy_port,
        )

        logger.info(f"Wuying deployment started for {self._config.desktop_id}")

    async def stop(self):
        """Stop the deployment.

        1. Close runtime
        2. Disconnect SSH
        """
        logger.info(f"Stopping Wuying deployment for desktop {self._config.desktop_id}")

        if self._runtime is not None:
            self._runtime.close()
            self._runtime = None

        await self._ssh_disconnect()

        logger.info(f"Wuying deployment stopped for {self._config.desktop_id}")

    async def _ssh_connect(self):
        """Establish SSH connection to the cloud desktop."""
        import paramiko

        logger.info(f"Connecting to {self._config.host_ip}:{self._config.ssh_port} via SSH")

        self._ssh_client = paramiko.SSHClient()
        self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Run in executor to avoid blocking
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._ssh_client.connect(
                hostname=self._config.host_ip,
                port=self._config.ssh_port,
                username=self._config.ssh_username,
                password=self._config.ssh_password,
                timeout=30,
            )
        )

        logger.info(f"SSH connection established to {self._config.host_ip}")

    async def _ssh_disconnect(self):
        """Close SSH connection."""
        if self._ssh_client is not None:
            self._ssh_client.close()
            self._ssh_client = None
            logger.info("SSH connection closed")

    async def _start_rocklet(self):
        """Start rocklet service on the cloud desktop.

        This method will:
        1. Check if rocklet is installed
        2. Install rocklet if not present
        3. Start rocklet service
        """
        if self._ssh_client is None:
            raise RuntimeError("SSH connection not established")

        loop = asyncio.get_running_loop()

        # Step 1: Check if rocklet is installed
        check_cmd = "which rocklet || echo 'NOT_FOUND'"
        stdin, stdout, stderr = await loop.run_in_executor(
            None,
            lambda: self._ssh_client.exec_command(check_cmd)
        )
        output = await loop.run_in_executor(None, lambda: stdout.read().decode())
        rocklet_installed = "NOT_FOUND" not in output

        if not rocklet_installed:
            logger.info("Rocklet not found, installing via pip...")
            # Install rocklet - it's part of rl-rock package
            # The package name is "rl-rock", and rocklet is an optional dependency
            install_commands = [
                # Try from Alibaba internal PyPI mirror (common in Aliyun environment)
                "pip install -i https://mirrors.aliyun.com/pypi/simple/ 'rl-rock[rocklet]' -q",
                # Try from PyPI directly
                "pip install 'rl-rock[rocklet]' -q",
                # Try from git (latest version)
                "pip install 'rl-rock[rocklet] @ git+https://github.com/alibaba/ROCK.git' -q",
            ]

            installed = False
            last_error = ""
            for install_cmd in install_commands:
                logger.debug(f"Trying install command: {install_cmd}")
                stdin, stdout, stderr = await loop.run_in_executor(
                    None,
                    lambda cmd=install_cmd: self._ssh_client.exec_command(cmd)
                )
                exit_code = await loop.run_in_executor(
                    None,
                    lambda: stdout.channel.recv_exit_status()
                )
                if exit_code == 0:
                    installed = True
                    logger.info("Rocklet installed successfully")
                    break
                else:
                    error_output = await loop.run_in_executor(
                        None,
                        lambda: stderr.read().decode()
                    )
                    last_error = error_output
                    logger.debug(f"Install failed: {error_output[:200]}")

            if not installed:
                raise RuntimeError(
                    f"Failed to install rocklet from any source. Last error: {last_error}"
                )

        # Step 2: Start rocklet
        # After pip install, the command might not be in PATH immediately
        # Try to find rocklet executable in common locations
        find_cmd = "which rocklet 2>/dev/null || test -f ~/.local/bin/rocklet && echo ~/.local/bin/rocklet || echo ''"
        stdin, stdout, stderr = await loop.run_in_executor(
            None,
            lambda cmd=find_cmd: self._ssh_client.exec_command(cmd)
        )
        rocklet_path = await loop.run_in_executor(None, lambda: stdout.read().decode().strip())

        if rocklet_path and rocklet_path.startswith("/"):
            # Found rocklet executable
            start_cmd = f"nohup {rocklet_path} --port {self._config.proxy_port} > /tmp/rocklet.log 2>&1 &"
        else:
            # rocklet might be installed but PATH not updated
            # Try using bash -l to load profile or use explicit path
            start_cmd = f"nohup bash -l -c 'rocklet --port {self._config.proxy_port}' > /tmp/rocklet.log 2>&1 &"

        logger.info(f"Starting rocklet with command: {start_cmd}")

        stdin, stdout, stderr = await loop.run_in_executor(
            None,
            lambda cmd=start_cmd: self._ssh_client.exec_command(cmd)
        )

        # For nohup commands, the shell returns immediately
        # Wait for rocklet to start
        await asyncio.sleep(3)

        # Check if rocklet is running and get its status
        check_cmd = "pgrep -a -f 'rocklet' || echo 'No rocklet process'; cat /tmp/rocklet.log 2>/dev/null | tail -20 || echo 'No log file'"
        stdin, stdout, stderr = await loop.run_in_executor(
            None,
            lambda: self._ssh_client.exec_command(check_cmd)
        )
        output = await loop.run_in_executor(None, lambda: stdout.read().decode().strip())
        logger.info(f"Rocklet status check: {output}")

        # Check if process is running
        if "rocklet" in output and "python" in output.lower():
            logger.info("Rocklet started successfully")
        else:
            # Check log for errors
            if "error" in output.lower() or "traceback" in output.lower():
                raise RuntimeError(f"Failed to start rocklet: {output}")
            logger.warning(f"Rocklet status unclear, proceeding anyway: {output[:200]}")

    def _build_rocklet_start_command(self) -> str:
        """Build the command to start rocklet.

        Returns:
            Command string to start rocklet
        """
        return f"rocklet start --port {self._config.proxy_port}"
