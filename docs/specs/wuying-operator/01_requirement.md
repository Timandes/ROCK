# Wuying Operator 需求规格

## Background

ROCK 项目目前支持 Ray 和 K8s 两种沙箱后端，分别用于 Ray Actor 集群和 Kubernetes 集群。阿里云云电脑（无影/ECD）提供了一种弹性、按需付费的计算资源，可以作为一种新的沙箱后端，适用于以下场景：

1. **Windows 环境沙箱**：云电脑支持 Windows 镜像，可以执行 Windows 特有的测试任务
2. **隔离性要求高的场景**：每个云电脑实例完全隔离，适合安全敏感任务
3. **弹性扩展**：按需创建/销毁，无需维护集群

## In/Out

### In (包含的功能范围)

1. **WuyingOperator**：实现 `AbstractOperator` 接口，支持云电脑实例的 CRUD
   - `submit()`: 创建云电脑实例
   - `get_status()`: 查询实例状态
   - `stop()`: 销毁实例

2. **WuyingDeployment**：实现 `AbstractDeployment` 接口，支持云电脑内部的部署
   - SSH 连接云电脑
   - 启动预装的 rocklet 服务
   - 提供 `RemoteSandboxRuntime` 接口

3. **Pool 模式**：借鉴 K8s 的 Pool 设计，通过配置文件映射 `image` → `bundle_id`
   - 支持多规格（cpu/memory）的 pool 选择
   - 复用 `ResourceMatchingPoolSelector` 匹配逻辑

4. **配置扩展**：新增 `WuyingConfig` 配置类

### Out (明确不做什么)

1. **RuntimeEnv 扩展**：不新增 RuntimeEnv 类型，因为 bundle 已预装 rocklet
2. **动态镜像构建**：不支持在创建实例时指定自定义镜像，必须使用预定义的 bundle
3. **Windows 支持**：初期仅支持 Linux 云电脑，Windows 支持作为后续扩展
4. **连接池**：不实现云电脑实例池化/预热，按需创建

## Acceptance Criteria

### AC1: 基本功能

- 用户通过 `/start_async` API 请求创建沙箱，指定 `image` 和 `cpus/memory`
- 系统自动匹配对应的 bundle，创建云电脑实例
- 实例创建后，SSH 连接并启动 rocklet
- 用户可以执行命令、读写文件等操作
- 调用 `/stop` API 可以销毁云电脑实例

### AC2: Pool 匹配逻辑

- 当用户请求 `image="python:3.11", cpus=2, memory="4g"` 时：
  - 筛选所有 `image="python:3.11"` 的 pools
  - 筛选满足 `cpus >= 2, memory >= 4g` 的 pools
  - 选择最小规格的 pool（best fit）
- 如果没有匹配的 pool，返回错误

### AC3: 状态管理

- `submit()` 返回 `state=PENDING`，包含 `sandbox_id`
- `get_status()` 返回实时状态：
  - 实例创建中 → `PENDING`
  - rocklet 启动成功 → `RUNNING`
  - 实例已销毁 → `STOPPED`

### AC4: 资源清理

- 用户调用 `stop()` 或超时后，必须销毁云电脑实例
- 避免资源泄露

## Constraints

### 性能约束

- 云电脑创建时间约 1-3 分钟，需在 API 层面处理超时
- SSH 连接需要处理实例启动延迟

### 安全约束

- 阿里云 AK/SK 需要通过环境变量获取
- SSH 凭据管理：
  - 默认值：用户名 `user`，密码 `password`
  - 配置文件可覆盖默认值
  - 环境变量 `ROCK_WUYING_SSH_USERNAME` / `ROCK_WUYING_SSH_PASSWORD` 优先级最高
  - 优先级：环境变量 > 配置文件 > 默认值

### 兼容性约束

- 必须复用现有 `DockerDeploymentConfig` 作为请求参数（不修改 API）
- 通过 `RuntimeConfig.operator_type = "wuying"` 切换后端
- 通过 `extended_params` 传递云电脑特有参数（如 pool_name）

### 依赖系统

- 阿里云 ECD SDK (`alibabacloud_ecd20200930`)
- paramiko (SSH 连接)

## Design Decisions

### DD1: Operator 和 Deployment 职责边界

**决策**：WuyingOperator 只负责实例 CRUD，WuyingDeployment 负责 SSH 连接

**理由**：
- 与 RayOperator/K8sOperator 保持架构一致性
- 职责单一，便于独立测试和维护
- Deployment 层负责"如何在实例内部署"，Operator 层负责"如何管理实例"

### DD2: 实例创建状态同步

**决策**：`submit()` 异步创建，立即返回 PENDING，`get_status()` 轮询

**理由**：
- 云电脑创建需要 1-3 分钟，阻塞式 API 会导致超时
- 与 K8s 的 BatchSandbox CRD 模式一致
- 可复用现有的 Redis 状态存储机制

### DD3: SSH 连接时机

**决策**：延迟初始化，在 `get_status()` 检测到实例可用后触发 SSH 连接

**理由**：
- 简单，无需引入额外状态（如 DEPLOYING）
- SSH 连接逻辑封装在 WuyingDeployment 内部
- 符合现有 RemoteDeployment 的模式

### DD4: Pool 配置独立性

**决策**：Wuying 使用独立的 Pool 配置，不与 K8s 共享

**理由**：
- 云电脑特有的 `bundle_id`、`office_site_id`、`policy_group_id` 字段
- 规格定义方式不同（`desktop_type` vs K8s 的资源请求）
- 避免配置耦合，便于独立演进

## Risks & Rollout

### 风险点

1. **创建延迟**：云电脑创建时间较长，可能影响用户体验
   - 缓解：前端显示"创建中"状态，支持异步查询

2. **成本控制**：云电脑按小时计费，实例泄露会导致持续计费
   - 缓解：强制 auto_clear_time，实现超时自动清理

3. **SSH 连接不稳定**：网络波动可能导致 SSH 断开
   - 缓解：实现重连机制

### 上线策略

1. **阶段一**：仅支持 Linux 云电脑，单个 region
2. **阶段二**：支持多 region，增加 pool 配置
3. **阶段三**：支持 Windows 云电脑（可选）

### 回滚预案

- 通过 `RuntimeConfig.operator_type` 切换回 `ray` 或 `k8s`
- 删除 `WuyingConfig` 配置即可禁用功能
