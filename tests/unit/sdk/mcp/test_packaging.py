from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def test_mcp_extra_declares_scaffoldhub_dependency():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    mcp_dependencies = pyproject["project"]["optional-dependencies"]["mcp"]

    assert "scaffoldhub==0.1.2; python_version >= '3.11' and python_version < '3.13'" in mcp_dependencies


def test_scaffoldhub_resolves_from_artlab_index():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["tool"]["uv"]["sources"]["scaffoldhub"] == {"index": "artlab"}
    assert {
        "name": "artlab",
        "url": "https://artlab.alibaba-inc.com/1/pypi/simple",
        "explicit": True,
    } in pyproject["tool"]["uv"]["index"]
