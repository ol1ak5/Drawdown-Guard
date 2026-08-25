"""The guards around the analyst.

Five concerns, one module, because they share the same small vocabulary and
are read together when someone asks "what could stop this agent".

THE DISTINCTION THAT MATTERS: veto VERSUS defect
------------------------------------------------
`RiskGateMiddleware` should never fire. The analyst is constructed with a
read-only toolset and has no order tools to call, so in the intended
configuration this code is unreachable.

It is not dead code, and it must not be read as a second risk gate either. It
is a tripwire on the first one. If it ever fires, the toolset was misconfigured
or later widened, and the analyst reached something it should never have been
able to see.

That is why a firing journals at `defect` and not at `veto`. A veto is the risk
gate working: an order was proposed, examined and refused, and the design held.
A defect means the design leaked. Recording both at the same severity would
bury the second in the noise of the first, and the second is the only one that
means something is wrong with the system rather than with a trade.
"""

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

from flywheel.journal import writer

# Tools that change something. Named individually rather than pattern-matched:
# `get_orders` contains "order" and only reads, `close_all_positions` does not
# contain it and is the most destructive call the server offers.
ORDER_TOOLS = frozenset(
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

# The manual override. `touch HALT`, commit, push, and the next scheduled run
# stops before doing anything. It is the one control that works from a phone.
HALT_FILE = "HALT"

END = {"jump_to": "end"}


def halt_file_present(root: Path | str = ".") -> bool:
    return (Path(root) / HALT_FILE).exists()


class RiskGateMiddleware:
    """Refuses any order tool reaching the analyst, and reports it as a defect.

    See the module docstring: this firing is evidence of a configuration
    problem, not of a bad trade.
    """

    def __init__(self, order_tools: frozenset[str] = ORDER_TOOLS) -> None:
        self.order_tools = order_tools

    def wrap_tool_call(self, name: str, arguments: dict, call_next):
        if name not in self.order_tools:
            return call_next(name, arguments)

        reason = (
            f"the analyst attempted to call {name}, which is an order tool. "
            f"It holds a read-only toolset and should not have been able to "
            f"see this. Refusing, and recording a configuration defect."
        )
        writer.write(
            "middleware.order_tool_blocked",
            {"tool": name, "arguments": arguments, "reason": reason},
            severity="defect",
        )
        return {"role": "tool", "name": name, "content": reason, "is_error": True}


class KillSwitchMiddleware:
    """Ends the cycle on a drawdown breach or a HALT file.

    Two ways to stop, deliberately: one the agent works out for itself from the
    account, one a human can trigger without running any code.
    """

    def __init__(self, max_drawdown_pct: float, root: Path | str = ".") -> None:
        self.max_drawdown_pct = max_drawdown_pct
        self.root = root

    def before_agent(self, state: dict) -> dict | None:
        if halt_file_present(self.root):
            writer.write(
                "middleware.halted",
                {"reason": f"{HALT_FILE} file present in the repository root"},
                severity="info",
            )
            return END

        portfolio = state.get("portfolio")
        drawdown = getattr(portfolio, "drawdown_pct", 0.0) if portfolio else 0.0
        if drawdown > self.max_drawdown_pct:
            writer.write(
                "middleware.halted",
                {
                    "reason": (
                        f"drawdown {drawdown:.1f}% exceeds the "
                        f"{self.max_drawdown_pct:.1f}% kill-switch"
                    )
                },
                severity="info",
            )
            return END
        return None


class MarketHoursMiddleware:
    """Ends the cycle when the market is shut or closing early.

    `is_open` is asked of the broker rather than worked out from the clock. A
    local calculation of market hours is wrong on every holiday, and wrong in
    the direction of trading when it should not.
    """

    def __init__(self, clock: dict | None = None) -> None:
        self.clock = clock or {}

    def before_agent(self, state: dict) -> dict | None:
        clock = state.get("clock") or self.clock
        if not clock:
            return None
        if not clock.get("is_open"):
            writer.write(
                "middleware.halted",
                {"reason": f"market closed; next open {clock.get('next_open')}"},
                severity="info",
            )
            return END
        close = str(clock.get("close") or "")
        if close and not close.startswith("16:00"):
            writer.write(
                "middleware.halted",
                {"reason": f"half day, closing {close}"},
                severity="info",
            )
            return END
        return None


class JournalMiddleware:
    """Records every prompt, response and tool call.

    The journal is the only durable account of why the agent did anything, and
    it is what the report is built from. Recording only the interesting events
    would make the record look like an agent that acts constantly and never
    explains itself.
    """

    def after_model(self, request: Any, response: Any) -> None:
        writer.write(
            "model.responded",
            {
                "prompt": str(getattr(request, "prompt", request))[:20000],
                "response": str(getattr(response, "content", response))[:20000],
            },
            severity="info",
        )

    def wrap_tool_call(self, name: str, arguments: dict, call_next):
        result = call_next(name, arguments)
        writer.write(
            "tool.called",
            {"tool": name, "arguments": arguments},
            severity="info",
        )
        return result


class RetryMiddleware:
    """Retries a transient model failure, with backoff, a bounded number of times.

    Bounded because an unbounded retry against a persistently failing model
    turns one broken cycle into a scheduled job that never finishes. Failing
    after three attempts is recoverable; hanging is not.
    """

    def __init__(self, attempts: int = 3, base_delay: float = 0.5) -> None:
        self.attempts = attempts
        self.base_delay = base_delay

    async def wrap_model_call(self, call_next):
        last: Exception | None = None
        for attempt in range(self.attempts):
            try:
                return await call_next()
            except Exception as exc:  # noqa: BLE001 — retried, then surfaced
                last = exc
                writer.write(
                    "model.retry",
                    {"attempt": attempt + 1, "of": self.attempts, "error": str(exc)},
                    severity="info",
                )
                if attempt + 1 < self.attempts:
                    await asyncio.sleep(self.base_delay * (2**attempt))
        raise last  # type: ignore[misc]


def default_stack(max_drawdown_pct: float, root: Path | str = ".") -> list:
    """The guards, in the order they should run.

    Cheapest and most absolute first: a HALT file costs a stat call and
    overrides everything, so asking the broker anything before checking it
    would be work done to reach a decision already made.
    """
    return [
        KillSwitchMiddleware(max_drawdown_pct, root),
        MarketHoursMiddleware(),
        RiskGateMiddleware(),
        JournalMiddleware(),
        RetryMiddleware(),
    ]


def today_is(day: date) -> str:
    return day.isoformat()
