# McpEnv SandboxAware 生命周期注入设计

## 背景

ROCK 拥有 `rock.sdk.mcp.McpEnv` 和 `RockRuntime`。`McpEnv` 通过
ScaffoldHub 的 `DataLifecycleFactory` 创建工具数据生命周期；`RockRuntime`
负责创建 ROCK sandbox、写入 `/app/mcp-servers.json`、启动 MCP server 进程、
检查 SSE endpoint，并在释放时停止 sandbox。

ScaffoldHub 现在从 `scaffoldhub.tools.base` 导出 `SandboxAware`。
`SandboxAware` 是一个很窄的 lifecycle 标记接口，公共契约只有：

```python
def set_sandbox(self, sandbox) -> None:
    ...
```

ScaffoldHub 的设计要求外部 launcher（包括 ROCK `McpEnv`）把外部创建好的
ROCK sandbox 注入到实现了 `SandboxAware` 的 lifecycle 中。这个标记接口不要求
launcher 调用 lifecycle 的 `before_launch()` 方法。那些方法仍然是具体 lifecycle
内部的兼容 helper，不属于公开集成契约。

## 目标

- 自动把 ROCK sandbox 注入到实现了 `SandboxAware` 的 ScaffoldHub lifecycle。
- 注入发生在 sandbox 启动并写入 `/app/mcp-servers.json` 之后。
- 注入发生在用户传入的 `before_launch(sandbox)` 回调之前。
- 保持现有 `McpEnv.start(before_launch=None)` API 形状不变。
- 保持 `RockRuntime` 不感知 ScaffoldHub 数据生命周期概念。
- 注入失败时复用当前启动失败清理逻辑。

## 非目标

- 不自动调用 lifecycle 的 `before_launch()`。
- 不向 `SandboxAware` 增加 `prepare`、`before_launch` 或 sandbox setup 方法。
- 不向 `RockRuntime.start()` 增加 lifecycle 参数。
- 不把 ScaffoldHub lifecycle 处理移动到 `RockRuntime`。
- 不改变 `init`、`dump`、`reset`、`release`、URL 生成、auth 占位符解析或
  auth lease release 语义。

## 当前流程

现在 `McpEnv.start()` 解析 server 配置后，会把调用方传入的 `before_launch`
回调原样传给 `RockRuntime.start()`。

```text
McpEnv.start(before_launch=user_hook)
  解析 server 配置
  RockRuntime.start(resolved_servers, before_launch=user_hook)
    Sandbox.start()
    准备 /app/workspace 和 /data
    写入 /app/mcp-servers.json
    user_hook(sandbox)
    启动 /app/launch.sh
    检查 SSE endpoints
```

这给调用方提供了一个 pre-launch hook，但如果某个 lifecycle 需要 sandbox，
调用方必须自己手写 SandboxAware 注入逻辑。

## 设计流程

`McpEnv` 会组合一个内部 pre-launch hook，再把这个 hook 传给
`RockRuntime.start()`。`RockRuntime` 保持相同的公共 API 和内部启动顺序。

```text
McpEnv.start(before_launch=user_hook)
  解析 server 配置
  组合 McpEnv pre-launch hook
  RockRuntime.start(resolved_servers, before_launch=mcp_env_hook)
    Sandbox.start()
    准备 /app/workspace 和 /data
    写入 /app/mcp-servers.json
    mcp_env_hook(sandbox)
      向 SandboxAware lifecycles 注入 sandbox
      user_hook(sandbox)
    启动 /app/launch.sh
    检查 SSE endpoints
```

外部可见顺序为：

1. 创建并启动 sandbox。
2. 准备 runtime 目录。
3. 写入 `/app/mcp-servers.json`。
4. `McpEnv` 对每个 `SandboxAware` lifecycle 调用 `set_sandbox(sandbox)`。
5. 如果调用方提供了 `before_launch(sandbox)`，再执行该回调。
6. 启动 MCP servers 并做 health check。

## 组件职责

### `RockRuntime`

`RockRuntime` 仍然是 MCP runtime 编排器。它抽象的是“把 MCP server 配置跑进
ROCK sandbox，并产出 SSE URL”的固定流程：

- 创建 `Sandbox(SandboxConfig(...))`；
- 准备 runtime 目录；
- 渲染并写入 MCP server 配置；
- 暴露一个通用 pre-launch hook 插槽；
- 启动 `/app/launch.sh`；
- 检查 SSE endpoints；
- release 或启动失败时停止 sandbox。

它不应该 import 或引用 `DataLifecycle`、`DataLifecycleFactory`、`SandboxAware`。

### `McpEnv`

`McpEnv` 仍然是 ROCK MCP runtime 和 ScaffoldHub 资源之间的集成 facade。它已经
拥有 ScaffoldHub auth provider、data lifecycle 创建、占位符解析、lifecycle
`init/dump/reset` 以及 auth lease release。`SandboxAware` 注入属于
ScaffoldHub lifecycle 集成职责，因此应放在 `McpEnv` 中。

## 详细设计

### 加载 ScaffoldHub 组件

`_load_scaffoldhub_components()` 当前加载 `AuthProvider` 和
`DataLifecycleFactory`。它将额外尝试加载 `SandboxAware`。

首选导入来源为：

