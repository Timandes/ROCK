# rl-rock Development Version Bump Design

## Goal

Update the `rl-rock` Python package development version from
`1.11.0.dev2026071418` to `1.11.0.dev2026071515` for the current MCP fix branch.

## Scope

- Update the `[project].version` value in `pyproject.toml`.
- Update the editable `rl-rock` package version in `uv.lock`.
- Do not change the independent rocklet, sandbox, or TypeScript SDK versions.
- Do not publish a package, create a tag, or create a release.

## Approach

Edit the two version declarations directly. This matches the repository's most
recent version-bump commit and avoids unrelated lock-file regeneration.

## Verification

- Confirm both files contain `1.11.0.dev2026071515` for `rl-rock`.
- Confirm the old `1.11.0.dev2026071418` value is absent from both files.
- Run `uv lock --check` to verify lock-file consistency.
- Review the final diff to ensure no unrelated files are staged.

