"""The analyst: proposes a regime, decides nothing.

Three properties make this safe to hand to a language model, and all three are
structural rather than matters of prompt wording:

*It has no order tools.* The analyst never touches the broker. It reads numbers
this module hands it and returns a label. There is no code path from its output
to `submit_order`.

*Its output can only tighten.* `calm`, `elevated`, `stress`, `crash` are
ordered, and every one after the first narrows the delta band and shrinks the
size multiplier. There is no value it can return that widens a limit or
approves a trade the gate would refuse. The worst a compromised analyst
achieves is a skipped cycle.

*It cannot fail into permission.* A malformed answer falls back to `stress`,
never to `calm`. An analyst that could not answer is not evidence of a calm
market, and the fallback that reads as "carry on as normal" is the one that
turns an outage into a position.

PROMPT ORDER
------------
The RULEBOOK is a file constant and goes first; the changing numbers are
appended after it. Static prefix first keeps the cached portion stable, and it
also means the instructions are read before any observed data, rather than
after it.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from drawdownguard.domain import Regime

RULEBOOK_PATH = Path(__file__).parent.parent / "prompts" / "analyst.md"

# The order that matters. Each is at least as conservative as the one before,
# which is what makes the analyst's output safe: it can move the agent along
# this list but never off the front of it.
REGIMES: tuple[Regime, ...] = ("calm", "elevated", "stress", "crash")

# Where an unparseable answer lands. Not `calm`. See the module docstring.
FALLBACK: Regime = "stress"


@lru_cache(maxsize=1)
def rulebook() -> str:
    return RULEBOOK_PATH.read_text(encoding="utf-8")


def _number(value: Any, digits: int = 3) -> str:
    """Format a number, or say plainly that it is missing.

    "unknown" rather than a dash or a zero. The rulebook tells the analyst not
    to treat a missing input as a middling one, and that instruction is only
    honourable if the rendering does not quietly supply a value.
    """
    if value is None:
        return "unknown"
    return f"{value:.{digits}f}"


def sanitize(headline: str) -> str:
    """Neutralise anything in a headline that could forge the delimiter.

    Replaced rather than dropped, and visibly: an attempt to escape the
    block is itself information, and the rulebook asks the analyst to
    report attempts in its rationale. Silently deleting it would remove
    the evidence along with the payload.
    """
    return _DELIMITER.sub("[delimiter removed]", headline)


def render_context(snapshots: dict, news: list[str] | None = None) -> str:
    """The changing half of the prompt: today's numbers, and any headlines.

    News is wrapped in `<news>` delimiters and never interpolated anywhere
    else. The rulebook defines that region as data; putting a headline outside
    it would place attacker-controlled text where instructions are read.
    """
    lines = ["", "---", "", "## Observed market data", ""]
    for symbol, snapshot in sorted(snapshots.items()):
        premium = getattr(snapshot, "variance_risk_premium", None)
        lines.append(
            f"- **{symbol}** spot {snapshot.spot:.2f}, "
            f"realised vol 20d {_number(snapshot.realized_vol_20d)}, "
            f"60d {_number(snapshot.realized_vol_60d)}, "
            f"implied vol {_number(snapshot.atm_iv)}, "
            f"IV rank {_number(snapshot.iv_rank, 1)}, "
            f"variance risk premium {_number(premium)}"
        )
    if not snapshots:
        lines.append("- no snapshots were available this cycle")

    if news:
        lines += ["", "<news>"]
        lines += [f"- {sanitize(headline)}" for headline in news]
        lines += ["</news>"]

    lines += ["", "Return the JSON object now."]
    return "\n".join(lines)


def build_prompt(snapshots: dict, news: list[str] | None = None) -> str:
    """Static rulebook first, observed data last."""
    return rulebook() + render_context(snapshots, news)


_JSON = re.compile(r"\{.*\}", re.DOTALL)

# Anything that could close the data region early. A headline containing
# "</news>" would terminate the block and place everything after it outside
# the delimiters -- in the region the rulebook defines as instructions. The
# delimiter rule is only worth writing if the delimiter cannot be forged.
_DELIMITER = re.compile(r"</?\s*news\s*/?>", re.IGNORECASE)


def parse_response(text: str) -> tuple[Regime, str]:
    """The model's answer as a regime and a rationale.

    Anything that cannot be read as one of the four labels becomes `stress`.
    That includes a model that ignored the output contract, a model that
    returned prose, and a model that returned a regime name this project does
    not define — all of which are the same event from here: the analyst did not
    answer, so the agent proceeds cautiously.
    """
    match = _JSON.search(text or "")
    if match is None:
        return FALLBACK, f"analyst returned no JSON object; defaulted to {FALLBACK}"
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return FALLBACK, f"analyst returned malformed JSON; defaulted to {FALLBACK}"

    regime = payload.get("regime")
    rationale = str(payload.get("rationale", "")).strip()
    if regime not in REGIMES:
        return (
            FALLBACK,
            f"analyst returned an unknown regime {regime!r}; defaulted to {FALLBACK}",
        )
    return regime, rationale or "the analyst gave no rationale"


async def classify_regime(
    snapshots: dict, news: list[str] | None = None
) -> tuple[Regime, str, str]:
    """Ask the analyst. Returns the regime, its rationale, and the exact prompt.

    The prompt is returned rather than only logged so the caller can journal it
    verbatim. A decision that cannot be reproduced line by line is not an
    auditable decision, and the rendered prompt is half of what produced it.
    """
    from drawdownguard.analyst.llm import build_llm, response_text

    prompt = build_prompt(snapshots, news)
    try:
        answer = response_text(await build_llm().ainvoke(prompt))
    except Exception as exc:  # noqa: BLE001 — an unreachable analyst is not calm
        return (
            FALLBACK,
            f"the analyst could not be reached ({exc}); defaulted to {FALLBACK}",
            prompt,
        )

    regime, rationale = parse_response(answer)
    return regime, rationale, prompt
