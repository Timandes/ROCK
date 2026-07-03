# McpEnv SandboxAware Lifecycle Injection Design

## Context

ROCK owns `rock.sdk.mcp.McpEnv` and `RockRuntime`. `McpEnv` creates
ScaffoldHub data lifecycles through `DataLifecycleFactory`, while
`RockRuntime` creates the ROCK sandbox, writes `/app/mcp-servers.json`, starts
MCP server processes, health-checks SSE endpoints, and stops the sandbox.

ScaffoldHub now exposes `SandboxAware` from `scaffoldhub.tools.base`.
`SandboxAware` is a narrow lifecycle marker whose public contract is only:

```python
def set_sandbox(self, sandbox) -> None:
    ...
```

ScaffoldHub's design states that external launchers, including ROCK
`McpEnv`, should inject the externally created ROCK sandbox into lifecycles
that implement `SandboxAware`. The marker does not ask the launcher to call
lifecycle `before_launch()` methods. Those methods remain lifecycle-internal
compatibility helpers and are not part of the public integration contract.

## Goals

- Automatically inject the ROCK sandbox into ScaffoldHub lifecycles that
  implement `SandboxAware`.
- Run injection after the sandbox is started and `/app/mcp-servers.json` has
  been written.
- Run injection before the user-provided `before_launch(sandbox)` callback.
- Preserve existing `McpEnv.start(before_launch=None)` API shape.
- Keep `RockRuntime` independent from ScaffoldHub data lifecycle concepts.
- Preserve current startup cleanup behavior when injection fails.

## Non-Goals

- Do not call lifecycle `before_launch()` automatically.
- Do not add `prepare`, `before_launch`, or sandbox setup methods to
  `SandboxAware`.
- Do not add lifecycle parameters to `RockRuntime.start()`.
- Do not move ScaffoldHub lifecycle handling into `RockRuntime`.
- Do not change `init`, `dump`, `reset`, `release`, URL generation, auth
  placeholder resolution, or auth lease release semantics.

## Current Flow

Today `McpEnv.start()` resolves server configs and passes the caller's
`before_launch` callback directly into `RockRuntime.start()`.

```text
McpEnv.start(before_launch=user_hook)
  resolve server configs
  RockRuntime.start(resolved_servers, before_launch=user_hook)
    Sandbox.start()
    prepare /app/workspace and /data
    write /app/mcp-servers.json
    user_hook(sandbox)
    launch /app/launch.sh
    health-check SSE endpoints
```

This gives callers one pre-launch hook but requires each caller to hand-write
SandboxAware injection if a lifecycle needs the sandbox.

## Proposed Flow

`McpEnv` will compose an internal pre-launch hook and pass that hook to
`RockRuntime.start()`. `RockRuntime` keeps the same public API and the same
internal sequencing.

```text
McpEnv.start(before_launch=user_hook)
  resolve server configs
  compose McpEnv pre-launch hook
  RockRuntime.start(resolved_servers, before_launch=mcp_env_hook)
    Sandbox.start()
    prepare /app/workspace and /data
    write /app/mcp-servers.json
    mcp_env_hook(sandbox)
      inject sandbox into SandboxAware lifecycles
      user_hook(sandbox)
    launch /app/launch.sh
    health-check SSE endpoints
```

The externally visible order becomes:

1. Sandbox is created and started.
2. Runtime directories are prepared.
3. `/app/mcp-servers.json` is written.
4. `McpEnv` calls `set_sandbox(sandbox)` on every `SandboxAware` lifecycle.
5. The caller's `before_launch(sandbox)` callback runs, if provided.
6. MCP servers are launched and health-checked.

## Component Responsibilities

### `RockRuntime`

`RockRuntime` remains the MCP runtime orchestrator. It abstracts the fixed
runtime sequence for running MCP server configs in a ROCK sandbox:

- create `Sandbox(SandboxConfig(...))`;
- prepare runtime directories;
- render and write MCP server config;
- expose one generic pre-launch hook slot;
- launch `/app/launch.sh`;
- health-check SSE endpoints;
- stop the sandbox during release or failed startup cleanup.

It should not import or reference `DataLifecycle`, `DataLifecycleFactory`, or
`SandboxAware`.

### `McpEnv`

`McpEnv` remains the integration facade between ROCK MCP runtime and
ScaffoldHub resources. It already owns ScaffoldHub auth providers, data
lifecycle creation, placeholder resolution, lifecycle `init/dump/reset`, and
auth lease release. SandboxAware injection belongs here because it is a
ScaffoldHub lifecycle integration concern.

## Detailed Design

### Loading ScaffoldHub Components

`_load_scaffoldhub_components()` currently loads `AuthProvider` and
`DataLifecycleFactory`. It will also attempt to load `SandboxAware` from
`scaffoldhub.tools.base`.

