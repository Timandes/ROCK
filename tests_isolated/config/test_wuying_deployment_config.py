"""Tests for WuyingDeploymentConfig."""
import pytest

from rock.deployments.config import WuyingDeploymentConfig, get_deployment
from rock.deployments.wuying import WuyingDeployment


class TestWuyingDeploymentConfig:
    """Tests for WuyingDeploymentConfig."""

    def test_wuying_deployment_config_type(self):
        """Test WuyingDeploymentConfig has correct type."""
        config = WuyingDeploymentConfig(
            desktop_id="ecd-test-123",
            host_ip="192.168.1.100",
        )

        assert config.type == "wuying"

    def test_wuying_deployment_config_required_fields(self):
        """Test WuyingDeploymentConfig with required fields."""
        config = WuyingDeploymentConfig(
            desktop_id="ecd-test-123",
            host_ip="192.168.1.100",
        )

        assert config.desktop_id == "ecd-test-123"
        assert config.host_ip == "192.168.1.100"

    def test_wuying_deployment_config_optional_fields(self):
        """Test WuyingDeploymentConfig with optional fields."""
        config = WuyingDeploymentConfig(
            desktop_id="ecd-test-123",
            host_ip="192.168.1.100",
            ssh_port=2222,
            ssh_username="admin",
            ssh_password="secret",
            proxy_port=9000,
        )

        assert config.ssh_port == 2222
        assert config.ssh_username == "admin"
        assert config.ssh_password == "secret"
        assert config.proxy_port == 9000

    def test_wuying_deployment_config_default_values(self):
        """Test WuyingDeploymentConfig default values."""
        config = WuyingDeploymentConfig(
            desktop_id="ecd-test-123",
            host_ip="192.168.1.100",
        )

        assert config.ssh_port == 22
        assert config.ssh_username == "user"
        assert config.ssh_password == "password"
        assert config.proxy_port == 8000

    def test_wuying_deployment_config_get_deployment(self):
        """Test WuyingDeploymentConfig.get_deployment returns WuyingDeployment."""
        config = WuyingDeploymentConfig(
            desktop_id="ecd-test",
            host_ip="192.168.1.100",
        )
        deployment = config.get_deployment()
        assert isinstance(deployment, WuyingDeployment)

    def test_get_deployment_factory_function(self):
        """Test get_deployment factory function with WuyingDeploymentConfig."""
        config = WuyingDeploymentConfig(
            desktop_id="ecd-test",
            host_ip="192.168.1.100",
        )
        deployment = get_deployment(config)
        assert isinstance(deployment, WuyingDeployment)

