"""The MCP connection, and the claim that the analyst cannot trade.

Most of this file needs a live server and real credentials, so it is marked
`integration`. The toolset-validation tests are not: they are the ones that
catch the mistake that motivated `_validate`, and a check that only runs when
the network is up is a check that stops running.
"""

import pytest

from flywheel.mcp.alpaca_client import (
    FULL_TOOLSETS,
    KNOWN_TOOLSETS,
    MUTATING_TOOLS,
    READ_ONLY_TOOLSETS,
    _validate,
    list_tools,
)


def test_an_unknown_toolset_name_raises_instead_of_narrowing_in_silence():
    """The bug this whole module is shaped around.

    The server ignores a toolset it does not recognise. Asking for `orders`
    (there is no such toolset — it is `trading`) yields a session with no order
    tools and no complaint, which looks exactly like a capability that was
    deliberately withheld.
    """
    with pytest.raises(ValueError, match="orders"):
        _validate("account,stock-data,orders")


def test_both_configured_toolsets_are_names_the_server_defines():
    for toolsets in (READ_ONLY_TOOLSETS, FULL_TOOLSETS):
        for name in toolsets.split(","):
            assert name in KNOWN_TOOLSETS


def test_the_read_only_toolset_does_not_request_trading_or_account():
    """Checked on the string, so it holds even with no server to ask.

    `account` is excluded for `update_account_config`, which can change margin
    and shorting settings. Mostly-read is not read-only.
    """
    requested = set(READ_ONLY_TOOLSETS.split(","))
    assert "trading" not in requested
    assert "account" not in requested


@pytest.mark.integration
async def test_the_server_starts_and_exposes_tools():
    assert len(await list_tools()) > 0


@pytest.mark.integration
async def test_the_full_toolset_can_actually_place_an_option_order():
    """Guards the test below from passing vacuously.

    Proving the analyst has no order tool means nothing unless an order tool is
    something this server can be made to hand out at all. If the server stopped
    shipping `place_option_order`, the read-only assertion would still pass and
    would have stopped testing anything.
    """
    assert "place_option_order" in await list_tools(FULL_TOOLSETS)


@pytest.mark.integration
async def test_the_read_only_toolset_exposes_no_tool_that_changes_anything():
    """The load-bearing one: the analyst physically cannot trade.

    Compared against a named set rather than a substring. `get_orders` contains
    "order" and only reads; `close_all_positions` does not contain it and is
    the most destructive call the server offers. Matching on the substring gets
    both exactly backwards.
    """
    names = set(await list_tools(READ_ONLY_TOOLSETS))
    assert not names & MUTATING_TOOLS
