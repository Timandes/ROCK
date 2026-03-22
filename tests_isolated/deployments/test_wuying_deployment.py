"""Tests for WuyingDeployment."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from rock.deployments.config import WuyingDeploymentConfig
from rock.deployments.wuying import WuyingDeployment
from rock.sandbox.remote_sandbox import RemoteSandboxRuntime


class TestWuyingDeploymentConfig:
    """Tests for WuyingDeploymentConfig.get_deployment()."""

    def test_get_deployment_returns_wuying_deployment(self):
        """Test get_deployment returns WuyingDeployment instance."""
        config = WuyingDeploymentConfig(
            desktop_id="ecd-test-123",
            host_ip="192.168.1.100",
        )

        deployment = config.get_deployment()

        assert isinstance(deployment, WuyingDeployment)


class TestWuyingDeploymentProperties:
    """Tests for WuyingDeployment properties."""

    @pytest.fixture
    def deployment(self):
        """Create a WuyingDeployment instance."""
        config = WuyingDeploymentConfig(
            desktop_id="ecd-test-123",
            host_ip="192.168.1.100",
            ssh_port=22,
            ssh_username="user",
            ssh_password="password",
            proxy_port=8000,
        )
        return WuyingDeployment.from_config(config)

    def test_deployment_stores_config(self, deployment):
        """Test deployment stores configuration."""
        assert deployment._config.desktop_id == "ecd-test-123"
        assert deployment._config.host_ip == "192.168.1.100"

    def test_runtime_raises_before_start(self, deployment):
        """Test runtime property raises before start."""
        from rock.rocklet.exceptions import DeploymentNotStartedError

        with pytest.raises(DeploymentNotStartedError):
            _ = deployment.runtime


class TestWuyingDeploymentLifecycle:
    """Tests for WuyingDeployment lifecycle methods."""

    @pytest.fixture
    def deployment(self):
        """Create a WuyingDeployment instance."""
        config = WuyingDeploymentConfig(
            desktop_id="ecd-test-123",
            host_ip="192.168.1.100",
            ssh_port=22,
            ssh_username="user",
            ssh_password="password",
            proxy_port=8000,
        )
        return WuyingDeployment.from_config(config)

    @pytest.mark.asyncio
    async def test_start_creates_runtime(self, deployment):
        """Test start() creates RemoteSandboxRuntime."""
        with patch.object(deployment, '_ssh_connect', new_callable=AsyncMock) as mock_ssh:
            with patch.object(deployment, '_start_rocklet', new_callable=AsyncMock) as mock_rocklet:
                await deployment.start()

                mock_ssh.assert_called_once()
                mock_rocklet.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_closes_runtime(self, deployment):
        """Test stop() closes runtime and SSH connection."""
        # Mock the runtime with close method
        mock_runtime = MagicMock(spec=RemoteSandboxRuntime)
        mock_runtime.close = MagicMock()
        deployment._runtime = mock_runtime

        with patch.object(deployment, '_ssh_disconnect', new_callable=AsyncMock) as mock_disconnect:
            await deployment.stop()

            mock_runtime.close.assert_called_once()
            mock_disconnect.assert_called_once()
            assert deployment._runtime is None

    @pytest.mark.asyncio
    async def test_is_alive_after_start(self, deployment):
        """Test is_alive() returns correct status after start."""
        with patch.object(deployment, '_ssh_connect', new_callable=AsyncMock):
            with patch.object(deployment, '_start_rocklet', new_callable=AsyncMock):
                await deployment.start()

        # Mock the runtime's is_alive method
        from rock.actions.sandbox.response import IsAliveResponse
        deployment._runtime.is_alive = AsyncMock(return_value=IsAliveResponse(is_alive=True))

        result = await deployment.is_alive()

        assert result.is_alive is True


class TestWuyingDeploymentSSHMethods:
    """Tests for WuyingDeployment SSH methods."""

    @pytest.fixture
    def deployment(self):
        """Create a WuyingDeployment instance."""
        config = WuyingDeploymentConfig(
            desktop_id="ecd-test-123",
            host_ip="192.168.1.100",
            ssh_port=2222,
            ssh_username="admin",
            ssh_password="secret",
            proxy_port=9000,
        )
        return WuyingDeployment.from_config(config)

    def test_build_rocklet_start_command(self, deployment):
        """Test _build_rocklet_start_command generates correct command."""
        cmd = deployment._build_rocklet_start_command()

        assert "rocklet" in cmd
        assert "9000" in cmd  # proxy_port

    def test_build_rocklet_start_command_default_port(self):
        """Test _build_rocklet_start_command with default port."""
        config = WuyingDeploymentConfig(
            desktop_id="ecd-test-123",
            host_ip="192.168.1.100",
        )
        deployment = WuyingDeployment.from_config(config)

        cmd = deployment._build_rocklet_start_command()

        assert "8000" in cmd  # default proxy_port
