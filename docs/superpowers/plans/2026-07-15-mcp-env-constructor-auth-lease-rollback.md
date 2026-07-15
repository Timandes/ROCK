# McpEnv Constructor Auth Lease Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure `McpEnv` immediately releases already-borrowed ScaffoldHub auth leases when lifecycle construction fails, while simplifying `McpEnv.__init__()` and preserving existing public behavior.

**Architecture:** Keep lifecycle construction eager, but move it into a transactional `_create_lifecycles()` helper. The helper builds local result dictionaries, rolls back the shared `AuthProvider` on every `BaseException`, preserves the original construction error when cleanup also fails, and publishes the dictionaries to the instance only after complete success.

**Tech Stack:** Python 3.10–3.12, pytest, ScaffoldHub `AuthProvider`/lifecycle factories, standard-library logging, Ruff.

## Global Constraints

- Create or confirm a GitHub Issue before modifying implementation or test code.
- Keep lifecycle creation in the constructor; do not defer auth borrowing to `start()`.
- Do not change the public `McpEnv` constructor or lifecycle APIs.
- Preserve lifecycle declaration order and create a DataLifecycle before the same server's EnvLifecycle.
- Preserve the original lifecycle construction exception if auth rollback also fails.
- Do not add a context manager, destructor, background renewal, or new ScaffoldHub interface.
- Use Angular/Conventional Commits with English commit messages.
- Do not include `Co-Authored-By` in any commit message.
- Preserve unrelated tracked and untracked workspace changes.

## File Structure

- Modify `rock/sdk/mcp/mcp_env.py`: own the transactional lifecycle construction and constructor-failure auth rollback.
- Modify `tests/unit/sdk/mcp/test_mcp_env.py`: make the ScaffoldHub fakes model auth acquisition and add constructor rollback regression coverage.
- No production files are added, and no public documentation changes are required because the public API and successful-path behavior remain unchanged.

---

### Task 0: Establish GitHub traceability

**Files:**
- No local files modified.

**Interfaces:**
- Consumes: repository rule requiring an Issue before code changes.
- Produces: one GitHub Issue number that the eventual PR can reference with `fixes`, `closes`, `resolves`, or `refs`.

- [ ] **Step 1: Check whether an existing Issue already covers the failure**

Run:

```bash
gh issue list --state open --search 'McpEnv auth lease constructor in:title,body' --limit 20
```

Expected: either one clearly relevant Issue is returned, or no relevant Issue exists.

- [ ] **Step 2: If no relevant Issue exists, obtain explicit user approval and create it**

Use this exact title:

```text
[BUG] Release McpEnv auth leases after constructor failure
```

Use this exact body:

```markdown
## Problem

`McpEnv.__init__()` eagerly creates ScaffoldHub data lifecycles. A lifecycle can borrow an auth lease through `AuthProvider.provide()`, after which a later data or environment lifecycle creation can fail. Because construction does not return an instance, callers cannot invoke `await env.release()`, so earlier leases remain active until expiry.

## Expected behavior

If lifecycle construction fails, `McpEnv` immediately calls `AuthProvider.release_active_leases()` and re-raises the original construction error. A cleanup failure is logged without masking the original error.

## Scope

- Keep lifecycle creation eager.
- Extract lifecycle construction from `McpEnv.__init__()` into a private transactional helper.
- Add unit coverage for data lifecycle failure, environment lifecycle failure, cleanup failure, and `BaseException` rollback.
```

Create `/tmp/mcp-env-auth-lease-issue.md` with exactly the body above using `apply_patch`, then create the Issue only after approval:

```bash
gh issue create --title "[BUG] Release McpEnv auth leases after constructor failure" --body-file /tmp/mcp-env-auth-lease-issue.md
```

Expected: GitHub returns the created Issue URL. Record its number for the PR body; do not insert the number into commit messages.

---

### Task 1: Add constructor rollback regression tests

**Files:**
- Modify: `tests/unit/sdk/mcp/test_mcp_env.py:282-391`
- Test: `tests/unit/sdk/mcp/test_mcp_env.py:473-509`

**Interfaces:**
- Consumes: current fake ScaffoldHub module installation through `reload_mcp_env()`.
- Produces: fakes that expose `provide_calls` and failure modes, plus five tests defining the constructor rollback contract.

- [ ] **Step 1: Make the fake auth provider and data factory model real auth acquisition**

Update `FakeAuthProvider` and `FakeDataLifecycleFactory.create()` to the following behavior:

