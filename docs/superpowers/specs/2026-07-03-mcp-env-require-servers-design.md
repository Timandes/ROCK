# McpEnv 非空 servers 构造校验设计

## 背景

`rock.sdk.mcp.McpEnv` 是 ROCK MCP SDK 的 facade，负责解析 MCP server 配置、
启动 ROCK sandbox、写入 `/app/mcp-servers.json`、暴露 SSE URL，并代理
ScaffoldHub lifecycle 的 `init/dump/reset/release`。

当前 `McpEnv.__init__(servers=None)` 会把 `None` 转成 `{}`，因此 `McpEnv()`
和 `McpEnv(servers={})` 都可以构造成功。单测中也有用例覆盖“无 servers 时允许
空数据初始化，但未启动前不能获取 URL”的行为。

这个行为容易掩盖调用方误用。`McpEnv` 的主要目的就是运行一组 MCP servers。
当没有任何 server 配置时，继续构造一个空环境没有实际运行价值，后续甚至可能
启动一个不包含 MCP server 的 sandbox。更清晰的契约是在构造期直接失败。

## 目标

- `McpEnv()`、`McpEnv(servers=None)` 和 `McpEnv(servers={})` 都在构造期报错。
- 非 dict 类型的 `servers` 仍然保持类型错误语义。
- 合法的非空 dict 配置保持现有行为不变。
- 错误信息清晰指向 `servers` 必须是非空 dict。
- 单测覆盖无参、`None`、空 dict、非 dict 和合法非空 dict 的构造行为。
- 文档明确 `servers` 是必需的非空配置。

## 非目标

- 不改变 `McpEnv.start()` 的运行顺序。
- 不改变 auth 占位符解析、lifecycle 创建、`init/dump/reset/release` 语义。
- 不改变 `RockRuntime` 的接口或空 server health check 行为。
- 不新增兼容期 warning。

## 方案选择

采用构造期严格校验。

备选方案包括在 `start()` 时校验，或先 warning 后续版本再报错。启动期校验会让
错误离根因更远，并继续保留空环境的半有效状态。warning 适合必须兼容旧调用方的
公开 API，但这里空 `servers` 对 `McpEnv` 来说是无实际运行意义的配置，直接失败
更符合 SDK facade 的职责边界。

## 详细设计

`McpEnv.__init__` 的签名可以暂时保持：

```python
def __init__(self, servers: dict | None = None):
    ...
```

保留 `None` 类型是为了让调用方得到清晰的 `ValueError`，而不是 Python 缺参时的
默认 `TypeError`。构造逻辑调整为：

1. 如果 `servers` 不是 dict：
   - 当 `servers is None` 时抛 `ValueError("servers must be a non-empty dict")`。
   - 其他非 dict 类型抛 `TypeError("servers must be a dict")`。
2. 如果 `servers` 是空 dict，抛 `ValueError("servers must be a non-empty dict")`。
3. 如果 `servers` 是非空 dict，继续按现有逻辑深拷贝、创建 auth provider、
   创建 lifecycle、初始化 `RockRuntime`。

错误类型区分的目的：

- `servers=[]`、`servers="slack"` 这类输入是类型错误，继续使用 `TypeError`。
- `McpEnv()`、`servers=None`、`servers={}` 代表缺少必需配置，使用 `ValueError`
  更准确。

## 测试设计

更新 `tests/unit/sdk/mcp/test_mcp_env.py`：

- 保留 `test_mcp_env_constructor_requires_servers_dict` 覆盖非 dict 类型抛
  `TypeError("servers must be a dict")`。
- 新增或调整构造校验测试，覆盖：
  - `McpEnv()` 抛 `ValueError("servers must be a non-empty dict")`；
  - `McpEnv(servers=None)` 抛同样的 `ValueError`；
  - `McpEnv(servers={})` 抛同样的 `ValueError`。
- 删除或改写当前允许无 servers 初始化的测试。
- 现有带 `servers={"slack": ...}` 的 `init/dump/reset/release/start` 测试保持通过。
- 当前部分测试使用 `McpEnv()` 只为了测试 `init` 输入类型；这些测试应改为传入
  最小合法 server 配置，避免依赖空环境。

推荐验证命令：

```bash
uv run pytest tests/unit/sdk/mcp/test_mcp_env.py -q
```

## 文档设计

在 MCP SDK 文档中补充一句：`servers` 必须是非空 dict，顶层 key 是 MCP server
名称或 lifecycle 类型。

已有示例已经使用非空 `servers`，无需改动示例结构。

## 兼容性影响

这是一个有意的行为收紧。依赖 `McpEnv()` 或 `McpEnv(servers={})` 创建空环境的代码
会在构造期失败。调用方应传入至少一个 MCP server 配置。

该变更不会影响正常启动真实 MCP servers 的调用方。
