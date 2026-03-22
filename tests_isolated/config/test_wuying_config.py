"""Unit tests for WuyingConfig and WuyingPoolConfig."""
import os
import pytest

from rock.config import WuyingConfig, WuyingPoolConfig


class TestWuyingPoolConfig:
    """Tests for WuyingPoolConfig dataclass."""

    def test_wuying_pool_config_basic_fields(self):
        """Test WuyingPoolConfig with required fields."""
        pool = WuyingPoolConfig(
            image="python:3.11",
            bundle_id="b-test-bundle",
            cpus=2,
            memory="4g",
        )

        assert pool.image == "python:3.11"
        assert pool.bundle_id == "b-test-bundle"
        assert pool.cpus == 2
        assert pool.memory == "4g"

    def test_wuying_pool_config_default_ports(self):
        """Test WuyingPoolConfig has default port configuration."""
        pool = WuyingPoolConfig(
            image="python:3.11",
            bundle_id="b-test-bundle",
            cpus=2,
            memory="4g",
        )

        assert pool.ports == {"proxy": 8000, "server": 8080, "ssh": 22}

    def test_wuying_pool_config_custom_ports(self):
        """Test WuyingPoolConfig with custom ports."""
        pool = WuyingPoolConfig(
            image="python:3.11",
            bundle_id="b-test-bundle",
            cpus=2,
            memory="4g",
            ports={"proxy": 9000, "server": 9080, "ssh": 2222},
        )

        assert pool.ports == {"proxy": 9000, "server": 9080, "ssh": 2222}

    def test_wuying_pool_config_optional_desktop_type(self):
        """Test WuyingPoolConfig with optional desktop_type."""
        pool = WuyingPoolConfig(
            image="python:3.11",
            bundle_id="b-test-bundle",
            cpus=2,
            memory="4g",
            desktop_type="eds.enterprise_office.2c4g",
        )

        assert pool.desktop_type == "eds.enterprise_office.2c4g"


class TestWuyingConfig:
    """Tests for WuyingConfig dataclass."""

    def test_wuying_config_default_values(self):
        """Test WuyingConfig with default values."""
        config = WuyingConfig()

        assert config.region_id == "cn-hangzhou"
        assert config.endpoint == "ecd.cn-hangzhou.aliyuncs.com"
        assert config.ssh_username == "user"
        assert config.ssh_password == "password"
        assert config.pools == {}

    def test_wuying_config_with_pools(self):
        """Test WuyingConfig with pool configurations."""
        pools = {
            "pool-python-2c4g": WuyingPoolConfig(
                image="python:3.11",
                bundle_id="b-test-bundle",
                cpus=2,
                memory="4g",
            )
        }

        config = WuyingConfig(
            region_id="cn-shanghai",
            office_site_id="cn-shanghai+dir-xxx",
            policy_group_id="pg-xxx",
            pools=pools,
        )

        assert config.region_id == "cn-shanghai"
        assert config.office_site_id == "cn-shanghai+dir-xxx"
        assert config.policy_group_id == "pg-xxx"
        assert "pool-python-2c4g" in config.pools

    def test_wuying_config_ssh_credentials_from_config(self):
        """Test SSH credentials can be set via config."""
        config = WuyingConfig(
            ssh_username="admin",
            ssh_password="secret",
        )

        assert config.ssh_username == "admin"
        assert config.ssh_password == "secret"


class TestWuyingConfigSSHCredentialsPriority:
    """Tests for SSH credentials priority: env > config > default."""

    def test_ssh_credentials_default_values(self):
        """Test SSH credentials use default values when no config or env."""
        # Clear env vars if set
        os.environ.pop("ROCK_WUYING_SSH_USERNAME", None)
        os.environ.pop("ROCK_WUYING_SSH_PASSWORD", None)

        config = WuyingConfig()
        username, password = config.get_ssh_credentials()

        assert username == "user"
        assert password == "password"

    def test_ssh_credentials_from_config(self):
        """Test SSH credentials from config override defaults."""
        os.environ.pop("ROCK_WUYING_SSH_USERNAME", None)
        os.environ.pop("ROCK_WUYING_SSH_PASSWORD", None)

        config = WuyingConfig(
            ssh_username="config_user",
            ssh_password="config_pass",
        )
        username, password = config.get_ssh_credentials()

        assert username == "config_user"
        assert password == "config_pass"

    def test_ssh_credentials_from_env_overrides_config(self):
        """Test SSH credentials from env override config values."""
        os.environ["ROCK_WUYING_SSH_USERNAME"] = "env_user"
        os.environ["ROCK_WUYING_SSH_PASSWORD"] = "env_pass"

        try:
            config = WuyingConfig(
                ssh_username="config_user",
                ssh_password="config_pass",
            )
            username, password = config.get_ssh_credentials()

            assert username == "env_user"
            assert password == "env_pass"
        finally:
            os.environ.pop("ROCK_WUYING_SSH_USERNAME", None)
            os.environ.pop("ROCK_WUYING_SSH_PASSWORD", None)
