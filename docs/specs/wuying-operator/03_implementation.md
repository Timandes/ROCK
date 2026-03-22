# Wuying Operator 实施细节

## 概述

本文档定义 Wuying Operator 的具体实施步骤，包括文件变更列表、核心逻辑和执行计划。

---

## 1. File Changes

### 1.1 新增文件

| 文件路径 | 说明 |
|----------|------|
| `rock/config.py` | 新增 `WuyingPoolConfig`、`WuyingConfig` 数据类 |
| `rock/sandbox/operator/wuying.py` | 新增 `WuyingOperator` 类 |
| `rock/deployments/wuying.py` | 新增 `WuyingDeployment` 类 |
| `rock/deployments/config.py` | 新增 `WuyingDeploymentConfig` 类 |

### 1.2 修改文件

| 文件路径 | 说明 |
|----------|------|
| `rock/sandbox/operator/factory.py` | 添加 `wuying` operator 类型的工厂方法 |
| `rock/deployments/config.py` | 新增 `WuyingDeploymentConfig` 类型 |
| `rock/config.py` | `RockConfig` 新增 `wuying: WuyingConfig` 字段 |

---

## 2. Core Logic (伪代码)

### 2.1 WuyingOperator.submit()

```python
async def submit(self, config: DockerDeploymentConfig, user_info: dict = {}) -> SandboxInfo:
    # Step 1: 选择 Pool
    pool_name = self._select_pool(config)
    if not pool_name:
        raise ValueError(f"No matching pool for image={config.image}, cpus={config.cpus}, memory={config.memory}")
    
    pool_config = self._wuying_config.pools[pool_name]
    
    # Step 2: 调用阿里云 API 创建云电脑
    client = self._create_ecd_client()
    request = CreateDesktopsRequest(
        region_id=self._wuying_config.region_id,
        bundle_id=pool_config.bundle_id,
        desktop_name=config.container_name,  # 使用 sandbox_id 作为名称
        office_site_id=self._wuying_config.office_site_id,
        policy_group_id=self._wuying_config.policy_group_id,
    )
    
    response = await client.create_desktops_with_options_async(request, RuntimeOptions())
    desktop_id = response.body.DesktopId[0]  # 获取创建的实例 ID
    
    # Step 3: 返回 PENDING 状态
    return SandboxInfo(
        sandbox_id=desktop_id,
        host_ip="",  # 暂无 IP
        host_name=desktop_id,
        state=State.PENDING,
        image=config.image,
        cpus=config.cpus,
        memory=config.memory,
        port_mapping=pool_config.ports,
        user_id=user_info.get("user_id", "default"),
        experiment_id=user_info.get("experiment_id", "default"),
        namespace=user_info.get("namespace", "default"),
    )
```

### 2.2 WuyingOperator.get_status()

```python
async def get_status(self, sandbox_id: str) -> SandboxInfo:
    # Step 1: 调用阿里云 API 查询实例状态
    client = self._create_ecd_client()
    request = DescribeDesktopsRequest(
        region_id=self._wuying_config.region_id,
        desktop_id=[sandbox_id],
    )
    
    response = await client.describe_desktops_with_options_async(request, RuntimeOptions())
    
    if not response.body.Desktops:
        return SandboxInfo(sandbox_id=sandbox_id, state=State.STOPPED)
    
    desktop = response.body.Desktops[0]
    
    # Step 2: 映射状态
    state = self._map_desktop_status(desktop.DesktopStatus)
    
    # Step 3: 获取 IP
    host_ip = desktop.IpAddresses[0] if desktop.IpAddresses else ""
    
    # Step 4: 如果有 IP，检查 rocklet 是否存活
    if host_ip and state == State.RUNNING:
        is_alive = await self._check_rocklet_alive(host_ip)
        if not is_alive:
            state = State.PENDING  # 实例启动但 rocklet 未就绪
    
    return SandboxInfo(
        sandbox_id=sandbox_id,
        host_ip=host_ip,
        host_name=desktop.DesktopName,
        state=state,
        ...
    )
```

