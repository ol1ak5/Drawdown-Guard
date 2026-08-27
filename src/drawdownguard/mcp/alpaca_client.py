"""Connection to the Alpaca MCP server.

Credentials are passed through the child process environment. They are never
written to a file inside this repository, and never appear in an argument
vector, where any other process on the machine could read them out of `ps`.

WHY THE TOOLSET NAMES ARE VALIDATED
-----------------------------------
The server selects which tools to expose from `ALPACA_TOOLSETS`, and **an
unrecognised name is silently ignored**. That failure mode is the reason
`_validate` exists rather than passing the string straight through.

The plan for this task asked for a toolset called `orders`. There is no such
toolset — the real name is `trading`. Asking for it produced a session with no
order tools and no error, and the test that was supposed to prove the analyst
cannot trade passed for the wrong reason: not because the tools had been
withheld, but because nobody had asked for them correctly in the first place.
A security property that holds by typo is not a security property.

So the names are checked against the set the server actually defines, and an
unknown one raises. A misconfiguration that would otherwise widen or narrow the
tool surface in silence becomes a crash at startup instead.

WHAT "READ ONLY" MEANS HERE
---------------------------
`READ_ONLY_TOOLSETS` deliberately excludes `account`. That toolset is mostly
reads, but it carries `update_account_config`, which can change margin and
shorting settings — a write. Excluding it makes the read-only claim literally
true: every tool in the analyst's session only reads.

The analyst does not lose the account context. It never needed a tool for it:
the agent builds the account snapshot itself, through a full session, and hands
it to the analyst as text. Data in the prompt, not a capability in its hands.
"""

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from drawdownguard.settings import get_settings

# The toolsets the server defines. Mirrored from alpaca_mcp_server.toolsets so a
# typo is caught here rather than silently changing what the agent can do.
KNOWN_TOOLSETS = frozenset(
    {
        "account",
        "trading",
        "watchlists",
        "assets",
        "stock-data",
        "crypto-data",
        "options-data",
        "corporate-actions",
        "news",
        "fixed-income-data",
        "index-data",
        "locates",
    }
)

# Every tool the analyst may hold. No `account` (it carries
# update_account_config) and no `trading`. See the module docstring.
READ_ONLY_TOOLSETS = "assets,stock-data,options-data,news"

# What the agent itself runs with. `trading` is what makes an order possible,
# and nothing reaches it except through the risk gate.
FULL_TOOLSETS = "account,assets,stock-data,options-data,news,trading"

# Tools that change something. Named individually rather than pattern-matched:
# `get_orders` contains "order" and is a read, `close_all_positions` does not
# and is the most destructive call on the server. A substring test would get
# both backwards.
MUTATING_TOOLS = frozenset(
    {
        "place_stock_order",
        "place_crypto_order",
        "place_option_order",
        "replace_order_by_id",
        "cancel_order_by_id",
        "cancel_all_orders",
        "close_position",
        "close_all_positions",
        "exercise_options_position",
        "do_not_exercise_options_position",
        "update_account_config",
    }
)


def _validate(toolsets: str) -> str:
    """Reject a toolset name the server does not define.

    The server ignores what it does not recognise, so without this an
    unrecognised name reads as "that capability was withheld" when what
    actually happened is "that capability was never requested".
    """
    requested = [name.strip() for name in toolsets.split(",") if name.strip()]
    unknown = sorted(set(requested) - KNOWN_TOOLSETS)
    if unknown:
        raise ValueError(
            f"unknown Alpaca toolset(s): {', '.join(unknown)}. "
            f"The server silently ignores these, so the session would come up "
            f"with fewer tools than intended and no error. "
            f"Known toolsets: {', '.join(sorted(KNOWN_TOOLSETS))}"
        )
    return ",".join(requested)


def _server_command() -> str:
    """The server executable that belongs to the interpreter now running.

    Resolved next to `sys.executable` rather than launched through `uv run`,
    which would re-resolve the environment at call time and could start a
    different build of the server than the one these tests checked.
    """
    candidate = Path(sys.executable).parent / "alpaca-mcp-server"
    if not candidate.exists():
        raise FileNotFoundError(
            f"alpaca-mcp-server not found at {candidate}. "
            f"Install it into this environment: uv pip install alpaca-mcp-server"
        )
    return str(candidate)


def _server_params(toolsets: str) -> StdioServerParameters:
    settings = get_settings()
    return StdioServerParameters(
        command=_server_command(),
        args=[],
        env={
            **os.environ,
            "ALPACA_API_KEY": settings.alpaca_api_key,
            "ALPACA_SECRET_KEY": settings.alpaca_secret_key,
            "ALPACA_PAPER_TRADE": "true",
            "ALPACA_TOOLSETS": _validate(toolsets),
        },
    )


@asynccontextmanager
async def alpaca_session(toolsets: str = FULL_TOOLSETS):
    """A connected session over the given toolsets."""
    async with stdio_client(_server_params(toolsets)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def list_tools(toolsets: str = FULL_TOOLSETS) -> list[str]:
    async with alpaca_session(toolsets) as session:
        result = await session.list_tools()
        return [tool.name for tool in result.tools]


def _unwrap(result: Any, name: str) -> Any:
    """Pull the payload out of a tool result.

    The server answers in whichever of two shapes fits the tool, so both are
    handled: `structuredContent` when the response has a schema, and a text
    block otherwise. The text is usually JSON but is not guaranteed to be, so a
    decode failure returns the string rather than raising — a tool that answers
    in prose is not a broken tool.
    """
    if getattr(result, "isError", False):
        raise RuntimeError(f"MCP tool {name} failed: {result.content}")
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    if not result.content:
        return None
    payload = getattr(result.content[0], "text", None)
    if payload is None:
        return result.content[0]
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return payload


async def call_tool(
    name: str, arguments: dict[str, Any], toolsets: str = FULL_TOOLSETS
) -> Any:
    """Call one tool on a session of its own.

    One session per call is deliberate at this layer: it keeps the call site
    honest about which toolset a given tool needed. Anything making several
    calls in a row should open `alpaca_session` once and reuse it.
    """
    async with alpaca_session(toolsets) as session:
        return _unwrap(await session.call_tool(name, arguments), name)
