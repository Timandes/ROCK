# ROCK SDK MCP McpEnv Migration Design

## Context

ScaffoldHub currently owns `McpEnv` and `RockRuntime` under `scaffoldhub.sdk`.
`McpEnv` starts MCP servers inside a ROCK sandbox, resolves server auth
placeholders through ScaffoldHub auth/tool resources, and delegates data
lifecycle operations to ScaffoldHub lifecycle implementations.

The desired ownership is:

- ROCK owns the MCP sandbox SDK facade and ROCK runtime integration.
- ScaffoldHub owns tool resources such as auth providers, data lifecycle
  factories, and concrete Slack/Notion lifecycle implementations.
- ScaffoldHub no longer depends on the ROCK SDK after the migration.

## Goals

- Add a new Python SDK package at `rock.sdk.mcp`.
- Move the public `McpEnv` API from ScaffoldHub into `rock.sdk.mcp`.
- Move `RockRuntime`, `RockRuntimeConfig`, runtime errors, and
  `BeforeLaunchHook` into `rock.sdk.mcp`.
- Preserve the current `McpEnv` behavior rather than redesigning the API.
- Make ScaffoldHub an optional dependency for users of `rock.sdk.mcp`.
- Allow ScaffoldHub to remove its `rl-rock` dependency after it stops exporting
  `scaffoldhub.sdk.McpEnv`.

## Non-Goals

- Do not add a top-level `rock.mcp` package.
- Do not redesign `McpEnv` method names, return values, or lifecycle semantics.
- Do not move ScaffoldHub tool implementations into ROCK.
- Do not introduce a compatibility re-export from `scaffoldhub.sdk` back to
  `rock.sdk.mcp`, because that would keep ScaffoldHub dependent on ROCK.
- Do not implement generic data upload APIs in `McpEnv`.

## Proposed Package Layout

Add:

```text
rock/sdk/mcp/
├── __init__.py
├── mcp_env.py
└── rock_runtime.py
```

Public imports:

```python
from rock.sdk.mcp import McpEnv
from rock.sdk.mcp.rock_runtime import RockRuntime, RockRuntimeConfig
```

`rock.sdk.mcp.__init__` exports only the MCP SDK surface needed by callers:

```python
__all__ = ["McpEnv"]
```

## Dependency Direction

The target dependency graph is:

```text
rock.sdk.mcp
  -> rock.sdk.sandbox.client.Sandbox
  -> rock.sdk.sandbox.config.SandboxConfig
  -> rock.actions.Command
  -> scaffoldhub.tools.base.DataLifecycleFactory
```

ScaffoldHub keeps:

```text
scaffoldhub.auth
scaffoldhub.tools.base
scaffoldhub.tools.slack
scaffoldhub.tools.notion
```

ROCK should add an optional extra:

```toml
[project.optional-dependencies]
mcp = [
    "scaffoldhub>=0.1.0",
]
```

The base `rl-rock` install should not force ScaffoldHub onto every user.
Callers that import or instantiate `McpEnv` without ScaffoldHub installed should
receive a clear import error telling them to install `rl-rock[mcp]` or
`scaffoldhub`.

## McpEnv Behavior

`McpEnv` remains a thin facade with the existing ScaffoldHub semantics.

### Construction

- `McpEnv(servers=None)` uses `{}`.
- `servers` must be a dict or `None`; otherwise raise
  `TypeError("servers must be a dict")`.
- Store a defensive copy in `self.servers`.
- Initialize:
  - `self.running = False`
  - `self.urls = {}`
  - `self.resolved_servers = {}`
  - `self.data_lifecycle_factory = DataLifecycleFactory()`
  - `self.data_lifecycles` for server keys supported by the factory
  - `self._rock_runtime = RockRuntime()`

### Sandbox Access

Expose `sandbox` as a property returning the raw ROCK `Sandbox | None` from the
runtime. This preserves the temporary low-level escape hatch used by
`before_launch` integrations.

### Start

`await start(before_launch=None)` should:

- Set `running` to `False`.
- Clear `urls`.
- Resolve each server config into `resolved_servers`.
- Replace full-string env placeholders such as `${SLACK_MCP_XOXP_TOKEN}` with
  auth values from the ScaffoldHub auth provider when available.
- Leave partial templates, unknown placeholders, non-string values, and
  non-dict server configs unchanged.
- Delegate sandbox startup to `RockRuntime.start`.
- Set `urls` and `running=True` only after successful launch and health checks.

### Data Lifecycle

`init(data)`:

- Requires `data` to be a dict.
- Only dispatches keys present in both `data_lifecycles` and `data`.
- Requires intersection values to be dicts.
- Does not store `self.data`.

`reset()`:

- Calls `reset()` on every configured lifecycle.
- Does not stop the ROCK runtime.
- Does not clear `urls`, `resolved_servers`, or `running`.

`dump()`:

- Calls `dump()` on every configured lifecycle.
- Returns only non-empty lifecycle dumps.
- Does not require a prior `init()`.

