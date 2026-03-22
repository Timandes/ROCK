"""Wuying (阿里云云电脑) Operator implementation for managing sandboxes."""
import asyncio
import re
from datetime import datetime

from rock.config import WuyingConfig, WuyingPoolConfig
from rock.deployments.config import DockerDeploymentConfig
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
        # Step 1: Select pool
        pool_name = self._select_pool(config)
        if not pool_name:
            raise ValueError(
                f"No matching pool for image={config.image}, "
                f"cpus={config.cpus}, memory={config.memory}"
            )

        pool_config = self._wuying_config.pools[pool_name]
        logger.info(f"Selected pool '{pool_name}' for image={config.image}")

        # Step 2: Create desktop via ECD API
        client = self._create_ecd_client()
        request = ecd_models.CreateDesktopsRequest(
            region_id=self._wuying_config.region_id,
            bundle_id=pool_config.bundle_id,
            desktop_name=config.container_name,  # Use container_name as desktop name
            office_site_id=self._wuying_config.office_site_id,
            policy_group_id=self._wuying_config.policy_group_id,
            end_user_id=[self._wuying_config.ssh_username],  # Pre-provision user for SSH access
        )

        runtime = util_models.RuntimeOptions()
        response = await asyncio.to_thread(
            client.create_desktops_with_options, request, runtime
        )

        # Step 3: Extract desktop ID
        desktop_id = response.body.desktop_id[0] if response.body.desktop_id else None
        if not desktop_id:
            raise RuntimeError("CreateDesktops API did not return desktop_id")

        logger.info(f"Created desktop {desktop_id} with bundle {pool_config.bundle_id}")

        # Step 4: Return PENDING status
        return SandboxInfo(
            sandbox_id=desktop_id,
            host_ip="",
            host_name=desktop_id,
            state=State.PENDING,
            image=config.image,
            cpus=config.cpus,
            memory=config.memory,
            port_mapping={v: v for v in pool_config.ports.values()},
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
        client = self._create_ecd_client()
        request = ecd_models.DescribeDesktopsRequest(
            region_id=self._wuying_config.region_id,
            desktop_id=[sandbox_id],
        )

        runtime = util_models.RuntimeOptions()
        response = await asyncio.to_thread(
            client.describe_desktops_with_options, request, runtime
        )

        # Check if desktop exists
        if not response.body.desktops or len(response.body.desktops) == 0:
            logger.warning(f"Desktop {sandbox_id} not found")
            return SandboxInfo(
                sandbox_id=sandbox_id,
                state=State.PENDING,  # Use PENDING since STOPPED doesn't exist
            )

        desktop = response.body.desktops[0]
        desktop_status = desktop.desktop_status

        # Map status
        state = self._map_desktop_status(desktop_status)

        # Get IP address (attribute name is network_interface_ip)
        host_ip = ""
        if hasattr(desktop, 'network_interface_ip') and desktop.network_interface_ip:
            host_ip = desktop.network_interface_ip

        return SandboxInfo(
            sandbox_id=sandbox_id,
            host_ip=host_ip,
            host_name=desktop.desktop_name or sandbox_id,
            state=state,
            image="",  # Not available from DescribeDesktops
            cpus=0.0,  # Not available from DescribeDesktops
            memory="",  # Not available from DescribeDesktops
            port_mapping={},
        )

    async def get_desktop_password(self, sandbox_id: str) -> str | None:
        """Get the dynamic password for a cloud desktop.

        Args:
            sandbox_id: Sandbox identifier (desktop ID)

        Returns:
            Password string if found, None otherwise
        """
        client = self._create_ecd_client()
        request = ecd_models.DescribeUsersPasswordRequest(
            region_id=self._wuying_config.region_id,
            desktop_id=sandbox_id,
        )

        runtime = util_models.RuntimeOptions()
        try:
            response = await asyncio.to_thread(
                client.describe_users_password_with_options, request, runtime
            )

            if response.body.desktop_users:
                # Return the first user's password (typically 'admin')
                password = response.body.desktop_users[0].password
                logger.info(f"Got password for desktop {sandbox_id}")
                return password
            else:
                logger.warning(f"No desktop users found for {sandbox_id}")
                return None
        except Exception as e:
            logger.error(f"Failed to get password for desktop {sandbox_id}: {e}")
            return None

    async def stop(self, sandbox_id: str) -> bool:
        """Stop and delete a sandbox.

        State transitions:
        - Pending -> Running: Wait for creation
        - Running -> (Stop) -> Stopping -> Stopped: Wait for stop
        - Stopped -> (Delete): Delete

        Args:
            sandbox_id: Sandbox identifier (desktop ID)

        Returns:
            True if successful, False otherwise
        """
        client = self._create_ecd_client()
        runtime = util_models.RuntimeOptions()

        async def get_desktop_status():
            """Get current desktop status."""
            request = ecd_models.DescribeDesktopsRequest(
                region_id=self._wuying_config.region_id,
                desktop_id=[sandbox_id],
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
                logger.debug(f"Desktop {sandbox_id} status: {status}, waiting for {target_statuses}")
                await asyncio.sleep(interval)
                elapsed += interval
            return status  # Return current status if timeout

        try:
            # Step 1: Get current status
            current_status = await get_desktop_status()
            logger.info(f"Desktop {sandbox_id} current status: {current_status}")

            # Step 2: If Pending, wait for Running
            if current_status == "Pending":
                logger.info(f"Waiting for {sandbox_id} to become Running...")
                current_status = await wait_for_status(["Running", "Stopped"], timeout=300)
                if current_status is None:
                    logger.info(f"Desktop {sandbox_id} not found (may have been deleted)")
                    return True

            # Step 3: If Running, stop the desktop
            if current_status == "Running":
                stop_request = ecd_models.StopDesktopsRequest(
                    region_id=self._wuying_config.region_id,
                    desktop_id=[sandbox_id],
                )
                await asyncio.to_thread(
                    client.stop_desktops_with_options, stop_request, runtime
                )
                logger.info(f"Stopped desktop {sandbox_id}")

                # Wait for Stopped status
                current_status = await wait_for_status(["Stopped"], timeout=60)
                logger.info(f"Desktop {sandbox_id} status after stop: {current_status}")

            # Step 4: Delete the desktop
            delete_request = ecd_models.DeleteDesktopsRequest(
                region_id=self._wuying_config.region_id,
                desktop_id=[sandbox_id],
            )
            await asyncio.to_thread(
                client.delete_desktops_with_options, delete_request, runtime
            )
            logger.info(f"Initiated delete for desktop {sandbox_id}")

            # Step 5: Wait for deletion to complete (desktop disappears)
            # Status flow: Stopped -> Deleting -> Deleted (then disappears)
            deletion_timeout = 120
            elapsed = 0
            interval = 5
            while elapsed < deletion_timeout:
                status = await get_desktop_status()
                if status is None:
                    logger.info(f"Desktop {sandbox_id} deleted successfully")
                    return True
                logger.debug(f"Desktop {sandbox_id} status during deletion: {status}")
                await asyncio.sleep(interval)
                elapsed += interval

            # If we reach here, deletion didn't complete in time
            logger.warning(f"Desktop {sandbox_id} deletion did not complete within {deletion_timeout}s")
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

    def get_pool_config(self, pool_name: str) -> WuyingPoolConfig | None:
        """Get pool configuration by name.

        Args:
            pool_name: Name of the pool

        Returns:
            WuyingPoolConfig if found, None otherwise
        """
        return self._wuying_config.pools.get(pool_name)
