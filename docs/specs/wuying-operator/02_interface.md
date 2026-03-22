# Wuying Operator 接口契约

## 概述

本文档定义 Wuying Operator 的内部接口契约，包括配置类、Operator 接口、Deployment 接口和状态模型。

---

## 1. WuyingConfig 配置类

### 1.1 数据结构

```python
@dataclass
class WuyingPoolConfig:
    """Wuying Pool 配置"""
    image: str                    # 用户请求的镜像名，如 "python:3.11"
    bundle_id: str                # 对应的云电脑 bundle ID
    cpus: float                   # CPU 核数，用于匹配
    memory: str                   # 内存大小，如 "4g"
    desktop_type: str | None = None  # 可选：云电脑规格类型
    ports: dict[str, int] = field(default_factory=lambda: {"proxy": 8000, "server": 8080, "ssh": 22})

@dataclass  
class WuyingConfig:
    """Wuying Operator 配置"""
    region_id: str = "cn-hangzhou"
    endpoint: str = "ecd.cn-hangzhou.aliyuncs.com"
    office_site_id: str = ""      # 办公网络 ID
    policy_group_id: str = ""     # 策略组 ID
    pools: dict[str, WuyingPoolConfig] = field(default_factory=dict)
    
    # 认证配置（从环境变量读取）
    access_key_id: str | None = None
    access_key_secret: str | None = None
```

### 1.2 配置示例

```yaml
# rock-conf/config.yaml
wuying:
  region_id: "cn-hangzhou"
  endpoint: "ecd.cn-hangzhou.aliyuncs.com"
  office_site_id: "cn-hangzhou+dir-5478102986"
  policy_group_id: "pg-0bbay4wbhh3627ur7"
  
  pools:
    pool-python-2c4g:
      image: "python:3.11"
      bundle_id: "b-g5oalp0gbl03itetf"
      cpus: 2
      memory: "4g"
      desktop_type: "eds.enterprise_office.2c4g"
      
    pool-python-4c8g:
      image: "python:3.11"
      bundle_id: "b-another-bundle-id"
      cpus: 4
      memory: "8g"
      desktop_type: "eds.enterprise_office.4c8g"
      
    pool-node-2c4g:
      image: "node:18"
      bundle_id: "b-node-bundle-id"
      cpus: 2
      memory: "4g"
```

---

## 2. WuyingOperator 接口

### 2.1 类定义

```python
class WuyingOperator(AbstractOperator):
    """Wuying 云电脑 Operator"""
    
    def __init__(self, wuying_config: WuyingConfig, redis_provider=None):
        """
        初始化 Wuying Operator
        
        Args:
            wuying_config: Wuying 配置
            redis_provider: 可选的 Redis 提供者，用于状态缓存
        """
        ...
```

### 2.2 submit() 接口

```python
async def submit(self, config: DockerDeploymentConfig, user_info: dict = {}) -> SandboxInfo:
    """
    创建云电脑实例
    
    流程:
    1. 根据 config.image 和 config.cpus/memory 选择 Pool
    2. 调用 CreateDesktops API 创建实例
    3. 返回 PENDING 状态的 SandboxInfo
    
    Args:
        config: Docker 部署配置（复用现有结构）
        user_info: 用户信息（user_id, experiment_id, namespace）
        
    Returns:
        SandboxInfo:
        {
            "sandbox_id": "ecd-xxx",          # 云电脑实例 ID
            "host_ip": "",                     # 暂无，等待实例启动
            "host_name": "ecd-xxx",
            "state": "PENDING",
            "image": "python:3.11",
            "cpus": 2,
            "memory": "4g",
            "port_mapping": {},
            "user_id": "xxx",
            "experiment_id": "xxx",
            "namespace": "xxx"
        }
        
    Raises:
        ValueError: 没有匹配的 Pool
        Exception: API 调用失败
    """
```

### 2.3 get_status() 接口

```python
async def get_status(self, sandbox_id: str) -> SandboxInfo:
    """
    查询云电脑状态
    
    流程:
    1. 调用 DescribeDesktops API 获取实例状态
    2. 如果实例已启动，获取 IP 地址
    3. 如果有 IP，尝试连接 rocklet 检查 is_alive
    4. 返回 SandboxInfo
    
    Args:
        sandbox_id: 云电脑实例 ID
        
    Returns:
        SandboxInfo:
        {
            "sandbox_id": "ecd-xxx",
            "host_ip": "192.168.x.x",        # 实例启动后返回
            "host_name": "ecd-xxx",
            "state": "RUNNING" | "PENDING" | "STOPPED",
            "port_mapping": {
                8000: 8000,  # proxy
                8080: 8080,  # server
                22: 22       # ssh
            },
            ...
        }
    """
```

### 2.4 stop() 接口

```python
async def stop(self, sandbox_id: str) -> bool:
    """
    销毁云电脑实例
    
    流程:
    1. 调用 DeleteDesktops API 销毁实例
    2. 清理本地状态
    
    Args:
        sandbox_id: 云电脑实例 ID
        
    Returns:
        True: 销毁成功
        False: 销毁失败（实例不存在也返回 True）
    """
```

---

## 3. WuyingDeployment 接口

### 3.1 类定义

