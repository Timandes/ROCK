"""Tests for WuyingOperator.

Note: This test file mocks the alibabacloud ECD SDK since it may not be installed
in the development environment.
"""
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Mock ECD SDK before importing WuyingOperator
mock_ecd_models = MagicMock()
mock_open_api_models = MagicMock()
mock_util_models = MagicMock()

sys.modules["alibabacloud_ecd20200930"] = MagicMock()
sys.modules["alibabacloud_ecd20200930.client"] = MagicMock()
sys.modules["alibabacloud_ecd20200930.models"] = mock_ecd_models
sys.modules["alibabacloud_tea_openapi"] = MagicMock()
sys.modules["alibabacloud_tea_openapi.models"] = mock_open_api_models
sys.modules["alibabacloud_tea_util"] = MagicMock()
sys.modules["alibabacloud_tea_util.models"] = mock_util_models

import pytest

from rock.config import WuyingConfig, WuyingPoolConfig
from rock.deployments.config import DockerDeploymentConfig
from rock.sandbox.operator.wuying import WuyingOperator
from rock.actions.sandbox.response import State


class TestWuyingOperatorPoolSelection:
    """Tests for WuyingOperator pool selection logic."""

    @pytest.fixture
    def wuying_config(self):
        """Create a WuyingConfig with test pools."""
        return WuyingConfig(
            region_id="cn-hangzhou",
            office_site_id="cn-hangzhou+dir-test",
            policy_group_id="pg-test",
            pools={
                "pool-python-2c4g": WuyingPoolConfig(
                    image="python:3.11",
                    bundle_id="b-python-2c4g",
                    cpus=2,
                    memory="4g",
                ),
                "pool-python-4c8g": WuyingPoolConfig(
                    image="python:3.11",
                    bundle_id="b-python-4c8g",
                    cpus=4,
                    memory="8g",
                ),
                "pool-node-2c4g": WuyingPoolConfig(
                    image="node:18",
                    bundle_id="b-node-2c4g",
                    cpus=2,
                    memory="4g",
                ),
            },
        )

    @pytest.fixture
    def operator(self, wuying_config):
        """Create a WuyingOperator instance."""
        return WuyingOperator(wuying_config=wuying_config)

    def test_select_pool_exact_match(self, operator):
        """Test pool selection with exact match."""
        config = DockerDeploymentConfig(
            image="python:3.11",
            cpus=2,
            memory="4g",
            container_name="test-sandbox",
        )

        pool_name = operator._select_pool(config)

        assert pool_name == "pool-python-2c4g"

    def test_select_pool_best_fit(self, operator):
        """Test pool selection with best fit (smallest sufficient)."""
        config = DockerDeploymentConfig(
            image="python:3.11",
            cpus=2,
            memory="4g",
            container_name="test-sandbox",
        )

        # Should select 2c4g (smaller) instead of 4c8g
        pool_name = operator._select_pool(config)

        assert pool_name == "pool-python-2c4g"

    def test_select_pool_different_image(self, operator):
        """Test pool selection with different image."""
        config = DockerDeploymentConfig(
            image="node:18",
            cpus=2,
            memory="4g",
            container_name="test-sandbox",
        )

        pool_name = operator._select_pool(config)

        assert pool_name == "pool-node-2c4g"

    def test_select_pool_no_match(self, operator):
        """Test pool selection with no matching pool."""
        config = DockerDeploymentConfig(
            image="ubuntu:22.04",
            cpus=2,
            memory="4g",
            container_name="test-sandbox",
        )

        pool_name = operator._select_pool(config)

        assert pool_name is None

    def test_select_pool_insufficient_resources(self, operator):
        """Test pool selection with insufficient resources."""
        config = DockerDeploymentConfig(
            image="python:3.11",
            cpus=8,
            memory="16g",
            container_name="test-sandbox",
        )

        pool_name = operator._select_pool(config)

        assert pool_name is None


class TestWuyingOperatorDesktopStatusMapping:
    """Tests for WuyingOperator desktop status mapping."""

    @pytest.fixture
    def operator(self):
        """Create a WuyingOperator instance."""
        config = WuyingConfig()
        return WuyingOperator(wuying_config=config)

    def test_map_desktop_status_running(self, operator):
        """Test mapping running status."""
        state = operator._map_desktop_status("Running")
        assert state == State.RUNNING

    def test_map_desktop_status_stopped(self, operator):
        """Test mapping stopped status (mapped to PENDING, not running)."""
        state = operator._map_desktop_status("Stopped")
        # Note: State enum only has PENDING and RUNNING
        # STOPPED is represented as PENDING (not running)
        assert state == State.PENDING

    def test_map_desktop_status_pending(self, operator):
        """Test mapping pending/creating status."""
        state = operator._map_desktop_status("Creating")
        assert state == State.PENDING

    def test_map_desktop_status_unknown(self, operator):
        """Test mapping unknown status (defaults to PENDING)."""
        state = operator._map_desktop_status("Unknown")
        assert state == State.PENDING


