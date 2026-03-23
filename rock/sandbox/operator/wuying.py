"""Wuying (阿里云云电脑) Operator implementation for managing sandboxes."""
import asyncio
import re
from datetime import datetime

from rock.config import WuyingConfig, WuyingPoolConfig
from rock.deployments.config import DockerDeploymentConfig
from rock.deployments.status import PhaseStatus, Status
from rock.sandbox.operator.abstract import AbstractOperator
from rock.actions.sandbox.sandbox_info import SandboxInfo
from rock.actions.sandbox.response import State
from rock.logger import init_logger

logger = init_logger(__name__)

# Conditionally import ECD SDK
try:
    from alibabacloud_ecd20200930.client import Client as ECDClient
    from alibabacloud_ecd20200930 import models as ecd_models
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as util_models
    ECD_AVAILABLE = True
except ImportError:
    ECD_AVAILABLE = False
    ECDClient = None
    ecd_models = None
    open_api_models = None
    util_models = None


class WuyingOperator(AbstractOperator):
    """Operator for managing sandboxes via Aliyun ECD (云电脑).

    This operator manages cloud desktop instances using the Aliyun ECD API.
    It maps user-requested images to pre-configured bundles via pool configuration.
    """

    def __init__(self, wuying_config: WuyingConfig, redis_provider=None):
        """Initialize Wuying operator.

        Args:
            wuying_config: WuyingConfig object containing region, credentials, and pools
            redis_provider: Optional Redis provider for caching sandbox info

        Raises:
            ImportError: If alibabacloud-ecd20200930 is not installed
        """
        if not ECD_AVAILABLE:
            raise ImportError(
                "alibabacloud-ecd20200930 is required for WuyingOperator. "
                "Install it with: pip install alibabacloud-ecd20200930"
            )
        self._wuying_config = wuying_config
        self._redis_provider = redis_provider
        self._client: ECDClient | None = None
        logger.info(f"Initialized WuyingOperator with region: {wuying_config.region_id}")

    def _create_ecd_client(self) -> ECDClient:
        """Create or return cached ECD client.

        Uses environment variables for credentials:
        - ALIBABA_CLOUD_ACCESS_KEY_ID
        - ALIBABA_CLOUD_ACCESS_KEY_SECRET

        Returns:
            ECD Client instance
        """
        if self._client is not None:
            return self._client

        import os

        config = open_api_models.Config(
            access_key_id=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", ""),
            access_key_secret=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", ""),
        )
        config.endpoint = self._wuying_config.endpoint
        self._client = ECDClient(config)
        return self._client

    async def submit(self, config: DockerDeploymentConfig, user_info: dict = {}) -> SandboxInfo:
        """Submit a new sandbox deployment.

        Args:
            config: Docker deployment configuration
            user_info: User metadata (user_id, experiment_id, namespace, rock_authorization)

        Returns:
            SandboxInfo with sandbox metadata

        Raises:
            ValueError: No matching pool found for the requested image/resources
        """
        # sandbox_id is the container_name (UUID), used as primary key
        sandbox_id = config.container_name

        # Step 1: Select pool
        pool_name = self._select_pool(config)
        if not pool_name:
            raise ValueError(
                f"No matching pool for image={config.image}, "
                f"cpus={config.cpus}, memory={config.memory}"
            )

        pool_config = self._wuying_config.pools[pool_name]
        logger.info(f"[{sandbox_id}] Selected pool '{pool_name}' for image={config.image}")

        # Step 2: Create desktop via ECD API
        # Use sandbox_id as desktop_name for easy identification
        client = self._create_ecd_client()
        request = ecd_models.CreateDesktopsRequest(
            region_id=self._wuying_config.region_id,
            bundle_id=pool_config.bundle_id,
            desktop_name=sandbox_id,  # Use sandbox_id as desktop name
            office_site_id=self._wuying_config.office_site_id,
            policy_group_id=self._wuying_config.policy_group_id,
            end_user_id=[self._wuying_config.ssh_username],  # Pre-provision user for SSH access
        )

        runtime = util_models.RuntimeOptions()
        response = await asyncio.to_thread(
            client.create_desktops_with_options, request, runtime
        )

        # Step 3: Extract desktop ID (ECD-generated)
        desktop_id = response.body.desktop_id[0] if response.body.desktop_id else None
        if not desktop_id:
            raise RuntimeError("CreateDesktops API did not return desktop_id")

        logger.info(f"[{sandbox_id}] Created desktop {desktop_id} with bundle {pool_config.bundle_id}")

        # Build port_mapping using Port enum integer values as keys
        # For Wuying: local_port == container_port (no Docker port mapping needed)
        # rocklet listens on 8000, SSH on 22, WebSocket on 8080
        from rock.deployments.constants import Port

        port_mapping = {
            int(Port.PROXY): pool_config.ports.get("proxy", 8000),    # 22555 -> 8000
            int(Port.SERVER): pool_config.ports.get("server", 8080),  # 8080 -> 8080
            int(Port.SSH): pool_config.ports.get("ssh", 22),          # 22 -> 22
        }

        # Step 4: Return PENDING status
        # sandbox_id is the primary key (UUID)
        # desktop_id is the ECD-generated ID, stored as separate field
        return SandboxInfo(
            sandbox_id=sandbox_id,
            desktop_id=desktop_id,  # ECD-generated ID for API calls
            host_ip="",
            host_name=sandbox_id,
            state=State.PENDING,
            image=config.image,
            cpus=config.cpus,
            memory=config.memory,
            port_mapping=port_mapping,
            user_id=user_info.get("user_id", "default"),
            experiment_id=user_info.get("experiment_id", "default"),
            namespace=user_info.get("namespace", "default"),
            create_time=datetime.now().isoformat(),
        )

    async def get_status(self, sandbox_id: str) -> SandboxInfo:
        """Get sandbox status.

        Args:
            sandbox_id: Sandbox identifier (desktop ID)

        Returns:
            SandboxInfo with current status
        """
        # First, get desktop_id from Redis cache
        desktop_id = await self._get_desktop_id(sandbox_id)
        if not desktop_id:
            logger.warning(f"[{sandbox_id}] No desktop_id found in Redis, returning PENDING")
            return SandboxInfo(
                sandbox_id=sandbox_id,
                state=State.PENDING,
                phases=self._build_phases(State.PENDING),
            )

        # Query ECD API using desktop_id
        client = self._create_ecd_client()
        request = ecd_models.DescribeDesktopsRequest(
            region_id=self._wuying_config.region_id,
            desktop_id=[desktop_id],
        )

        runtime = util_models.RuntimeOptions()
        response = await asyncio.to_thread(
            client.describe_desktops_with_options, request, runtime
        )

        # Check if desktop exists
        if not response.body.desktops or len(response.body.desktops) == 0:
            logger.warning(f"[{sandbox_id}] Desktop {desktop_id} not found in ECD")
            return SandboxInfo(
                sandbox_id=sandbox_id,
                desktop_id=desktop_id,
                state=State.PENDING,
                phases=self._build_phases(State.PENDING),
            )

        desktop = response.body.desktops[0]
        desktop_status = desktop.desktop_status

        # Get IP address (attribute name is network_interface_ip)
        host_ip = ""
        if hasattr(desktop, 'network_interface_ip') and desktop.network_interface_ip:
            host_ip = desktop.network_interface_ip

        # Get port_mapping from Redis cache (preserved from submit())
        port_mapping = await self._get_port_mapping(sandbox_id)

        # Check if rocklet is alive (K8s Operator style: state depends on is_alive)
        # Only check if desktop is running and has IP
        desktop_running = desktop_status == "Running"
        is_alive = False
        if desktop_running and host_ip:
            is_alive = await self._check_rocklet_alive(sandbox_id, host_ip, port_mapping)
            # Start rocklet in background if not alive (non-blocking)
            if not is_alive:
                asyncio.create_task(self._ensure_rocklet_running(sandbox_id, desktop_id, host_ip))

        # State is determined by is_alive, not just desktop status (K8s Operator style)
        # This ensures clients wait until rocklet is actually responding
        state = State.RUNNING if is_alive else State.PENDING

        # Build phases based on desktop status and rocklet status
        phases = self._build_phases(State.RUNNING if desktop_running else State.PENDING, is_alive)

        return SandboxInfo(
            sandbox_id=sandbox_id,
            desktop_id=desktop_id,
            host_ip=host_ip,
            host_name=desktop.desktop_name or sandbox_id,
            state=state,
            phases=phases,
            image="",  # Not available from DescribeDesktops
            cpus=0.0,  # Not available from DescribeDesktops
            memory="",  # Not available from DescribeDesktops
            port_mapping=port_mapping or {},
        )

    async def get_desktop_password(self, sandbox_id: str) -> str | None:
        """Get the dynamic password for a cloud desktop.

        Args:
            sandbox_id: Sandbox identifier (UUID)

        Returns:
            Password string if found, None otherwise
        """
        # Get desktop_id from Redis
        desktop_id = await self._get_desktop_id(sandbox_id)
        if not desktop_id:
            logger.warning(f"[{sandbox_id}] Cannot get password: desktop_id not found")
            return None

        client = self._create_ecd_client()
        request = ecd_models.DescribeUsersPasswordRequest(
            region_id=self._wuying_config.region_id,
            desktop_id=desktop_id,
        )

        runtime = util_models.RuntimeOptions()
        try:
            response = await asyncio.to_thread(
                client.describe_users_password_with_options, request, runtime
            )

            if response.body.desktop_users:
                # Return the first user's password (typically 'admin')
                password = response.body.desktop_users[0].password
                logger.info(f"[{sandbox_id}] Got password for desktop {desktop_id}")
                return password
            else:
                logger.warning(f"[{sandbox_id}] No desktop users found for {desktop_id}")
                return None
        except Exception as e:
            logger.error(f"[{sandbox_id}] Failed to get password for desktop {desktop_id}: {e}")
            return None

    async def stop(self, sandbox_id: str) -> bool:
        """Stop and delete a sandbox.

        State transitions:
        - Pending -> Running: Wait for creation
        - Running -> (Stop) -> Stopping -> Stopped: Wait for stop
        - Stopped -> (Delete): Delete

        Args:
            sandbox_id: Sandbox identifier (UUID)

        Returns:
            True if successful, False otherwise
        """
        # Get desktop_id from Redis
        desktop_id = await self._get_desktop_id(sandbox_id)
        if not desktop_id:
            logger.warning(f"[{sandbox_id}] Cannot stop: desktop_id not found in Redis")
            return False

        client = self._create_ecd_client()
        runtime = util_models.RuntimeOptions()

        async def get_desktop_status():
            """Get current desktop status."""
            request = ecd_models.DescribeDesktopsRequest(
                region_id=self._wuying_config.region_id,
                desktop_id=[desktop_id],
            )
            response = await asyncio.to_thread(
                client.describe_desktops_with_options, request, runtime
            )
            if not response.body.desktops:
                return None
            return response.body.desktops[0].desktop_status

        async def wait_for_status(target_statuses: list, timeout: int = 120, interval: int = 5):
            """Wait for desktop to reach one of the target statuses."""
            elapsed = 0
            while elapsed < timeout:
                status = await get_desktop_status()
                if status is None:
                    return None  # Desktop not found
                if status in target_statuses:
                    return status
                logger.debug(f"[{sandbox_id}] Desktop {desktop_id} status: {status}, waiting for {target_statuses}")
                await asyncio.sleep(interval)
                elapsed += interval
            return status  # Return current status if timeout

        try:
            # Step 1: Get current status
            current_status = await get_desktop_status()
            logger.info(f"[{sandbox_id}] Desktop {desktop_id} current status: {current_status}")

            # Step 2: If Pending, wait for Running
            if current_status == "Pending":
                logger.info(f"[{sandbox_id}] Waiting for {desktop_id} to become Running...")
                current_status = await wait_for_status(["Running", "Stopped"], timeout=300)
                if current_status is None:
                    logger.info(f"[{sandbox_id}] Desktop {desktop_id} not found (may have been deleted)")
                    return True

            # Step 3: If Running, stop the desktop
            if current_status == "Running":
                stop_request = ecd_models.StopDesktopsRequest(
                    region_id=self._wuying_config.region_id,
                    desktop_id=[desktop_id],
                )
                await asyncio.to_thread(
                    client.stop_desktops_with_options, stop_request, runtime
                )
                logger.info(f"[{sandbox_id}] Stopped desktop {desktop_id}")

                # Wait for Stopped status
                current_status = await wait_for_status(["Stopped"], timeout=60)
                logger.info(f"[{sandbox_id}] Desktop {desktop_id} status after stop: {current_status}")

            # Step 4: Delete the desktop
            delete_request = ecd_models.DeleteDesktopsRequest(
                region_id=self._wuying_config.region_id,
                desktop_id=[desktop_id],
            )
            await asyncio.to_thread(
                client.delete_desktops_with_options, delete_request, runtime
            )
            logger.info(f"[{sandbox_id}] Initiated delete for desktop {desktop_id}")

            # Step 5: Wait for deletion to complete (desktop disappears)
            # Status flow: Stopped -> Deleting -> Deleted (then disappears)
            deletion_timeout = 120
            elapsed = 0
            interval = 5
            while elapsed < deletion_timeout:
                status = await get_desktop_status()
                if status is None:
                    logger.info(f"[{sandbox_id}] Desktop {desktop_id} deleted successfully")
                    return True
                logger.debug(f"[{sandbox_id}] Desktop {desktop_id} status during deletion: {status}")
                await asyncio.sleep(interval)
                elapsed += interval

            # If we reach here, deletion didn't complete in time
            logger.warning(f"[{sandbox_id}] Desktop {desktop_id} deletion did not complete within {deletion_timeout}s")
            return True

        except Exception as e:
            error_code = ""
            if hasattr(e, 'data') and e.data:
                error_code = e.data.get('Code', '')

            # If desktop not found, consider it deleted
            if "InvalidDesktopIds" in error_code:
                logger.info(f"Desktop {sandbox_id} already deleted or not found")
                return True

            logger.error(f"Failed to delete desktop {sandbox_id}: {e}")
            raise

    def _select_pool(self, config: DockerDeploymentConfig) -> str | None:
        """Select the best matching pool for the given deployment config.

        Selection criteria:
        1. Pool image must exactly match config image
        2. Pool resources (cpus, memory) must be >= config requirements
        3. Among all matching pools, select the one with smallest resource capacity

        Args:
            config: Docker deployment configuration

        Returns:
            Pool name if found, None otherwise
        """
        if not self._wuying_config.pools:
            return None

        config_memory_mb = self._parse_memory_to_mb(config.memory)
        matching_pools: list[tuple[str, WuyingPoolConfig, float]] = []

        for pool_name, pool_config in self._wuying_config.pools.items():
            # Check image match
            if pool_config.image != config.image:
                continue

            # Check resource capacity (pool must have >= required resources)
            pool_memory_mb = self._parse_memory_to_mb(pool_config.memory)
            if pool_config.cpus < config.cpus or pool_memory_mb < config_memory_mb:
                continue

            # Calculate score for this matching pool (lower = smaller capacity)
            score = self._get_pool_resource_score(pool_config)
            matching_pools.append((pool_name, pool_config, score))

        if not matching_pools:
            return None

        # Select pool with smallest resource capacity (best fit)
        matching_pools.sort(key=lambda x: x[2])
        best_pool_name, _, _ = matching_pools[0]
        return best_pool_name

    def _parse_memory_to_mb(self, memory: str) -> float:
        """Parse memory string to MB for comparison."""
        memory = memory.lower().strip()

        # Extract number and unit
        match = re.match(r'^(\d+(\.\d+)?)\s*([a-z]*)$', memory)
        if not match:
            try:
                return float(memory) / (1024 * 1024)  # Assume bytes
            except (ValueError, TypeError):
                return 0

        value = float(match.group(1))
        unit = match.group(3)

        # Convert to MB
        if unit in ('', 'b'):
            return value / (1024 * 1024)
        elif unit in ('k', 'kb'):
            return value / 1024
        elif unit in ('m', 'mb', 'mi'):
            return value
        elif unit in ('g', 'gb', 'gi'):
            return value * 1024
        elif unit in ('t', 'tb', 'ti'):
            return value * 1024 * 1024
        else:
            return 0

    async def _get_desktop_id(self, sandbox_id: str) -> str | None:
        """Get desktop_id from Redis cache.

        Args:
            sandbox_id: Sandbox identifier (UUID)

        Returns:
            desktop_id if found, None otherwise
        """
        if not self._redis_provider:
            logger.warning(f"[{sandbox_id}] No redis_provider, cannot get desktop_id")
            return None

        try:
            from rock.admin.core.redis_key import alive_sandbox_key

            sandbox_info = await self._redis_provider.json_get(alive_sandbox_key(sandbox_id), "$")
            if sandbox_info and len(sandbox_info) > 0:
                desktop_id = sandbox_info[0].get("desktop_id")
                if desktop_id:
                    logger.debug(f"[{sandbox_id}] Found desktop_id: {desktop_id}")
                    return desktop_id
        except Exception as e:
            logger.error(f"[{sandbox_id}] Failed to get desktop_id from Redis: {e}")

        return None

    async def _get_port_mapping(self, sandbox_id: str) -> dict | None:
        """Get port_mapping from Redis cache.

        Args:
            sandbox_id: Sandbox identifier (UUID)

        Returns:
            port_mapping dict with integer keys if found, None otherwise
        """
        if not self._redis_provider:
            return None

        try:
            from rock.admin.core.redis_key import alive_sandbox_key

            sandbox_info = await self._redis_provider.json_get(alive_sandbox_key(sandbox_id), "$")
            if sandbox_info and len(sandbox_info) > 0:
                raw_mapping = sandbox_info[0].get("port_mapping", {})
                # Convert string keys to integers (JSON serialization converts int keys to strings)
                return {int(k): v for k, v in raw_mapping.items()}
        except Exception as e:
            logger.error(f"[{sandbox_id}] Failed to get port_mapping from Redis: {e}")

        return None

    async def _ensure_rocklet_running(self, sandbox_id: str, desktop_id: str, host_ip: str) -> None:
        """Ensure rocklet is running on the cloud desktop.

        This method checks if rocklet is already running, and if not,
        connects via SSH to install and start it.

        Args:
            sandbox_id: Sandbox identifier (UUID)
            desktop_id: ECD desktop ID
            host_ip: Desktop IP address
        """
        import httpx

        # First check if rocklet is already responding
        port_mapping = await self._get_port_mapping(sandbox_id)
        rocklet_port = port_mapping.get(22555, 8000) if port_mapping else 8000

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"http://{host_ip}:{rocklet_port}/is_alive")
                if resp.status_code == 200:
                    logger.debug(f"[{sandbox_id}] Rocklet is already running on {host_ip}:{rocklet_port}")
                    return
        except Exception:
            pass  # Rocklet not responding, need to start it

        logger.info(f"[{sandbox_id}] Rocklet not responding, starting via SSH...")

        # Get password
        password = await self.get_desktop_password(sandbox_id)
        if not password:
            logger.warning(f"[{sandbox_id}] Cannot get password, using config password")
            password = self._wuying_config.ssh_password

        # Connect via SSH and start rocklet
        await self._ssh_start_rocklet(sandbox_id, host_ip, password, rocklet_port)

    async def _ssh_start_rocklet(self, sandbox_id: str, host_ip: str, password: str, rocklet_port: int) -> None:
        """Connect via SSH and start rocklet.

        Args:
            sandbox_id: Sandbox identifier
            host_ip: Desktop IP address
            password: SSH password
            rocklet_port: Port for rocklet to listen on
        """
        try:
            import paramiko
        except ImportError:
            logger.error(f"[{sandbox_id}] paramiko not installed, cannot start rocklet via SSH")
            return

        ssh_client = None
        try:
            loop = asyncio.get_running_loop()
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Connect
            await loop.run_in_executor(
                None,
                lambda: ssh_client.connect(
                    hostname=host_ip,
                    port=22,
                    username=self._wuying_config.ssh_username,
                    password=password,
                    timeout=30,
                )
            )
            logger.info(f"[{sandbox_id}] SSH connected to {host_ip}")

            # Check if rocklet is installed
            stdin, stdout, stderr = await loop.run_in_executor(
                None, lambda: ssh_client.exec_command("which rocklet || echo 'NOT_FOUND'")
            )
            output = await loop.run_in_executor(None, lambda: stdout.read().decode())

            if "NOT_FOUND" in output:
                # Install rocklet
                logger.info(f"[{sandbox_id}] Installing rocklet...")
                install_cmd = "pip install -i https://mirrors.aliyun.com/pypi/simple/ 'rl-rock[rocklet]' -q"
                stdin, stdout, stderr = await loop.run_in_executor(
                    None, lambda: ssh_client.exec_command(install_cmd)
                )
                await loop.run_in_executor(None, lambda: stdout.channel.recv_exit_status())
                logger.info(f"[{sandbox_id}] Rocklet installed")

            # Start rocklet in background
            start_cmd = f"nohup ~/.local/bin/rocklet --port {rocklet_port} > /tmp/rocklet.log 2>&1 &"
            await loop.run_in_executor(
                None, lambda: ssh_client.exec_command(start_cmd)
            )
            logger.info(f"[{sandbox_id}] Rocklet started on port {rocklet_port}")

            # Wait a moment and verify
            await asyncio.sleep(2)
            stdin, stdout, stderr = await loop.run_in_executor(
                None, lambda: ssh_client.exec_command("pgrep -f rocklet || echo 'NOT_RUNNING'")
            )
            output = await loop.run_in_executor(None, lambda: stdout.read().decode())
            if "NOT_RUNNING" not in output:
                logger.info(f"[{sandbox_id}] Rocklet is running")
            else:
                logger.warning(f"[{sandbox_id}] Rocklet may not have started properly")

        except Exception as e:
            logger.error(f"[{sandbox_id}] Failed to start rocklet via SSH: {e}")
        finally:
            if ssh_client:
                ssh_client.close()

    def _get_pool_resource_score(self, pool_config: WuyingPoolConfig) -> float:
        """Calculate resource score for a pool (lower is better for selection)."""
        memory_mb = self._parse_memory_to_mb(pool_config.memory)
        # Normalize memory to GB and add to cpus for a unified score
        return pool_config.cpus + memory_mb / 1024

    def _map_desktop_status(self, desktop_status: str) -> State:
        """Map Aliyun ECD desktop status to ROCK state.

        Note: ROCK State enum only has PENDING and RUNNING.
        Stopped/Stopping states are mapped to PENDING (not running).

        Args:
            desktop_status: Aliyun ECD desktop status string

        Returns:
            State enum value
        """
        running_states = {"Running"}
        # All other states are considered "not running" = PENDING
        if desktop_status in running_states:
            return State.RUNNING
        return State.PENDING

    def _build_phases(self, state: State, is_alive: bool = False) -> dict[str, PhaseStatus]:
        """Build phases dict consistent with Ray/Docker operators.

        Maps Wuying concepts to standard phases:
        - image_pull: Desktop creation (云电脑创建)
        - docker_run: Rocklet running status

        Args:
            state: Current sandbox state
            is_alive: Whether rocklet is responding

        Returns:
            dict with 'image_pull' and 'docker_run' PhaseStatus
        """
        if state == State.RUNNING and is_alive:
            return {
                "image_pull": PhaseStatus(status=Status.SUCCESS, message="desktop created"),
                "docker_run": PhaseStatus(status=Status.SUCCESS, message="rocklet running"),
            }
        elif state == State.RUNNING:
            # Desktop running but rocklet not ready
            return {
                "image_pull": PhaseStatus(status=Status.SUCCESS, message="desktop created"),
                "docker_run": PhaseStatus(status=Status.RUNNING, message="rocklet starting"),
            }
        else:
            # Desktop not ready
            return {
                "image_pull": PhaseStatus(status=Status.WAITING, message="desktop creating"),
                "docker_run": PhaseStatus(status=Status.WAITING, message="waiting"),
            }

    async def _check_rocklet_alive(self, sandbox_id: str, host_ip: str, port_mapping: dict | None) -> bool:
        """Check if rocklet is responding on the cloud desktop.

        Args:
            sandbox_id: Sandbox identifier (UUID)
            host_ip: Desktop IP address
            port_mapping: Port mapping dict

        Returns:
            True if rocklet is alive, False otherwise
        """
        import httpx

        rocklet_port = port_mapping.get(22555, 8000) if port_mapping else 8000

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"http://{host_ip}:{rocklet_port}/is_alive")
                if resp.status_code == 200:
                    return True
        except Exception:
            pass  # Rocklet not responding
        return False

    def get_pool_config(self, pool_name: str) -> WuyingPoolConfig | None:
        """Get pool configuration by name.

        Args:
            pool_name: Name of the pool

        Returns:
            WuyingPoolConfig if found, None otherwise
        """
        return self._wuying_config.pools.get(pool_name)