### 2.3 WuyingOperator.stop()

```python
async def stop(self, sandbox_id: str) -> bool:
    client = self._create_ecd_client()
    request = DeleteDesktopsRequest(
        region_id=self._wuying_config.region_id,
        desktop_id=[sandbox_id],
    )
    
    try:
        await client.delete_desktops_with_options_async(request, RuntimeOptions())
        return True
    except Exception as e:
        if "not found" in str(e).lower():
            return True  # 已删除
        raise
```

### 2.4 WuyingDeployment.start()

```python
async def start(self):
    # Step 1: SSH 凭据已在 __init__ 中通过 _get_ssh_credentials() 获取
    # 优先级：环境变量 > 配置文件 > 默认值 (user/password)
    
    # Step 2: 建立 SSH 连接（带重试）
    for attempt in range(3):
        try:
            self._ssh_client = paramiko.SSHClient()
            self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self._ssh_client.connect(
                hostname=self._host_ip,
                port=self._ssh_port,
                username=self._username,
                password=self._password,
                timeout=30,
            )
            break
        except Exception as e:
            if attempt == 2:
                raise
            await asyncio.sleep(10)  # 等待后重试
    
    # Step 3: 启动 rocklet
    stdin, stdout, stderr = self._ssh_client.exec_command(
        f"rocklet start --port {self._proxy_port}"
    )
    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0:
        raise CommandFailedError(f"rocklet start failed: {stderr.read().decode()}")
    
    # Step 4: 创建 RemoteSandboxRuntime
    self._runtime = RemoteSandboxRuntime.from_config(
        RemoteSandboxRuntimeConfig(
            host=f"http://{self._host_ip}",
            port=self._proxy_port,
        )
    )
    
    # Step 5: 等待 rocklet 就绪
    await wait_until_alive(self.is_alive, timeout=30)
```

### 2.5 _get_ssh_credentials() 方法

```python
def _get_ssh_credentials(self, wuying_config: WuyingConfig) -> tuple[str, str]:
    """
    获取 SSH 凭据，按优先级合并
    
    优先级: 环境变量 > 配置文件 > 默认值
    
    Args:
        wuying_config: Wuying 配置
        
    Returns:
        (username, password)
    """
    import os
    
    # 默认值
    username = "user"
    password = "password"
    
    # 配置文件覆盖
    if wuying_config.ssh_username:
        username = wuying_config.ssh_username
    if wuying_config.ssh_password:
        password = wuying_config.ssh_password
    
    # 环境变量覆盖（最高优先级）
    env_username = os.getenv("ROCK_WUYING_SSH_USERNAME")
    if env_username:
        username = env_username
    
    env_password = os.getenv("ROCK_WUYING_SSH_PASSWORD")
    if env_password:
        password = env_password
    
    return username, password
```
    
    # Step 4: 创建 RemoteSandboxRuntime
    self._runtime = RemoteSandboxRuntime.from_config(
        RemoteSandboxRuntimeConfig(
            host=f"http://{self._host_ip}",
            port=self._proxy_port,
        )
    )
    
    # Step 5: 等待 rocklet 就绪
    await wait_until_alive(self.is_alive, timeout=30)
```

### 2.5 Pool 选择逻辑

```python
def _select_pool(self, config: DockerDeploymentConfig) -> str | None:
    """复用 K8s 的 ResourceMatchingPoolSelector 逻辑"""
    from rock.sandbox.operator.k8s.provider import ResourceMatchingPoolSelector
    
    # 转换 WuyingPoolConfig 为兼容格式
    compatible_pools = {
        name: PoolConfig(image=p.image, cpus=p.cpus, memory=p.memory)
        for name, p in self._wuying_config.pools.items()
    }
    
    selector = ResourceMatchingPoolSelector()
    return selector.select_pool(config, compatible_pools)
