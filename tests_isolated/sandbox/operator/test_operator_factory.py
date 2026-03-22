"""Tests for OperatorFactory Wuying support.

Note: This test file uses sys.modules mocking to avoid Ray, Kubernetes, and
ECD SDK dependency issues in Python 3.13 environment where these packages
may not be installed.
"""
import sys
from unittest.mock import MagicMock

# Mock ray module before importing factory (Ray doesn't support Python 3.13+)
sys.modules["ray"] = MagicMock()
# Mock kubernetes modules (may not be installed in dev environment)
sys.modules["kubernetes"] = MagicMock()
sys.modules["kubernetes.client"] = MagicMock()
sys.modules["kubernetes.config"] = MagicMock()
# Mock ECD SDK (optional dependency)
sys.modules["alibabacloud_ecd20200930"] = MagicMock()
sys.modules["alibabacloud_ecd20200930.client"] = MagicMock()
sys.modules["alibabacloud_ecd20200930.models"] = MagicMock()
sys.modules["alibabacloud_tea_openapi"] = MagicMock()
sys.modules["alibabacloud_tea_openapi.models"] = MagicMock()
sys.modules["alibabacloud_tea_util"] = MagicMock()
sys.modules["alibabacloud_tea_util.models"] = MagicMock()

import pytest

from rock.config import RuntimeConfig, WuyingConfig
from rock.sandbox.operator.factory import OperatorFactory, OperatorContext
from rock.sandbox.operator.wuying import WuyingOperator


class TestOperatorFactoryWuyingSupport:
    """Tests for OperatorFactory creating WuyingOperator."""

    @pytest.fixture
    def runtime_config(self):
        """Create a RuntimeConfig with wuying operator type."""
        return RuntimeConfig(operator_type="wuying")

    @pytest.fixture
    def wuying_config(self):
        """Create a WuyingConfig for testing."""
        return WuyingConfig(
            region_id="cn-hangzhou",
            office_site_id="cn-hangzhou+dir-test",
            policy_group_id="pg-test",
        )

    def test_create_wuying_operator(self, runtime_config, wuying_config):
        """Test OperatorFactory creates WuyingOperator."""
        context = OperatorContext(
            runtime_config=runtime_config,
            wuying_config=wuying_config,
        )

        operator = OperatorFactory.create_operator(context)

        assert isinstance(operator, WuyingOperator)

    def test_create_wuying_operator_missing_config(self, runtime_config):
        """Test OperatorFactory raises error when wuying_config is missing."""
        context = OperatorContext(
            runtime_config=runtime_config,
            wuying_config=None,
        )

        with pytest.raises(ValueError, match="WuyingConfig is required"):
            OperatorFactory.create_operator(context)

    def test_operator_factory_supports_wuying_type(self):
        """Test that 'wuying' is a supported operator type."""
        from rock.sandbox.operator.factory import OperatorFactory, OperatorContext

        # This test verifies the error message includes 'wuying'
        runtime_config = RuntimeConfig(operator_type="unknown")
        context = OperatorContext(runtime_config=runtime_config)

        with pytest.raises(ValueError) as exc_info:
            OperatorFactory.create_operator(context)

        # Error message should list supported types including wuying
        error_msg = str(exc_info.value)
        assert "wuying" in error_msg.lower()