class TestWuyingOperatorGetPoolConfig:
    """Tests for WuyingOperator.get_pool_config."""

    @pytest.fixture
    def wuying_config(self):
        """Create a WuyingConfig with test pools."""
        return WuyingConfig(
            pools={
                "pool-test": WuyingPoolConfig(
                    image="python:3.11",
                    bundle_id="b-test",
                    cpus=2,
                    memory="4g",
                ),
            },
        )

    @pytest.fixture
    def operator(self, wuying_config):
        """Create a WuyingOperator instance."""
        return WuyingOperator(wuying_config=wuying_config)

    def test_get_pool_config_existing(self, operator):
        """Test getting existing pool config."""
        pool_config = operator.get_pool_config("pool-test")

        assert pool_config is not None
        assert pool_config.bundle_id == "b-test"

    def test_get_pool_config_non_existing(self, operator):
        """Test getting non-existing pool config."""
        pool_config = operator.get_pool_config("non-existing")

        assert pool_config is None


class TestWuyingOperatorSubmit:
    """Tests for WuyingOperator.submit method."""

    @pytest.fixture
    def wuying_config(self):
        """Create a WuyingConfig with test pools."""
        return WuyingConfig(
            region_id="cn-hangzhou",
            office_site_id="cn-hangzhou+dir-test",
            policy_group_id="pg-test",
            pools={
                "pool-python-2c4g": WuyingPoolConfig(
                    image="python:3.11",
                    bundle_id="b-python-2c4g",
                    cpus=2,
                    memory="4g",
                ),
            },
        )

    @pytest.fixture
    def operator(self, wuying_config):
        """Create a WuyingOperator instance."""
        return WuyingOperator(wuying_config=wuying_config)

    @pytest.mark.asyncio
    async def test_submit_creates_desktop(self, operator):
        """Test submit creates a desktop and returns SandboxInfo."""
        config = DockerDeploymentConfig(
            image="python:3.11",
            cpus=2,
            memory="4g",
            container_name="test-sandbox",
        )

        # Mock the ECD client and response
        mock_response = MagicMock()
        mock_response.body.desktop_id = ["ecd-test-123"]

        with patch.object(operator, "_create_ecd_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client.create_desktops_with_options.return_value = mock_response
            mock_client_factory.return_value = mock_client

            result = await operator.submit(config, {"user_id": "test-user"})

        assert result["sandbox_id"] == "ecd-test-123"
        assert result["state"] == State.PENDING
        assert result["image"] == "python:3.11"
        assert result["user_id"] == "test-user"

    @pytest.mark.asyncio
    async def test_submit_no_matching_pool(self, operator):
        """Test submit raises error when no matching pool."""
        config = DockerDeploymentConfig(
            image="ubuntu:22.04",  # No pool for this image
            cpus=2,
            memory="4g",
            container_name="test-sandbox",
        )

        with pytest.raises(ValueError, match="No matching pool"):
            await operator.submit(config)


class TestWuyingOperatorGetStatus:
    """Tests for WuyingOperator.get_status method."""

    @pytest.fixture
    def operator(self):
        """Create a WuyingOperator instance."""
        config = WuyingConfig(region_id="cn-hangzhou")
        return WuyingOperator(wuying_config=config)

    @pytest.mark.asyncio
    async def test_get_status_running(self, operator):
        """Test get_status returns RUNNING state."""
        mock_response = MagicMock()
        mock_desktop = MagicMock()
        mock_desktop.desktop_status = "Running"
        mock_desktop.desktop_name = "test-desktop"
        mock_desktop.network_interface_ip = "192.168.1.100"
        mock_response.body.desktops = [mock_desktop]

        with patch.object(operator, "_get_desktop_id", return_value="ecd-test-123"):
            with patch.object(operator, "_create_ecd_client") as mock_client_factory:
                mock_client = MagicMock()
                mock_client.describe_desktops_with_options.return_value = mock_response
                mock_client_factory.return_value = mock_client

                with patch.object(operator, "_check_rocklet_alive", return_value=True):
                    result = await operator.get_status("sandbox-123")

        assert result["sandbox_id"] == "sandbox-123"
        assert result["state"] == State.RUNNING
        assert result["host_ip"] == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_get_status_not_found(self, operator):
        """Test get_status returns PENDING when desktop not found."""
        mock_response = MagicMock()
        mock_response.body.desktops = []

        with patch.object(operator, "_get_desktop_id", return_value="ecd-test-123"):
            with patch.object(operator, "_create_ecd_client") as mock_client_factory:
                mock_client = MagicMock()
                mock_client.describe_desktops_with_options.return_value = mock_response
                mock_client_factory.return_value = mock_client

                result = await operator.get_status("sandbox-123")

        assert result["sandbox_id"] == "sandbox-123"
        assert result["state"] == State.PENDING

    @pytest.mark.asyncio
    async def test_get_status_returns_phases_dict(self, operator):
        """Test get_status returns phases dict consistent with other operators."""
        mock_response = MagicMock()
        mock_desktop = MagicMock()
        mock_desktop.desktop_status = "Running"
        mock_desktop.desktop_name = "test-desktop"
        mock_desktop.network_interface_ip = "192.168.1.100"
        mock_response.body.desktops = [mock_desktop]

        with patch.object(operator, "_get_desktop_id", return_value="ecd-test-123"):
            with patch.object(operator, "_create_ecd_client") as mock_client_factory:
                mock_client = MagicMock()
                mock_client.describe_desktops_with_options.return_value = mock_response
                mock_client_factory.return_value = mock_client

                with patch.object(operator, "_check_rocklet_alive", return_value=True):
                    result = await operator.get_status("sandbox-123")

        # phases should be a dict with 'image_pull' and 'docker_run' keys
        assert "phases" in result
        assert isinstance(result["phases"], dict)
        assert "image_pull" in result["phases"]
        assert "docker_run" in result["phases"]

    @pytest.mark.asyncio
    async def test_get_status_phases_running_when_alive(self, operator):
        """Test phases show success when desktop running and rocklet alive."""
        mock_response = MagicMock()
        mock_desktop = MagicMock()
        mock_desktop.desktop_status = "Running"
        mock_desktop.desktop_name = "test-desktop"
        mock_desktop.network_interface_ip = "192.168.1.100"
        mock_response.body.desktops = [mock_desktop]

        with patch.object(operator, "_get_desktop_id", return_value="ecd-test-123"):
            with patch.object(operator, "_create_ecd_client") as mock_client_factory:
                mock_client = MagicMock()
                mock_client.describe_desktops_with_options.return_value = mock_response
                mock_client_factory.return_value = mock_client

                with patch.object(operator, "_check_rocklet_alive", return_value=True):
                    result = await operator.get_status("sandbox-123")

        # Both phases should be success
        assert result["phases"]["image_pull"].status.value == "success"
        assert result["phases"]["docker_run"].status.value == "success"

    @pytest.mark.asyncio
    async def test_get_status_phases_pending_when_not_running(self, operator):
        """Test phases show waiting when desktop is pending."""
        mock_response = MagicMock()
        mock_desktop = MagicMock()
        mock_desktop.desktop_status = "Pending"
        mock_desktop.desktop_name = "test-desktop"
        mock_desktop.network_interface_ip = ""
        mock_response.body.desktops = [mock_desktop]

        with patch.object(operator, "_get_desktop_id", return_value="ecd-test-123"):
            with patch.object(operator, "_create_ecd_client") as mock_client_factory:
                mock_client = MagicMock()
                mock_client.describe_desktops_with_options.return_value = mock_response
                mock_client_factory.return_value = mock_client

                result = await operator.get_status("sandbox-123")

        # Both phases should be waiting
        assert result["phases"]["image_pull"].status.value == "waiting"
        assert result["phases"]["docker_run"].status.value == "waiting"


class TestWuyingOperatorStop:
    """Tests for WuyingOperator.stop method."""

    @pytest.fixture
    def operator(self):
        """Create a WuyingOperator instance."""
        config = WuyingConfig(region_id="cn-hangzhou")
        return WuyingOperator(wuying_config=config)

    @pytest.mark.asyncio
    async def test_stop_success(self, operator):
        """Test stop deletes desktop successfully."""
        mock_response = MagicMock()

        with patch.object(operator, "_create_ecd_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client.delete_desktops_with_options.return_value = mock_response
            mock_client_factory.return_value = mock_client

            result = await operator.stop("ecd-test-123")

        assert result is True

    @pytest.mark.asyncio
    async def test_stop_not_found(self, operator):
        """Test stop returns True when desktop not found."""
        with patch.object(operator, "_create_ecd_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client.delete_desktops_with_options.side_effect = Exception("Desktop not found")
            mock_client_factory.return_value = mock_client

            result = await operator.stop("ecd-not-exist")

        assert result is True
