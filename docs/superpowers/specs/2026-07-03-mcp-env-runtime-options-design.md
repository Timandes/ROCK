# McpEnv Runtime Options Design

## Purpose

`McpEnv` currently constructs `RockRuntime()` with fixed runtime behavior.
`RockRuntime` already has timeout-related constructor parameters for MCP server
health checks, but `McpEnv` does not expose them. Users need a stable way to
adjust these runtime health-check settings when creating an MCP environment
without expanding `McpEnv.__init__` into a long list of low-level parameters.

This change introduces a small options object for `RockRuntime` settings and
lets `McpEnv` accept it during construction.

## Goals

- Expose all existing `RockRuntime` timeout-related health-check settings
  through one object.
- Keep current behavior unchanged when users do not pass options.
- Let users create `RockRuntimeOptions()`, mutate only the fields they need,
  and pass the object to `McpEnv`.
- Lock the options at `McpEnv` construction time so later mutations to the
  caller-owned object do not change an existing environment.
- Preserve direct `RockRuntime(...)` construction compatibility.

## Non-Goals

- Do not introduce a new total health-check deadline parameter.
- Do not change the existing retry-loop semantics in `_health_check()`.
- Do not include sandbox startup, MCP config writing, `before_launch`, or server
  launch time in any health-check timeout setting.
- Do not expose unrelated ROCK sandbox configuration through this object.

## Public API

Add a non-frozen dataclass:

```python
@dataclass
class RockRuntimeOptions:
    health_check_retries: int = 10
    health_check_interval_seconds: float = 10.0
    http_timeout_seconds: float = 10.0
```

Export it from `rock.sdk.mcp`:

```python
from rock.sdk.mcp import McpEnv, RockRuntimeOptions
```

Usage:

```python
options = RockRuntimeOptions()
options.health_check_retries = 12
options.health_check_interval_seconds = 5.0

env = McpEnv(
    servers={"calculator": {"command": "uvx", "args": ["mcp-server-calculator==0.2.0"]}},
    runtime_options=options,
)

options.health_check_retries = 1  # Does not affect env.
```

`McpEnv.__init__` accepts:

```python
def __init__(
    self,
    servers: dict | None = None,
    runtime_options: RockRuntimeOptions | None = None,
):
    ...
```

When `runtime_options` is `None`, `McpEnv` uses `RockRuntimeOptions()` and
therefore keeps current default behavior.

## Option Snapshot Semantics

`RockRuntimeOptions` remains mutable for ergonomic caller-side setup. Once
passed to `McpEnv`, the environment snapshots it immediately and does not hold a
reference to the caller-owned object.

This means:

- Mutating options before `McpEnv(...)` affects that environment.
- Mutating the same options object after `McpEnv(...)` does not affect that
  environment.
- Each `McpEnv` instance owns independent runtime options.

Implementation snapshots by constructing a new `RockRuntimeOptions` from the
input fields. The dataclass only contains scalar values, and an explicit
field-by-field copy makes the API boundary clear.

## RockRuntime Construction

`RockRuntime` accepts an `options` object while keeping the existing flat
parameters for compatibility:

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

Normalization rules:

- Start from a snapshot of `options` if provided, otherwise
  `RockRuntimeOptions()`.
- If any flat parameter is provided, it overrides the corresponding option
  field. This preserves existing direct `RockRuntime(...)` use cases and lets
  tests continue to customize one value directly.
- Store the final snapshot as `self.options`.
- Keep existing runtime attributes `self.health_check_retries`,
  `self.health_check_interval_seconds`, and `self.http_timeout_seconds` as
  assigned aliases copied from `self.options`. This preserves direct attribute
  access for tests or internal callers while keeping option normalization in one
  place.

`McpEnv` should construct:

```python
self._rock_runtime = RockRuntime(options=runtime_options_snapshot)
```

## Validation

Validate normalized runtime options during `RockRuntime` construction:

- `health_check_retries` must be an integer greater than or equal to `1`.
- `health_check_interval_seconds` must be a number greater than `0`.
- `http_timeout_seconds` must be a number greater than `0`.

Invalid values raise `RockRuntimeConfigError` with a field-specific message,
for example:

- `health_check_retries must be >= 1`
- `health_check_interval_seconds must be > 0`
- `http_timeout_seconds must be > 0`

Validation happens before sandbox startup. `McpEnv(...)` will fail fast because
it constructs `RockRuntime` during initialization.

## Data Flow

1. Caller creates and optionally mutates `RockRuntimeOptions`.
2. Caller passes it to `McpEnv`.
3. `McpEnv` snapshots the options and constructs `RockRuntime(options=...)`.
4. `McpEnv.start()` resolves server configuration as it does today.
5. `RockRuntime.start()` launches the sandbox and eventually calls
   `_health_check()`.
6. `_health_check()` reads the normalized option values for retry count,
   interval, and per-request HTTP timeout.

No lifecycle, auth, server-config resolution, sandbox injection, or release
behavior changes.

## Testing

Add unit coverage for:

- `RockRuntimeOptions()` exposes current defaults.
- `McpEnv(runtime_options=...)` passes option values into its `RockRuntime`.
- Mutating the original options after `McpEnv(...)` does not affect
  `env._rock_runtime.options`.
- `RockRuntime(options=..., health_check_retries=...)` applies flat parameter
  overrides for direct-construction compatibility.
- Invalid option values raise `RockRuntimeConfigError`.
- Existing `McpEnv(servers=...)` and `RockRuntime(...)` tests still pass without
  API changes.

Integration tests do not need to exercise non-default timing because this is
configuration plumbing and runtime validation. Existing real ROCK MCP
integration coverage is sufficient for default behavior.

## Documentation

Update MCP SDK documentation with a small example showing:

```python
options = RockRuntimeOptions()
options.health_check_retries = 12
env = McpEnv(servers=servers, runtime_options=options)
```

Mention that options are snapshotted when passed to `McpEnv`.
