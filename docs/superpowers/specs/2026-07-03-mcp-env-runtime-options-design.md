# McpEnv Runtime Options 设计

## 目的

`McpEnv` 当前会固定构造 `RockRuntime()`，用户无法通过 `McpEnv`
调整 runtime 行为。`RockRuntime` 已经有 MCP server 健康检查相关的超时构造参数，
但这些参数没有暴露到 `McpEnv`。

本设计新增一个小型 options 对象，用来承载 `RockRuntime` 的健康检查配置，
并让 `McpEnv` 在构造时接收该对象。这样既能暴露配置能力，也避免把
`McpEnv.__init__` 扩展成一长串低层参数。

## 目标

- 通过一个对象暴露现有 `RockRuntime` 的全部健康检查超时相关参数。
- 用户不传 options 时，保持当前默认行为不变。
- 用户可以先创建 `RockRuntimeOptions()`，只修改自己关心的字段，再传给
  `McpEnv`。
- `McpEnv` 构造时锁定 options；调用方后续修改原始 options 对象，不影响已创建的
  environment。
- 保留直接构造 `RockRuntime(...)` 的兼容性。

## 非目标

- 不新增“整体健康检查截止时间”参数。
- 不改变 `_health_check()` 当前基于 retry loop 的语义。
- 不把 sandbox startup、MCP config 写入、`before_launch` 或 server launch 时间计入任何
  健康检查 timeout 参数。
- 不通过该对象暴露无关的 ROCK sandbox 配置。

## 公共 API

新增一个非 frozen dataclass：

```python
@dataclass
class RockRuntimeOptions:
    health_check_retries: int = 10
    health_check_interval_seconds: float = 10.0
    http_timeout_seconds: float = 10.0
```

从 `rock.sdk.mcp` 导出：

```python
from rock.sdk.mcp import McpEnv, RockRuntimeOptions
```

用法示例：

```python
options = RockRuntimeOptions()
options.health_check_retries = 12
options.health_check_interval_seconds = 5.0

env = McpEnv(
    servers={"calculator": {"command": "uvx", "args": ["mcp-server-calculator==0.2.0"]}},
    runtime_options=options,
)

options.health_check_retries = 1  # 不影响 env。
```

`McpEnv.__init__` 接收：

```python
def __init__(
    self,
    servers: dict | None = None,
    runtime_options: RockRuntimeOptions | None = None,
):
    ...
```

当 `runtime_options` 为 `None` 时，`McpEnv` 使用 `RockRuntimeOptions()`，
因此默认行为与当前实现一致。

## Options 快照语义

`RockRuntimeOptions` 保持可变，方便调用方先构造对象，再按需修改字段。
对象一旦传入 `McpEnv`，`McpEnv` 会立即创建一份快照，不持有调用方原始对象引用。

具体语义：

- 在调用 `McpEnv(...)` 之前修改 options，会影响该 environment。
- 在调用 `McpEnv(...)` 之后继续修改同一个 options 对象，不影响该 environment。
- 每个 `McpEnv` 实例拥有独立的 runtime options。

实现时通过读取输入对象字段并构造新的 `RockRuntimeOptions` 来创建快照。
该 dataclass 只包含标量字段，显式逐字段复制可以让 API 边界更清楚。

## RockRuntime 构造

`RockRuntime` 接收一个 `options` 对象，同时保留现有平铺参数以兼容直接构造用法：

```python
class RockRuntime:
    def __init__(
        self,
        config: RockRuntimeConfig | None = None,
        *,
        options: RockRuntimeOptions | None = None,
        health_check_retries: int | None = None,
        health_check_interval_seconds: float | None = None,
        http_timeout_seconds: float | None = None,
    ):
        ...
```

归一化规则：

- 如果传入 `options`，先基于它创建一份快照；否则使用 `RockRuntimeOptions()`。
- 如果同时传入任意平铺参数，则平铺参数覆盖对应的 option 字段。
  这样可以保留既有 `RockRuntime(...)` 直接使用方式，也方便测试继续单独覆盖某个值。
- 将最终快照保存为 `self.options`。
- 保留现有 runtime 属性 `self.health_check_retries`、
  `self.health_check_interval_seconds` 和 `self.http_timeout_seconds`，这些属性从
  `self.options` 复制赋值。这样既保留测试或内部调用方可能依赖的直接属性访问，
  又把 options 归一化集中在一个地方。

`McpEnv` 构造 runtime 时使用：

```python
self._rock_runtime = RockRuntime(options=runtime_options_snapshot)
```

## 校验

在 `RockRuntime` 构造期间校验归一化后的 runtime options：

- `health_check_retries` 必须是大于等于 `1` 的整数。
- `health_check_interval_seconds` 必须是大于 `0` 的数字。
- `http_timeout_seconds` 必须是大于 `0` 的数字。

非法值抛出 `RockRuntimeConfigError`，错误信息应包含具体字段，例如：

- `health_check_retries must be >= 1`
- `health_check_interval_seconds must be > 0`
- `http_timeout_seconds must be > 0`

校验发生在 sandbox 启动之前。因为 `McpEnv(...)` 会在初始化阶段构造
`RockRuntime`，所以非法 options 会快速失败。

## 数据流

1. 调用方创建并按需修改 `RockRuntimeOptions`。
2. 调用方将 options 传给 `McpEnv`。
3. `McpEnv` 创建 options 快照，并构造 `RockRuntime(options=...)`。
4. `McpEnv.start()` 按现有逻辑解析 server 配置。
5. `RockRuntime.start()` 启动 sandbox，并最终调用 `_health_check()`。
6. `_health_check()` 读取归一化后的 retry 次数、间隔和单次 HTTP 请求 timeout。

生命周期、auth、server config 解析、sandbox 注入和 release 行为均不改变。

## 测试

新增单元测试覆盖：

- `RockRuntimeOptions()` 暴露当前默认值。
- `McpEnv(runtime_options=...)` 将 option 值传入其 `RockRuntime`。
- `McpEnv(...)` 之后修改原始 options，不影响 `env._rock_runtime.options`。
- `RockRuntime(options=..., health_check_retries=...)` 会应用平铺参数覆盖，
  保持直接构造兼容性。
- 非法 option 值抛出 `RockRuntimeConfigError`。
- 现有 `McpEnv(servers=...)` 和 `RockRuntime(...)` 测试无需 API 调整即可继续通过。

集成测试不需要覆盖非默认 timing。该改动主要是配置传递和 runtime 校验；
现有真实 ROCK MCP 集成测试足以覆盖默认行为。

## 文档

更新 MCP SDK 文档，增加一个简短示例：

```python
options = RockRuntimeOptions()
options.health_check_retries = 12
env = McpEnv(servers=servers, runtime_options=options)
```

文档中需要说明：options 传入 `McpEnv` 时会被快照锁定。
