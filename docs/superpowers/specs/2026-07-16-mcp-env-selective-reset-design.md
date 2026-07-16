# McpEnv 选择性重置设计

## 背景

`McpEnv.reset()` 当前会遍历所有已配置的 DataLifecycle，并依次调用它们的 `reset()`。调用方无法只清理
某几个工具的数据；在同时配置多个 MCP Server 时，即使只需要重置 `git`，其他生命周期也会被重置。

本设计为 `reset()` 增加可选的 `keys` 参数，同时保持不传参数时的现有全量重置行为。

## 目标

- 不传 `keys` 时重置全部已配置 DataLifecycle，保持向后兼容。
- 传入非空 `keys` 时，只重置名称匹配的 DataLifecycle。
- 显式传入空列表时不调用任何 DataLifecycle，作为 NOP 返回。
- 忽略未配置或不支持的 key。
- 保持现有同步与异步 `reset()` 实现兼容。
- 保持 DataLifecycle 的配置顺序。

## 非目标

- 不修改 `init()`、`dump()`、`start()`、`release()` 或 EnvLifecycle 行为。
- 不增加并行 reset、返回值聚合或失败重试。
- 不改变 lifecycle 抛出异常时的传播方式。
- 不增加 `keys` 的运行时类型校验。

## 方案选择

### 采用：以 `None` 区分省略参数与显式空列表

接口声明为：

```python
async def reset(self, keys: list[str] | None = None) -> None:
```

`None` 表示调用方没有传入筛选条件，因此执行全量重置；列表表示调用方明确提供了筛选条件，其中空列表
自然表示没有任何目标。这种设计能准确表达三种调用语义，同时避免使用可变默认参数。

### 未采用：`keys: list[str] = []`

该签名无法仅凭参数值区分“省略参数”和“显式传入空列表”，也使用了可变默认参数。若额外引入哨兵值，
公开类型签名会比 `None` 方案更复杂。

### 未采用：可变位置参数

`reset(*keys: str)` 可以区分有无 key，但调用形式会变成 `reset("git", "code-executor")`，不符合调用方
希望传入列表的接口形式。

## 详细设计

`reset()` 根据 `keys` 选择目标：

1. `keys is None`：遍历 `self.data_lifecycles.items()` 的全部条目。
2. `keys` 是列表：将其转换为集合用于成员判断，并按 `self.data_lifecycles` 的原始顺序筛选匹配条目。
3. `keys == []`：筛选结果为空，循环不执行，方法返回 `None`。
4. `keys` 包含未知名称：未知名称没有对应条目，因此被静默忽略。

对于每个被选中的 lifecycle，继续按现有方式调用 `lifecycle.reset()`；如果返回 awaitable，则等待其完成。
任一 lifecycle 抛出异常时立即向调用方传播，不继续处理后续 lifecycle，与现有行为一致。

## 调用语义

```python
await env.reset()                         # 重置全部 DataLifecycle
await env.reset(["git"])                  # 只重置 git
await env.reset(["git", "code-executor"]) # 只重置两个匹配项
await env.reset([])                       # NOP
await env.reset(["unknown"])              # NOP，未知 key 被忽略
```

## 兼容性

- 现有 `await env.reset()` 调用保持全量重置。
- 返回类型仍为 `None`。
- 同步和异步 lifecycle 的处理方式不变。
- lifecycle 顺序、异常传播及 runtime 状态均不变。
- 新参数使用 `list[str] | None`，符合项目支持的 Python 3.10–3.12。

## 测试设计

在 `tests/unit/sdk/mcp/test_mcp_env.py` 覆盖：

1. 不传 `keys` 时，所有已配置的同步和异步 lifecycle 都重置一次。
2. 传入单个或多个 key 时，只有匹配的 lifecycle 被重置。
3. 显式传入空列表时，没有 lifecycle 被重置。
4. 传入未知 key 时，已配置 lifecycle 不被误重置，方法正常返回。
5. 现有 runtime 状态、URL 和已解析 server 配置不受 reset 影响。

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