```python
class FakeAuthProvider:
    def __init__(self):
        self.auth = {
            "slack": {
                "SLACK_MCP_XOXP_TOKEN": "xoxp-test-token",
                "SLACK_MCP_XOXB_TOKEN": "xoxb-test-token",
            }
        }
        self.provide_calls: list[str] = []
        self.release_active_leases_calls = 0

    def provide(self, platform: str) -> dict:
        self.provide_calls.append(platform)
        if platform not in self.auth:
            raise ValueError(f"Unsupported platform: {platform}")
        return self.auth[platform]

    def release_active_leases(self) -> None:
        self.release_active_leases_calls += 1


class FakeDataLifecycleFactory:
    last_auth_provider = None

    def __init__(self, auth_provider=None):
        self.auth_provider = auth_provider or FakeAuthProvider()
        FakeDataLifecycleFactory.last_auth_provider = self.auth_provider
        self.created = {}

    def supports(self, lifecycle_type: str) -> bool:
        return lifecycle_type == "slack"

    def create(self, lifecycle_type: str):
        if lifecycle_type != "slack":
            raise ValueError(f"Unsupported data lifecycle type: {lifecycle_type}")
        self.auth_provider.provide(lifecycle_type)
        lifecycle = RecordingDataLifecycle()
        self.created[lifecycle_type] = lifecycle
        return lifecycle
```

- [ ] **Step 2: Add focused failure-mode fakes**

Add these classes next to the existing factory/provider fakes:

```python
class MultiPlatformAuthProvider(FakeAuthProvider):
    def __init__(self):
        super().__init__()
        self.auth["second"] = {"TOKEN": "second-token"}


class FailingSecondDataLifecycleFactory(FakeDataLifecycleFactory):
    def supports(self, lifecycle_type: str) -> bool:
        return lifecycle_type in {"slack", "second"}

    def create(self, lifecycle_type: str):
        if lifecycle_type == "second":
            self.auth_provider.provide(lifecycle_type)
            raise RuntimeError("failed to create second")
        return super().create(lifecycle_type)


class LifecycleConstructionInterrupted(BaseException):
    pass


class InterruptingEnvLifecycleFactory(FakeEnvLifecycleFactory):
    def create(self, lifecycle_type: str):
        raise LifecycleConstructionInterrupted(f"interrupted while creating {lifecycle_type}")
```

Keep the existing `FailingEnvLifecycleFactory` and `FailingReleaseAuthProvider` unchanged.

- [ ] **Step 3: Allow tests to inject auth and data factory classes**

Replace the helper signatures and assignments with:

```python
def install_fake_scaffoldhub(
    monkeypatch,
    *,
    include_sandbox_aware: bool = True,
    auth_provider_class=FakeAuthProvider,
    data_lifecycle_factory_class=FakeDataLifecycleFactory,
    env_lifecycle_factory_class=FakeEnvLifecycleFactory,
):
    scaffoldhub = ModuleType("scaffoldhub")
    auth = ModuleType("scaffoldhub.auth")
    tools = ModuleType("scaffoldhub.tools")
    base = ModuleType("scaffoldhub.tools.base")
    auth.AuthProvider = auth_provider_class
    base.DataLifecycleFactory = data_lifecycle_factory_class
    base.EnvLifecycleFactory = env_lifecycle_factory_class
    if include_sandbox_aware:
        base.SandboxAware = FakeSandboxAware

    monkeypatch.setitem(sys.modules, "scaffoldhub", scaffoldhub)
    monkeypatch.setitem(sys.modules, "scaffoldhub.auth", auth)
    monkeypatch.setitem(sys.modules, "scaffoldhub.tools", tools)
    monkeypatch.setitem(sys.modules, "scaffoldhub.tools.base", base)


def reload_mcp_env(
    monkeypatch,
    *,
    include_sandbox_aware: bool = True,
    auth_provider_class=FakeAuthProvider,
    data_lifecycle_factory_class=FakeDataLifecycleFactory,
    env_lifecycle_factory_class=FakeEnvLifecycleFactory,
):
    install_fake_scaffoldhub(
        monkeypatch,
        include_sandbox_aware=include_sandbox_aware,
        auth_provider_class=auth_provider_class,
        data_lifecycle_factory_class=data_lifecycle_factory_class,
        env_lifecycle_factory_class=env_lifecycle_factory_class,
    )
    sys.modules.pop("rock.sdk.mcp.mcp_env", None)
    module = importlib.import_module("rock.sdk.mcp.mcp_env")
    return importlib.reload(module)
```