```python
class WuyingDeployment(AbstractDeployment):
    """Wuying 云电脑部署"""
    
    def __init__(
        self,
        desktop_id: str,
        host_ip: str,
        ssh_port: int = 22,
        username: str = "root",
        password: str | None = None,
        **kwargs
    ):
        """
        初始化 Wuying 部署
        
        Args:
            desktop_id: 云电脑实例 ID
            host_ip: 云电脑 IP 地址
            ssh_port: SSH 端口
            username: SSH 用户名
            password: SSH 密码（可选，可后续通过 API 获取）
        """
        ...
```

### 3.2 start() 接口

```python
async def start(self):
    """
    启动部署
    
    流程:
    1. 如果没有密码，调用 DescribeUsersPassword API 获取
    2. 建立 SSH 连接（使用 paramiko）
    3. 执行启动命令: rocklet start --port 8000
    4. 创建 RemoteSandboxRuntime 连接 rocklet
    5. 等待 rocklet 就绪
    
    Raises:
        SSHTimedOutError: SSH 连接超时
        CommandFailedError: 启动命令执行失败
    """
```

### 3.3 stop() 接口

```python
async def stop(self):
    """
    停止部署
    
    流程:
    1. 通过 rocklet API 发送停止信号
    2. 关闭 SSH 连接
    """
```

### 3.4 runtime 属性

```python
@property
def runtime(self) -> RemoteSandboxRuntime:
    """
    返回 RemoteSandboxRuntime 实例
    
    用于执行命令、读写文件等操作
    """
```

---

## 4. 状态模型

### 4.1 SandboxInfo 状态映射

| 云电脑状态 (DesktopStatus) | ROCK 状态 | 说明 |
|---------------------------|-----------|------|
| CREATING | PENDING | 实例创建中 |
| STARTING | PENDING | 实例启动中 |
| RUNNING | RUNNING | 实例运行中（需 rocklet is_alive 确认） |
| STOPPED | STOPPED | 实例已停止 |
| DELETING | STOPPED | 实例删除中 |
| UNKNOWN | PENDING | 未知状态 |

### 4.2 Pool 选择逻辑

```python
def select_pool(config: DockerDeploymentConfig, pools: dict[str, WuyingPoolConfig]) -> str | None:
    """
    选择最佳匹配的 Pool
    
    规则:
    1. 筛选 image 匹配的 pools
    2. 筛选 cpus >= config.cpus 且 memory >= config.memory 的 pools
    3. 选择资源最小的 pool（best fit）
    
    Returns:
        pool_name 或 None（无匹配）
    """
```

---

## 5. SSH 认证配置

### 5.1 配置优先级

```
环境变量 > 配置文件 > 默认值
```

### 5.2 配置来源

| 来源 | 用户名配置 | 密码配置 |
|------|-----------|----------|
| 默认值 | `user` | `password` |
| 配置文件 | `wuying.ssh_username` | `wuying.ssh_password` |
| 环境变量 | `ROCK_WUYING_SSH_USERNAME` | `ROCK_WUYING_SSH_PASSWORD` |

### 5.3 WuyingConfig 扩展

```python
@dataclass
class WuyingConfig:
    """Wuying Operator 配置"""
    region_id: str = "cn-hangzhou"
    endpoint: str = "ecd.cn-hangzhou.aliyuncs.com"
    office_site_id: str = ""
    policy_group_id: str = ""
    
    # SSH 认证配置
    ssh_username: str = "user"      # 配置文件可覆盖
    ssh_password: str = "password"  # 配置文件可覆盖
    
    pools: dict[str, WuyingPoolConfig] = field(default_factory=dict)
```

### 5.4 环境变量

| 变量名 | 说明 | 优先级 |
|--------|------|--------|
| `ALIBABA_CLOUD_ACCESS_KEY_ID` | 阿里云 AccessKey ID | - |
| `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | 阿里云 AccessKey Secret | - |
| `ROCK_WUYING_SSH_USERNAME` | SSH 用户名（覆盖配置文件） | 最高 |
| `ROCK_WUYING_SSH_PASSWORD` | SSH 密码（覆盖配置文件） | 最高 |

### 5.5 配置示例

```yaml
# rock-conf/config.yaml
wuying:
  region_id: "cn-hangzhou"
  ssh_username: "admin"      # 覆盖默认值 "user"
  ssh_password: "mySecret"   # 覆盖默认值 "password"
  pools:
    ...
```

### 5.6 代码实现

```python
def get_ssh_credentials(wuying_config: WuyingConfig) -> tuple[str, str]:
    """
    获取 SSH 凭据，按优先级合并
    
    Returns:
        (username, password)
    """
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

---

## 6. 错误码

| 错误码 | 说明 | 处理建议 |
|--------|------|----------|
| POOL_NOT_FOUND | 没有匹配的 Pool | 检查配置，确保有对应 image 和规格的 pool |
| DESKTOP_CREATE_FAILED | 创建云电脑失败 | 检查 bundle_id、配额等 |
| SSH_CONNECTION_FAILED | SSH 连接失败 | 检查网络、密码、安全组 |
| ROCKLET_START_FAILED | rocklet 启动失败 | 检查 bundle 是否预装 rocklet |
