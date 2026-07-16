# McpEnv Selective Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional `keys` parameter to `McpEnv.reset()` so omitted keys reset all lifecycles, non-empty keys reset only matching lifecycles, and an empty list is a no-op.

**Architecture:** Keep selection inside `McpEnv.reset()` and iterate the existing `data_lifecycles` mapping in configuration order. Represent an omitted filter with `None`; when a list is supplied, convert it to a set for membership checks while preserving mapping iteration order.

**Tech Stack:** Python 3.10–3.12, asyncio, pytest, Ruff

## Global Constraints

- `reset()` with no argument resets every configured DataLifecycle.
- `reset(["git"])` resets only the matching `git` DataLifecycle.
- `reset([])` is a NOP and calls no DataLifecycle.
- Unknown keys are ignored.
- Existing synchronous and asynchronous lifecycle support, iteration order, exception propagation, and runtime state behavior remain unchanged.
- Work directly on the current `feat/rock-sdk-mcp-migration` branch without creating a worktree or Issue.
- Commit messages use English Conventional Commits and never include `Co-Authored-By`.

---

### Task 1: Add selective DataLifecycle reset

**Files:**
- Modify: `tests/unit/sdk/mcp/test_mcp_env.py:703`
- Modify: `rock/sdk/mcp/mcp_env.py:209`

**Interfaces:**
- Consumes: `McpEnv.data_lifecycles: dict[str, Any]` and lifecycle `reset()` methods that may return awaitables.
- Produces: `async def McpEnv.reset(self, keys: list[str] | None = None) -> None`.

- [ ] **Step 1: Write failing tests for selective, empty, and unknown key filters**

Add these tests after the existing reset delegation test:

```python
def test_mcp_env_reset_only_resets_selected_lifecycles(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    slack_lifecycle = RecordingDataLifecycle()
    git_lifecycle = RecordingDataLifecycle()
    code_executor_lifecycle = AsyncRecordingDataLifecycle()
    env.data_lifecycles = {
        "slack": slack_lifecycle,
        "git": git_lifecycle,
        "code-executor": code_executor_lifecycle,
    }

    asyncio.run(env.reset(keys=["git", "code-executor"]))

    assert slack_lifecycle.reset_calls == 0
    assert git_lifecycle.reset_calls == 1
    assert code_executor_lifecycle.reset_calls == 1


def test_mcp_env_reset_with_empty_keys_is_noop(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle

    asyncio.run(env.reset(keys=[]))

    assert lifecycle.reset_calls == 0


def test_mcp_env_reset_ignores_unknown_keys(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)
    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})
    lifecycle = RecordingDataLifecycle()
    env.data_lifecycles["slack"] = lifecycle

    asyncio.run(env.reset(keys=["unknown"]))

    assert lifecycle.reset_calls == 0
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
uv run pytest \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_reset_only_resets_selected_lifecycles \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_reset_with_empty_keys_is_noop \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_reset_ignores_unknown_keys \
  -v
```

Expected: all three tests fail with `TypeError: McpEnv.reset() got an unexpected keyword argument 'keys'`.

- [ ] **Step 3: Implement the minimal key filter**

Replace `McpEnv.reset()` with:

```python
async def reset(self, keys: list[str] | None = None) -> None:
    """
    Reset configured MCP environment data.

    Args:
        keys: Optional lifecycle names to reset. When omitted, all configured
            data lifecycles are reset. An empty list performs no resets.

    This only delegates to configured data lifecycles. It does not stop the
    ROCK runtime, clear URLs, or change the recorded running state. Unknown
    lifecycle names are ignored.
    """
    selected_keys = None if keys is None else set(keys)
    for lifecycle_type, lifecycle in self.data_lifecycles.items():
        if selected_keys is not None and lifecycle_type not in selected_keys:
            continue
        result = lifecycle.reset()
        if inspect.isawaitable(result):
            await result
```

- [ ] **Step 4: Run the focused reset tests and verify GREEN**

Run:

```bash
uv run pytest tests/unit/sdk/mcp/test_mcp_env.py -k "mcp_env_reset or mixed_sync_and_async_lifecycles" -v
```

Expected: every selected test passes, including the pre-existing no-argument reset tests.

- [ ] **Step 5: Run the full MCP environment unit test file**

Run:

```bash
uv run pytest tests/unit/sdk/mcp/test_mcp_env.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run lint and format checks**

Run:

```bash
uv run ruff check rock/sdk/mcp/mcp_env.py tests/unit/sdk/mcp/test_mcp_env.py
uv run ruff format --check rock/sdk/mcp/mcp_env.py tests/unit/sdk/mcp/test_mcp_env.py
```

Expected: both commands exit successfully with no required changes.

- [ ] **Step 7: Commit the implementation**

```bash
git add rock/sdk/mcp/mcp_env.py tests/unit/sdk/mcp/test_mcp_env.py
git commit -m "feat(mcp): support selective lifecycle reset"
```