- [ ] **Step 4: Add the failing constructor rollback tests**

Add and update the constructor tests as follows:

```python
def test_mcp_env_owns_auth_provider_and_passes_it_to_lifecycle_factory(monkeypatch):
    mcp_env = reload_mcp_env(monkeypatch)

    env = mcp_env.McpEnv(servers={"slack": slack_server_config()})

    assert isinstance(env.auth_provider, FakeAuthProvider)
    assert FakeDataLifecycleFactory.last_auth_provider is env.auth_provider
    assert env.data_lifecycle_factory.auth_provider is env.auth_provider
    assert env.auth_provider.provide_calls == ["slack"]
    assert env.auth_provider.release_active_leases_calls == 0


def test_mcp_env_constructor_releases_auth_when_later_data_lifecycle_creation_fails(monkeypatch):
    mcp_env = reload_mcp_env(
        monkeypatch,
        auth_provider_class=MultiPlatformAuthProvider,
        data_lifecycle_factory_class=FailingSecondDataLifecycleFactory,
    )

    with pytest.raises(RuntimeError, match="failed to create second"):
        mcp_env.McpEnv(servers={"slack": slack_server_config(), "second": {}})

    auth_provider = FakeDataLifecycleFactory.last_auth_provider
    assert auth_provider.provide_calls == ["slack", "second"]
    assert auth_provider.release_active_leases_calls == 1


def test_mcp_env_constructor_releases_auth_when_environment_lifecycle_creation_fails(monkeypatch):
    mcp_env = reload_mcp_env(
        monkeypatch,
        env_lifecycle_factory_class=FailingEnvLifecycleFactory,
    )

    with pytest.raises(RuntimeError, match="failed to create slack"):
        mcp_env.McpEnv(servers={"slack": slack_server_config()})

    auth_provider = FakeDataLifecycleFactory.last_auth_provider
    assert auth_provider.provide_calls == ["slack"]
    assert auth_provider.release_active_leases_calls == 1


def test_mcp_env_constructor_preserves_creation_error_when_auth_release_fails(monkeypatch, caplog):
    mcp_env = reload_mcp_env(
        monkeypatch,
        auth_provider_class=FailingReleaseAuthProvider,
        env_lifecycle_factory_class=FailingEnvLifecycleFactory,
    )

    with pytest.raises(RuntimeError, match="failed to create slack"):
        mcp_env.McpEnv(servers={"slack": slack_server_config()})

    auth_provider = FakeDataLifecycleFactory.last_auth_provider
    assert auth_provider.release_active_leases_calls == 1
    assert "Failed to release MCP auth leases after lifecycle construction failure" in caplog.text
    assert "database release failed" in caplog.text


def test_mcp_env_constructor_releases_auth_on_base_exception(monkeypatch):
    mcp_env = reload_mcp_env(
        monkeypatch,
        env_lifecycle_factory_class=InterruptingEnvLifecycleFactory,
    )

    with pytest.raises(LifecycleConstructionInterrupted, match="interrupted while creating slack"):
        mcp_env.McpEnv(servers={"slack": slack_server_config()})

    auth_provider = FakeDataLifecycleFactory.last_auth_provider
    assert auth_provider.provide_calls == ["slack"]
    assert auth_provider.release_active_leases_calls == 1
```

Replace the old `test_mcp_env_propagates_registered_environment_lifecycle_creation_failure` with the more specific environment rollback test above.

- [ ] **Step 5: Run the new tests to verify RED**

Run:

```bash
uv run pytest \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_constructor_releases_auth_when_later_data_lifecycle_creation_fails \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_constructor_releases_auth_when_environment_lifecycle_creation_fails \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_constructor_preserves_creation_error_when_auth_release_fails \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_constructor_releases_auth_on_base_exception \
  -v
```

Expected: all four tests fail because `release_active_leases_calls` remains `0` and the cleanup-failure warning is absent. Confirm that failures are assertions about missing rollback, not import or test-fixture errors.

---

### Task 2: Implement transactional lifecycle construction

**Files:**
- Modify: `rock/sdk/mcp/mcp_env.py:85-95`
- Test: `tests/unit/sdk/mcp/test_mcp_env.py`

**Interfaces:**
- Consumes: `self.servers`, `self.auth_provider`, `self.data_lifecycle_factory`, and `self.env_lifecycle_factory` initialized by `McpEnv.__init__()`.
- Produces: `_create_lifecycles(self) -> tuple[dict[str, Any], dict[str, Any]]`, returning fully constructed mappings or re-raising the original failure after auth rollback.