```python
from scaffoldhub.auth import AuthProvider
from scaffoldhub.tools.base import DataLifecycleFactory
```

`SandboxAware` 应单独导入，这样兼容处理更精确：

```python
try:
    from scaffoldhub.tools.base import SandboxAware
except ImportError:
    SandboxAware = None
```

为了兼容旧版 ScaffoldHub，缺少 `SandboxAware` 不应导致 `McpEnv` 构造失败。
如果 `AuthProvider` 或 `DataLifecycleFactory` 无法导入，`McpEnv` 仍保留当前
清晰的 optional dependency 错误。如果只有 `SandboxAware` 缺失，`McpEnv`
保存 `None` 并跳过自动注入。

### 构造状态

`McpEnv.__init__()` 保存加载到的标记类：

```python
self.sandbox_aware_class = sandbox_aware_class
```

不新增公开构造参数。

### Start Hook 组合

`McpEnv.start(before_launch=None)` 在调用 `RockRuntime.start()` 前组合 hook：

```python
runtime_before_launch = self._compose_before_launch(before_launch)
urls = await self._rock_runtime.start(
    self.resolved_servers,
    before_launch=runtime_before_launch,
)
```

组合后的 hook 总是先执行 SandboxAware 注入，然后执行调用方传入的 hook。
调用方 hook 继续支持同步和异步两种形式。

### SandboxAware 注入

`McpEnv` 新增私有 helper：

```python
def _inject_sandbox_into_lifecycles(self, sandbox: Sandbox) -> None:
    sandbox_aware_class = self.sandbox_aware_class
    if sandbox_aware_class is None:
        return

    for lifecycle in self.data_lifecycles.values():
        if isinstance(lifecycle, sandbox_aware_class):
            lifecycle.set_sandbox(sandbox)
```

该 helper 不检查 lifecycle 名称，不为具体工具写白名单，也不调用 lifecycle 的
`before_launch()`。

### 用户 Hook 调用

`McpEnv` 复用当前 `RockRuntime` 的同步/异步 hook 语义：

```python
result = before_launch(sandbox)
if inspect.isawaitable(result):
    await result
```

这样保持现有调用方兼容，同时保证调用方 hook 执行时，相关 lifecycles 已经完成
sandbox 注入。

## 错误处理

如果 `set_sandbox()` 抛异常，组合 hook 直接抛出。`RockRuntime.start()` 已经把
hook 失败视为启动失败：

- 调用 `stop()` 清理 sandbox；
- 记录 cleanup 失败，但不遮蔽原始启动错误；
- 抛出 `RockRuntimeError`，并把原始错误挂在异常链上。

`McpEnv` 不应增加第二套清理路径。

如果调用方的 `before_launch()` 在注入成功后抛异常，行为与今天一致：启动失败，
由 `RockRuntime` 清理 sandbox。

## 兼容性

不使用 ScaffoldHub `SandboxAware` 的现有调用方继续正常工作。已有
`before_launch(sandbox)` 回调的调用方仍会在同一个 runtime 时机拿到原始 ROCK
sandbox，只是此时自动 lifecycle 注入已经完成。

缺少 `SandboxAware` 的旧版 ScaffoldHub 保持旧行为：不执行自动 lifecycle 注入。
如果调用方确实需要注入 sandbox，仍可在自己的 `before_launch` 回调中手动完成。

## 测试

单测应覆盖：

- `McpEnv.start()` 会向实现 `SandboxAware` 的 lifecycle 注入 sandbox。
- 用户 `before_launch(sandbox)` 在 SandboxAware 注入之后执行。
- 非 SandboxAware lifecycle 会被忽略。
- 当 ScaffoldHub 不导出 `SandboxAware` 时，`McpEnv` 仍按旧行为启动。
- `set_sandbox()` 抛异常时，启动失败并走 runtime cleanup 路径。
- 现有占位符解析、lifecycle `init/dump/reset`、auth lease release、raw sandbox
  property 测试继续通过。

实现后的聚焦验证命令：

```bash
uv run pytest tests/unit/sdk/mcp/test_mcp_env.py -v
uv run pytest tests/unit/sdk/mcp/test_rock_runtime.py -v
uv run ruff check rock/sdk/mcp tests/unit/sdk/mcp
uv run ruff format rock/sdk/mcp tests/unit/sdk/mcp
```

## 文档

更新 MCP SDK 文档，说明：

- `McpEnv` 会自动把已启动的 ROCK sandbox 注入到实现 `SandboxAware` 的
  ScaffoldHub lifecycle。
- 注入发生在 `/app/mcp-servers.json` 写入之后、调用方 `before_launch` 回调之前。
- `McpEnv` 不会自动调用 lifecycle 的 `before_launch()` 方法。

## 风险

- 某些 lifecycle 可能实现了 `SandboxAware`，但仍需要调用方执行额外准备方法。
  本设计有意只做依赖注入；完整 lifecycle 准备需要另一个公开契约。
- 旧版 ScaffoldHub package 不导出 `SandboxAware`。兼容行为可以避免构造失败，
  但不会提供自动注入。
- 多个 lifecycles 会收到同一个 sandbox 对象。如果它们共享可变 sandbox 状态，
  需要由具体 lifecycle 自身保证使用方式正确。这符合外部 launcher 拥有 sandbox
  的模型。
