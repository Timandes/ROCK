from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def test_mcp_extra_declares_scaffoldhub_dependency():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    mcp_dependencies = pyproject["project"]["optional-dependencies"]["mcp"]

    assert "scaffoldhub>=0.1.0.dev1" in mcp_dependencies