- [ ] **Step 1: Replace the inline constructor loop with one transactional assignment**

Keep `_rock_runtime` initialization in its current order, remove the two empty-dict assignments and inline loop, and use:

```python
self._rock_runtime = RockRuntime(options=_snapshot_runtime_options(runtime_options))
self.data_lifecycles, self.env_lifecycles = self._create_lifecycles()
```

- [ ] **Step 2: Add the minimal `_create_lifecycles()` implementation**

Insert this method immediately before the `sandbox` property:

```python
def _create_lifecycles(self) -> tuple[dict[str, Any], dict[str, Any]]:
    data_lifecycles: dict[str, Any] = {}
    env_lifecycles: dict[str, Any] = {}
    try:
        for lifecycle_type in self.servers:
            if self.data_lifecycle_factory.supports(lifecycle_type):
                data_lifecycles[lifecycle_type] = self.data_lifecycle_factory.create(lifecycle_type)
            if self.env_lifecycle_factory.supports(lifecycle_type):
                env_lifecycles[lifecycle_type] = self.env_lifecycle_factory.create(lifecycle_type)
    except BaseException:
        try:
            self.auth_provider.release_active_leases()
        except BaseException as error:
            logger.warning(
                "Failed to release MCP auth leases after lifecycle construction failure: %s",
                error,
            )
        raise

    return data_lifecycles, env_lifecycles
```

- [ ] **Step 3: Run the constructor tests to verify GREEN**

Run:

```bash
uv run pytest \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_owns_auth_provider_and_passes_it_to_lifecycle_factory \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_constructor_releases_auth_when_later_data_lifecycle_creation_fails \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_constructor_releases_auth_when_environment_lifecycle_creation_fails \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_constructor_preserves_creation_error_when_auth_release_fails \
  tests/unit/sdk/mcp/test_mcp_env.py::test_mcp_env_constructor_releases_auth_on_base_exception \
  -v
```

Expected: all five tests pass. The cleanup-failure test logs the warning while still receiving `failed to create slack`.

- [ ] **Step 4: Run the complete McpEnv unit test file**

Run:

```bash
uv run pytest tests/unit/sdk/mcp/test_mcp_env.py -v
```

Expected: all tests pass with zero failures, including existing release and startup rollback coverage.

- [ ] **Step 5: Run focused lint and formatting checks**

Run:

```bash
uv run ruff check rock/sdk/mcp/mcp_env.py tests/unit/sdk/mcp/test_mcp_env.py
uv run ruff format --check rock/sdk/mcp/mcp_env.py tests/unit/sdk/mcp/test_mcp_env.py
git diff --check
```

Expected: Ruff reports no errors, format check reports both files already formatted, and `git diff --check` produces no output.

- [ ] **Step 6: Review the diff against the approved design**

Run:

```bash
git diff -- rock/sdk/mcp/mcp_env.py tests/unit/sdk/mcp/test_mcp_env.py
git status --short
```

Expected: only the two planned files have new implementation changes; pre-existing unrelated untracked files remain untouched.

- [ ] **Step 7: Commit the tested implementation**

Run:

```bash
git add rock/sdk/mcp/mcp_env.py tests/unit/sdk/mcp/test_mcp_env.py
git commit -m "fix(mcp): release auth leases after constructor failure"
```

Expected: one Conventional Commit containing only the implementation and regression tests, with no `Co-Authored-By` trailer.

---

### Task 3: Run repository-level fast regression verification

**Files:**
- No additional file modifications expected.

**Interfaces:**
- Consumes: committed constructor rollback implementation from Task 2.
- Produces: verification evidence that the change does not regress unrelated fast tests.

- [ ] **Step 1: Run the repository fast-test profile**

Run:

```bash
uv run pytest -m "not need_ray and not need_admin and not need_admin_and_network" --reruns 1
```

Expected: the fast-test profile completes with zero failures. If an unrelated pre-existing failure appears, preserve its full output and separate it from the focused McpEnv result instead of changing unrelated code.

- [ ] **Step 2: Verify the committed scope and commit message**

Run:

```bash
git show --stat --oneline HEAD
git log -1 --format='%B'
git status --short
```

Expected: the implementation commit contains only `rock/sdk/mcp/mcp_env.py` and `tests/unit/sdk/mcp/test_mcp_env.py`; its message is `fix(mcp): release auth leases after constructor failure`, contains no `Co-Authored-By`, and unrelated workspace files remain unmodified.
