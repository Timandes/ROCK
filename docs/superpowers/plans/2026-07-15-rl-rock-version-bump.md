# rl-rock Development Version Bump Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the `rl-rock` Python package development version from `1.11.0.dev2026071418` to `1.11.0.dev2026071515`.

**Architecture:** Keep `pyproject.toml` as the package metadata source and synchronize the editable `rl-rock` entry in `uv.lock` directly. Avoid regenerating the entire lock file so dependency resolution and unrelated package metadata remain unchanged.

**Tech Stack:** Python packaging metadata, TOML, uv, Git.

## Global Constraints

- Update only the Python package `rl-rock` version declarations.
- Set the exact target version to `1.11.0.dev2026071515`.
- Do not change the independent rocklet, sandbox, or TypeScript SDK versions.
- Do not publish a package, create a tag, or create a release.
- Preserve unrelated tracked and untracked workspace changes.
- Use the commit message `chore: bump version` without `Co-Authored-By`.

---

### Task 1: Synchronize rl-rock package metadata

**Files:**
- Modify: `pyproject.toml:10`
- Modify: `uv.lock:4518`

**Interfaces:**
- Consumes: the current `rl-rock` version `1.11.0.dev2026071418` in both package metadata files.
- Produces: consistent `rl-rock` metadata with version `1.11.0.dev2026071515`.

- [ ] **Step 1: Verify the target version is not already present**

Run:

```bash
rg -n '1\.11\.0\.dev2026071515' pyproject.toml uv.lock
```

Expected: no matches and exit status 1.

- [ ] **Step 2: Update both version declarations**

Apply this exact diff:

```diff
diff --git a/pyproject.toml b/pyproject.toml
@@
-version = "1.11.0.dev2026071418"
+version = "1.11.0.dev2026071515"
diff --git a/uv.lock b/uv.lock
@@
 name = "rl-rock"
-version = "1.11.0.dev2026071418"
+version = "1.11.0.dev2026071515"
 source = { editable = "." }
```

- [ ] **Step 3: Verify exact version synchronization**

Run:

```bash
rg -n '1\.11\.0\.dev2026071515|1\.11\.0\.dev2026071418' pyproject.toml uv.lock
```

Expected: exactly two matches for `1.11.0.dev2026071515`, one in each file, and no match for `1.11.0.dev2026071418`.

- [ ] **Step 4: Verify lock-file consistency and diff scope**

Run:

```bash
uv lock --check
git diff --check
git diff -- pyproject.toml uv.lock
```

Expected: `uv lock --check` and `git diff --check` exit successfully; the diff contains only the two intended version replacements.

- [ ] **Step 5: Commit the version bump**

Run:

```bash
git add pyproject.toml uv.lock
git commit -m "chore: bump version"
```

Expected: one commit containing only `pyproject.toml` and `uv.lock`, with no `Co-Authored-By` trailer.

- [ ] **Step 6: Push the current branch**

Run:

```bash
git push timandes feat/rock-sdk-mcp-migration
```

Expected: the remote branch advances to the version-bump commit.

