# McpEnv 构造失败时的认证租约回滚设计

## 背景

`McpEnv` 在构造阶段遍历 `servers`，并通过 ScaffoldHub
`DataLifecycleFactory.create()` 急切创建数据生命周期。工厂创建数据生命周期前会调用共享
`AuthProvider.provide()`；当凭据来自远端认证池时，这一步会借出并记录数据库认证租约。

当前 `McpEnv.release()` 会主动调用 `AuthProvider.release_active_leases()`，`start()` 失败也会进入
`release()`。但是，如果前一个 lifecycle 已成功借出凭据，而后续 lifecycle 在
`McpEnv.__init__()` 内创建失败，构造异常会直接逃逸。调用方拿不到 `McpEnv` 实例，因此无法调用
`release()`，已借出的租约只能等待服务端租期到期。

本设计在不改变现有生命周期语义的前提下，为构造阶段增加事务式认证租约回滚，并适当减少
`McpEnv.__init__()` 的代码量。

## 目标

- 任意 lifecycle 在构造阶段创建失败时，立即释放当前 `AuthProvider` 已记录的全部活跃租约。
- 保留原始构造异常，不让清理异常掩盖真正的创建失败原因。
- 保持 `McpEnv` 构造成功后的 `init()`、`dump()`、`reset()`、`start()` 和 `release()` 语义不变。
- 将 lifecycle 创建循环从 `__init__()` 提取到职责单一的私有方法。
- 避免在实例字段中留下仅完成一部分的 lifecycle 映射。

## 非目标

- 不把凭据借用或 DataLifecycle 创建推迟到 `start()`。
- 不修改 ScaffoldHub 的 `AuthProvider` 或 `DataLifecycleFactory` 接口。
- 不改变认证租约时长、远端认证服务协议或凭据池容量策略。
- 不给 `McpEnv` 增加上下文管理器、析构器或后台续租机制。
- 不改变正常 `release()`、启动失败回滚及环境生命周期释放顺序。
- 不为 lifecycle 对象增加新的同步关闭协议；构造失败阶段只回滚共享 `AuthProvider` 持有的租约。

## 方案选择

### 采用：构造阶段事务式回滚

继续在 `McpEnv.__init__()` 阶段创建 lifecycle，但把整个创建过程放进一个私有方法。私有方法使用
局部字典收集创建结果；任意步骤失败时调用 `release_active_leases()`，全部成功后才把两个结果字典
返回给 `__init__()`。

这个方案改动范围小，并保持构造完成后立即可使用数据生命周期的现有行为。

### 未采用：将借用推迟到 `start()`

当前借用发生在 `DataLifecycleFactory.create()` 内。要推迟借用，需要把 DataLifecycle 创建本身一并
推迟，或者修改 ScaffoldHub 工厂协议。这会改变 `start()` 前调用 `init()`、`dump()`、`reset()` 的
行为，还会引入失败重试时重建 lifecycle、清除持有旧凭据对象等问题。本次修复不扩大到该范围。

### 未采用：依赖析构器兜底

Python 析构时机不确定，进程异常退出时也没有可靠保证，不适合承担远端租约的主要释放职责。

## 详细设计

### `McpEnv.__init__()`

`__init__()` 保留以下职责：

1. 校验并快照 `servers`。
2. 加载 ScaffoldHub 组件。
3. 创建共享 `AuthProvider`、两个 lifecycle factory 和 `RockRuntime`。
4. 调用私有方法创建 lifecycle，并一次性接收完整结果。

生命周期字段初始化收敛为：

```python
self.data_lifecycles, self.env_lifecycles = self._create_lifecycles()
```

`__init__()` 不再直接包含 lifecycle 遍历和分支逻辑。

### `_create_lifecycles()`

新增私有方法 `_create_lifecycles(self) -> tuple[dict[str, Any], dict[str, Any]]`。

方法行为：

1. 创建局部 `data_lifecycles` 和 `env_lifecycles` 字典。
2. 按 `self.servers` 的声明顺序遍历 lifecycle type。
3. 对 DataLifecycle factory 执行 `supports()` 和必要的 `create()`。
4. 对 EnvLifecycle factory 执行 `supports()` 和必要的 `create()`。
5. 全部成功后返回两个局部字典。

