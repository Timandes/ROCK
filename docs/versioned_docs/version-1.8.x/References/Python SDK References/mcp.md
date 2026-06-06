# MCP SDK

`rock.sdk.mcp` provides `McpEnv`, a small SDK facade for running MCP servers
inside ROCK sandboxes.

## Installation

Install the MCP extra when using ScaffoldHub-backed tool lifecycles:

```bash
pip install --extra-index-url https://artlab.alibaba-inc.com/1/pypi/simple "rl-rock[mcp]"
```

The MCP extra currently supports Python 3.11 and 3.12 because the published
ScaffoldHub package is Python 3.11+ and ROCK officially supports Python 3.10 to
3.12.

## Basic Usage

```python
import asyncio

from rock.sdk.mcp import McpEnv


async def main():
    env = McpEnv(
        servers={
            "calculator": {
                "command": "uvx",
                "args": ["mcp-server-calculator==0.2.0"],
            }
        }
    )

    try:
        await env.start()
        urls = env.get_urls()
        print(urls["calculator"])
    finally:
        if env.is_alive():
            await env.release()


asyncio.run(main())
```

## Lifecycle Data

`McpEnv.init(data)`, `McpEnv.reset()`, and `McpEnv.dump()` delegate tool data
lifecycle work to ScaffoldHub resources. The top-level keys in `data` match the
server or lifecycle type:

```python
env.init({"slack": {"seed_fixture_path": "fixture.json"}})
snapshot = env.dump()
env.reset()
```

`reset()` only resets configured tool data. It does not stop the ROCK sandbox.
Use `await env.release()` to stop the sandbox runtime.

## Launch Hook

`start()` accepts an optional sync or async `before_launch` callback. The
callback receives the raw ROCK `Sandbox` after `/app/mcp-servers.json` is
written and before `/app/launch.sh` starts MCP servers.

```python
async def before_launch(sandbox):
    await sandbox.write_file_by_path(content="ready", path="/data/marker.txt")


await env.start(before_launch=before_launch)
```
