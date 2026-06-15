# McpEnv Auth Lease Release Design

## Context

ScaffoldHub 0.1.0.dev4 adds a PostgreSQL-backed, lease-aware `AuthProvider`.
When ROCK `McpEnv` resolves MCP server environment placeholders, auth values may
come from a database lease. ROCK must release those active leases when the MCP
environment is released.

This design covers only the ROCK repository changes. ScaffoldHub owns the
database schema, auth borrowing, active lease tracking, and
`AuthProvider.release_active_leases()` implementation.

## Goals

- Upgrade `rl-rock[mcp]` to depend on `scaffoldhub==0.1.0.dev4`.
- Make `McpEnv` explicitly own the `AuthProvider` instance used for both server
  environment resolution and lifecycle construction.
- Call `AuthProvider.release_active_leases()` from `McpEnv.release()`.
- Surface auth lease release failures to callers.
- Preserve the caller's ability to call `release()` again after an auth lease
  release failure.

## Non-Goals

- Do not implement auth borrow or release SQL in ROCK.
- Do not add `McpEnv.__init__` parameters for custom auth providers or data
  lifecycle factories.
- Do not change `reset()` semantics. `reset()` remains data lifecycle cleanup
  only and does not stop the runtime or release auth leases.
- Do not silently support auth providers that lack `release_active_leases()`.
  The new method is a required ScaffoldHub 0.1.0.dev4 contract.

## Architecture

`McpEnv` will load both `DataLifecycleFactory` and `AuthProvider` from
ScaffoldHub. During construction it will create one `AuthProvider` and pass the
same instance into `DataLifecycleFactory`:

```python
self.auth_provider = AuthProvider()
self.data_lifecycle_factory = DataLifecycleFactory(auth_provider=self.auth_provider)
```

This makes auth resource ownership explicit at the ROCK boundary. The factory
still creates tool data lifecycles, but `McpEnv` owns the auth provider whose
leases must be released.

`_server_auth()` will use `self.auth_provider.provide(server_name)` directly.
That keeps server environment placeholder resolution and
`DataLifecycleFactory.create()` on the same `AuthProvider` instance. A lease
borrowed while resolving a server env placeholder will therefore be recorded in
the same provider that `release()` later asks to release active leases.

## Release Semantics

`McpEnv.release()` will perform two cleanup actions:

1. Stop the ROCK runtime if `self.running` is true.
2. Call `self.auth_provider.release_active_leases()`.

The auth release call must not be gated by `self.running`. If the first
`release()` call stops the runtime but fails while releasing auth leases, the
caller must be able to call `release()` again and retry auth lease release even
though the runtime is no longer running.

`release()` will clear `running`, `urls`, and `resolved_servers` in a `finally`
block. This mirrors the existing behavior that a release attempt makes the
runtime facade unusable for server URL access, even if part of cleanup reports
an error.

## Error Handling

Runtime stop failures keep the existing behavior: log a warning and continue.
This preserves current tests and avoids masking auth lease cleanup behind a
sandbox stop failure.

Auth lease release failures are caller-visible. If
`release_active_leases()` raises, `McpEnv.release()` raises:

```python
RuntimeError("Failed to release MCP auth leases")
```

with the original exception chained via `raise ... from error`.

The state cleanup still happens before the exception is raised. The remaining
active lease state is owned by ScaffoldHub `AuthProvider`, so a later
`await env.release()` will call `release_active_leases()` again and allow the
provider to retry any leases it still tracks.

## Data Flow

1. `McpEnv.__init__()` creates `self.auth_provider`.
2. `McpEnv.__init__()` creates `self.data_lifecycle_factory` with that provider.
3. During construction, supported lifecycles are created through the factory.
4. `start()` resolves each server config.
5. `_server_auth()` calls `self.auth_provider.provide(server_name)`.
6. ScaffoldHub may borrow and track a database auth lease.
7. `release()` calls `self.auth_provider.release_active_leases()` on every
   release attempt.

## Tests

Unit tests should cover:

- The fake ScaffoldHub `DataLifecycleFactory` receives the same auth provider
  instance that `McpEnv` stores on `env.auth_provider`.
- `release()` calls `auth_provider.release_active_leases()` on success.
- A second `release()` call still calls `release_active_leases()` when
  `running` is false.
- If `release_active_leases()` raises, `release()` raises `RuntimeError` and
  clears `running`, `urls`, and `resolved_servers`.
- If runtime stop raises, `release()` still calls `release_active_leases()`.
- Existing server env placeholder resolution still uses the provider returned
  auth data.

The fake auth provider in `tests/unit/sdk/mcp/test_mcp_env.py` must implement
the required `release_active_leases()` method to match the ScaffoldHub 0.1.0.dev4
contract.

## Documentation

Update MCP SDK documentation and the client migration guide to state:

- `rl-rock[mcp]` depends on `scaffoldhub==0.1.0.dev4`.
- `await env.release()` stops the ROCK sandbox and releases ScaffoldHub auth
  leases.
- If auth lease release fails, `release()` raises and callers may call it again
  to retry release.

## Verification

Run focused tests:

```bash
uv run pytest tests/unit/sdk/mcp/test_mcp_env.py -v
```

Run formatting and linting for touched files:

```bash
uv run ruff format rock/sdk/mcp/mcp_env.py tests/unit/sdk/mcp/test_mcp_env.py
uv run ruff check rock/sdk/mcp/mcp_env.py tests/unit/sdk/mcp/test_mcp_env.py
```

If dependency metadata changes, run the relevant packaging or lockfile checks
according to the implementation plan.