```

---

## 3. Execution Plan

### Step 1: 配置类定义

**文件**: `rock/config.py`

1. 新增 `WuyingPoolConfig` dataclass
2. 新增 `WuyingConfig` dataclass
3. 在 `RockConfig` 中添加 `wuying: WuyingConfig` 字段

### Step 2: WuyingDeployment 实现

**文件**: `rock/deployments/wuying.py`

1. 实现 `WuyingDeployment` 类
2. 实现 SSH 连接逻辑
3. 实现 rocklet 启动逻辑
4. 实现 `is_alive()` 和 `stop()` 方法

**文件**: `rock/deployments/config.py`

1. 新增 `WuyingDeploymentConfig` 类
2. 实现 `get_deployment()` 方法返回 `WuyingDeployment`

### Step 3: WuyingOperator 实现

**文件**: `rock/sandbox/operator/wuying.py`

1. 实现 `WuyingOperator` 类
2. 实现 `submit()` 方法
3. 实现 `get_status()` 方法
4. 实现 `stop()` 方法
5. 实现 Pool 选择逻辑

### Step 4: 工厂方法扩展

**文件**: `rock/sandbox/operator/factory.py`

1. 在 `OperatorFactory.create_operator()` 中添加 `wuying` 分支

### Step 5: 单元测试

**文件**: `tests/unit/sandbox/operator/test_wuying_operator.py`

1. 测试 Pool 选择逻辑
2. 测试状态映射逻辑
3. Mock API 调用进行测试

**文件**: `tests/unit/deployments/test_wuying_deployment.py`

1. 测试 SSH 连接逻辑
2. 测试 rocklet 启动逻辑

---

## 4. Dependencies

### 4.1 新增依赖

```
# pyproject.toml
dependencies = [
    ...
    "alibabacloud-ecd20200930>=1.0.0",  # 阿里云 ECD SDK
    "paramiko>=3.0.0",                   # SSH 连接
]
```

### 4.2 条件依赖

考虑到 Wuying 是可选后端，建议使用 `extras` 或条件导入：

```python
# rock/sandbox/operator/wuying.py
try:
    from alibabacloud_ecd20200930.client import Client as ECDClient
    ECD_AVAILABLE = True
except ImportError:
    ECD_AVAILABLE = False

class WuyingOperator(AbstractOperator):
    def __init__(self, ...):
        if not ECD_AVAILABLE:
            raise ImportError("alibabacloud-ecd20200930 is required for WuyingOperator")
```

---

## 5. Rollback & Compatibility

### 5.1 回滚方案

1. **配置回滚**: 删除 `wuying` 配置节，系统将不加载 WuyingOperator
2. **代码回滚**: Wuying 相关代码完全独立，删除新增文件即可
3. **实例清理**: 提供脚本手动清理残留的云电脑实例

### 5.2 兼容性考虑

1. **API 兼容**: 复用 `DockerDeploymentConfig`，无需修改 `/start_async` 接口
2. **状态兼容**: 使用相同的 `SandboxInfo` 结构
3. **配置兼容**: 新增配置节不影响现有配置

---

## 6. Mock 数据

### 6.1 CreateDesktops Response

```json
{
  "RequestId": "xxx",
  "DesktopId": ["ecd-xxx"],
  "OrderId": "xxx"
}
```

### 6.2 DescribeDesktops Response

```json
{
  "RequestId": "xxx",
  "TotalCount": 1,
  "Desktops": [
    {
      "DesktopId": "ecd-xxx",
      "DesktopName": "api-create",
      "DesktopStatus": "RUNNING",
      "IpAddresses": ["192.168.1.100"],
      "BundleId": "b-xxx"
    }
  ]
}
```

### 6.3 DescribeUsersPassword Response

```json
{
  "RequestId": "xxx",
  "UsersPassword": [
    {
      "EndUserId": "admin",
      "Password": "xxx"
    }
  ]
}
```