The preferred import source is:

```python
from scaffoldhub.auth import AuthProvider
from scaffoldhub.tools.base import DataLifecycleFactory
```

`SandboxAware` should be imported separately so compatibility handling is
precise:

```python
try:
    from scaffoldhub.tools.base import SandboxAware
except ImportError:
    SandboxAware = None
```

For compatibility with older ScaffoldHub versions, missing `SandboxAware`
should not break `McpEnv` construction. If `AuthProvider` or
`DataLifecycleFactory` cannot be imported, `McpEnv` should keep the existing
clear optional dependency error. If only `SandboxAware` is missing,
`McpEnv` stores `None` and skips automatic injection.

### Construction State

`McpEnv.__init__()` will store the loaded marker class:

```python
self.sandbox_aware_class = sandbox_aware_class
```

No public constructor arguments are added.

### Start Hook Composition

`McpEnv.start(before_launch=None)` will compose a hook before calling
`RockRuntime.start()`:

```python
runtime_before_launch = self._compose_before_launch(before_launch)
urls = await self._rock_runtime.start(
    self.resolved_servers,
    before_launch=runtime_before_launch,
)
```

The composed hook always runs SandboxAware injection first. It then invokes the
caller-provided hook, preserving support for both synchronous and asynchronous
callbacks.

### SandboxAware Injection

`McpEnv` will add a private helper:

```python
def _inject_sandbox_into_lifecycles(self, sandbox: Sandbox) -> None:
    sandbox_aware_class = self.sandbox_aware_class
    if sandbox_aware_class is None:
        return

    for lifecycle in self.data_lifecycles.values():
        if isinstance(lifecycle, sandbox_aware_class):
            lifecycle.set_sandbox(sandbox)
```

The helper does not inspect lifecycle names, does not special-case tools, and
does not call lifecycle `before_launch()`.

### User Hook Invocation

`McpEnv` will mirror the current `RockRuntime` sync/async hook behavior:

```python
result = before_launch(sandbox)
if inspect.isawaitable(result):
    await result
```

This keeps existing callers compatible while guaranteeing that any caller hook
sees lifecycles after sandbox injection.

## Error Handling

If `set_sandbox()` raises, the composed hook raises. `RockRuntime.start()`
already treats hook failures as startup failures:

- it calls `stop()` to clean up the sandbox;
- it logs cleanup failures without masking the original startup error;
- it raises `RockRuntimeError` with the original error chained.

`McpEnv` should not add a second cleanup path.

If the caller's `before_launch()` raises after successful injection, behavior
remains the same as today: startup fails and `RockRuntime` cleans up.

## Compatibility

Existing callers that do not use ScaffoldHub `SandboxAware` continue to work.
Existing callers with a `before_launch(sandbox)` callback continue to receive
the raw ROCK sandbox at the same runtime point, but after automatic lifecycle
injection.

Older ScaffoldHub versions that lack `SandboxAware` keep the previous behavior:
no automatic lifecycle injection is performed, and the caller can still inject
manually from their own `before_launch` callback if needed.

## Testing

Unit tests should cover:

- `McpEnv.start()` injects the sandbox into lifecycles that implement
  `SandboxAware`.
- User `before_launch(sandbox)` runs after SandboxAware injection.
- Non-SandboxAware lifecycles are ignored.
- If `SandboxAware` is unavailable from ScaffoldHub, `McpEnv` still starts
  using the previous behavior.
- If `set_sandbox()` raises, startup fails and the runtime cleanup path runs.
- Existing placeholder resolution, lifecycle `init/dump/reset`, auth lease
  release, and raw sandbox property tests continue to pass.

Focused verification after implementation:

```bash
uv run pytest tests/unit/sdk/mcp/test_mcp_env.py -v
uv run pytest tests/unit/sdk/mcp/test_rock_runtime.py -v
uv run ruff check rock/sdk/mcp tests/unit/sdk/mcp
uv run ruff format rock/sdk/mcp tests/unit/sdk/mcp
```

## Documentation

Update MCP SDK documentation to state:

- `McpEnv` automatically injects the started ROCK sandbox into ScaffoldHub
  lifecycles that implement `SandboxAware`.
- Injection happens after `/app/mcp-servers.json` is written and before the
  caller's `before_launch` callback.
- `McpEnv` does not call lifecycle `before_launch()` methods automatically.

## Risks

- A lifecycle may implement `SandboxAware` and rely on the caller to run an
  additional preparation method. This design intentionally does only dependency
  injection; full lifecycle preparation requires a separate public contract.
- Older ScaffoldHub packages do not export `SandboxAware`. The compatibility
  behavior avoids breaking construction but does not provide injection.
- If multiple lifecycles share mutable sandbox state, they all receive the same
  sandbox object. This matches the external launcher ownership model.