局部字典保证只有完整构造成功的结果才会赋给实例字段。该方法不改变原有 server 顺序及
DataLifecycle 先于同名 EnvLifecycle 创建的顺序。

### 异常与回滚

创建循环以 `BaseException` 作为回滚边界，仅执行清理后通过裸 `raise` 原样重新抛出。这可以在
普通异常以及构造期间发生进程级中断时尽最大努力释放已经借出的远端租约，同时不会吞掉中断。

回滚调用：

```python
self.auth_provider.release_active_leases()
```

该调用覆盖以下失败位置：

- `DataLifecycleFactory.supports()` 失败；
- `DataLifecycleFactory.create()` 在借用凭据之前或之后失败；
- lifecycle 实现类动态导入或实例化失败；
- `EnvLifecycleFactory.supports()` 或 `create()` 失败；
- 后续 lifecycle 因无可用凭据而失败，而前面的 lifecycle 已持有租约。

如果 `release_active_leases()` 自身失败：

- 捕获清理异常并记录 warning；
- warning 包含“构造失败后的认证租约回滚失败”语义及清理异常内容；
- 继续原样抛出最初的 lifecycle 构造异常。

选择保留原始异常，是因为它解释了 `McpEnv` 为什么无法创建；清理失败仍可通过日志观测。由于
构造没有成功返回，调用方不存在可用于重试释放的 `McpEnv` 实例。

## 数据流

成功路径：

1. `McpEnv` 创建共享 `AuthProvider`。
2. `_create_lifecycles()` 创建局部结果字典。
3. DataLifecycle factory 通过共享 provider 获取凭据。
4. 所有 lifecycle 创建成功。
5. 完整字典一次性赋给 `McpEnv`。
6. 调用方最终通过现有 `await env.release()` 归还租约。

失败路径：

1. 前一个 DataLifecycle 通过共享 provider 借出租约。
2. 当前或后续 lifecycle 创建失败。
3. `_create_lifecycles()` 调用 `release_active_leases()`。
4. 清理成功或记录清理失败 warning。
5. 原始构造异常继续抛给调用方。

## 兼容性

- `McpEnv` 公共构造参数不变。
- lifecycle 仍在构造阶段创建，`start()` 前的数据生命周期调用方式不变。
- 成功构造时不会提前释放租约。
- `release()` 仍会在每次调用时尝试释放活跃租约。
- `start()` 失败后的现有回滚路径不变。
- lifecycle 创建顺序不变。
- 不要求 ScaffoldHub 发布新的接口版本。

## 测试设计

在 `tests/unit/sdk/mcp/test_mcp_env.py` 增加或调整测试，覆盖：

1. 第一个 DataLifecycle 已借出凭据，第二个 DataLifecycle 创建失败：
   - 构造抛出第二个 lifecycle 的原始异常；
   - `release_active_leases()` 恰好调用一次。
2. DataLifecycle 创建成功，随后同名 EnvLifecycle 创建失败：
   - 原始 EnvLifecycle 创建异常继续抛出；
   - 已借出的认证租约被释放。
3. lifecycle 创建失败且认证租约回滚也失败：
   - 调用方收到原始 lifecycle 构造异常；
   - 日志包含认证租约回滚失败 warning。
4. 所有 lifecycle 创建成功：
   - 两个映射内容与现有行为一致；
   - 构造期间不调用 `release_active_leases()`。
5. 现有正常释放、重复释放、配置解析失败和 runtime 启动失败回滚测试继续通过。

测试 fake 应显式记录 `provide()`、lifecycle create 和 `release_active_leases()` 调用，验证回滚发生在
构造异常传播之前。测试不依赖真实认证服务。

## 验证

运行聚焦测试：

```bash
uv run pytest tests/unit/sdk/mcp/test_mcp_env.py -v
```

运行相关 lint 和格式检查：

```bash
uv run ruff check rock/sdk/mcp/mcp_env.py tests/unit/sdk/mcp/test_mcp_env.py
uv run ruff format --check rock/sdk/mcp/mcp_env.py tests/unit/sdk/mcp/test_mcp_env.py
```

实施代码变更前按照项目规范创建或确认关联的 GitHub Issue；后续分支、提交和 PR 均关联该 Issue。