`release()`:

- Stops the ROCK runtime only when `running` is true.
- Logs runtime stop failures.
- Always sets `running=False`, clears `urls`, and clears `resolved_servers`.
- Preserves lifecycle instances and leaves data cleanup to explicit `reset()`.

## RockRuntime Behavior

`RockRuntime` also preserves the current ScaffoldHub implementation.

`RockRuntimeConfig.from_env()` reads:

- `ROCK_API_KEY` and `ROCK_USER_ID` as required values.
- `ROCK_BASE_URL`, defaulting to `https://xrl.alibaba-inc.com`.
- `ROCK_SANDBOX_IMAGE`, defaulting to
  `rock-registry.cn-hangzhou.cr.aliyuncs.com/envs/mcp-atlas-local:v0.4.0`.
- `ROCK_EXPERIMENT_ID`, defaulting to `mcpenv`.
- `ROCK_CLUSTER`, defaulting to `nt-a`.
- `ROCK_SANDBOX_CPUS`, defaulting to `4`.
- `ROCK_SANDBOX_MEMORY`, defaulting to `8g`.
- `ROCK_AUTO_CLEAR_SECONDS`, defaulting to `3600`.

`start(servers, before_launch=None)` should:

- Reject repeated start attempts.
- Create `Sandbox(SandboxConfig(...))` using ROCK SDK.
- Start the sandbox.
- Create `/app/workspace` and `/data`.
- Write `/app/mcp-servers.json` with `{"mcpServers": servers}`.
- Run the optional sync or async `before_launch(sandbox)` callback.
- Execute `bash /app/launch.sh > /tmp/launch.log 2>&1 &`.
- Health-check each server's SSE proxy URL.
- Return `{server_name: sse_url}`.
- Stop the sandbox on startup failure and preserve the original startup error
  as the cause.

`stop()` clears runtime state and stops the sandbox when present.

Diagnostic helpers such as `dump_sandbox_logs`, `upload_file`, and `read_file`
should move with `RockRuntime` because they operate on the owned sandbox.

## ScaffoldHub Changes

After ROCK gains `rock.sdk.mcp`, ScaffoldHub should:

- Remove `rl-rock>=...` from its project dependencies.
- Remove or stop exporting `scaffoldhub.sdk.McpEnv`.
- Remove or move `scaffoldhub.sdk.rock_runtime`.
- Keep `DataLifecycle`, `DataLifecycleFactory`, `AuthProvider`, and concrete
  lifecycle implementations.
- Update examples and docs from:

```python
from scaffoldhub.sdk import McpEnv
```

to:

```python
from rock.sdk.mcp import McpEnv
```

No compatibility re-export should be added in ScaffoldHub, because it would
reintroduce a package-level cycle.

## Testing

Move ScaffoldHub MCP SDK tests into ROCK:

```text
tests/unit/sdk/mcp/test_mcp_env.py
tests/unit/sdk/mcp/test_rock_runtime.py
tests/integration/sdk/mcp/test_mcp_env_rock_integration.py
```

Unit tests should avoid real ROCK services by using fake runtime or fake
sandbox objects. They should cover:

- `McpEnv` constructor validation.
- Server env placeholder resolution.
- Unavailable auth keeps placeholders unchanged.
- `init`, `reset`, `dump`, and `release` behavior.
- `before_launch` signature compatibility.
- Raw sandbox property exposure.
- `RockRuntimeConfig.from_env()` required and default values.
- MCP server JSON rendering.
- Server URL construction.
- Repeated runtime start rejection.
- Startup cleanup and original error preservation.

Integration tests that require real ROCK services should remain marked so they
do not run in the fast default test set unless the environment is prepared.

## Verification

Expected verification commands after implementation:

```bash
uv run pytest tests/unit/sdk/mcp -v
uv run ruff check rock/sdk/mcp tests/unit/sdk/mcp
uv run ruff format rock/sdk/mcp tests/unit/sdk/mcp
```

If ScaffoldHub changes are made in a separate repository, run its lifecycle
tests there after removing `rl-rock` from dependencies.

## Migration Risks

- If `scaffoldhub` is installed with a version that lacks
  `scaffoldhub.tools.base.DataLifecycleFactory`, `McpEnv` cannot create
  lifecycles. The import error should identify the missing optional dependency.
- If ScaffoldHub keeps `scaffoldhub.sdk.McpEnv` as a re-export, package
  dependency cycles may persist. The migration should avoid that compatibility
  path.
- `RockRuntimeConfig` environment variable names are currently inherited from
  ScaffoldHub. This migration keeps them unchanged to preserve behavior.

## Release Coordination

- ROCK adds `scaffoldhub>=0.1.0` to the `mcp` optional extra.
- ROCK releases `rock.sdk.mcp` before ScaffoldHub removes its SDK facade.
- ScaffoldHub repository edits are a separate implementation unit from this
  ROCK repository change. They remove the `rl-rock` dependency and update
  ScaffoldHub docs/tests after the ROCK API is available.
