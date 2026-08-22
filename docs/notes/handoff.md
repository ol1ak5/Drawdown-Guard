# How to carry this project on your own

Written 2026-08-22, after Task 4. Everything needed to keep building without
help. Read this once, then work from the plan.

## Where things live, and why `src/`

```
alpaca-hackathon/
├── src/flywheel/          <- all package code
├── tests/                 <- all tests
├── config/                <- risk.yaml, strategy.yaml
├── docs/
│   ├── superpowers/specs/     the design document (what and why)
│   ├── superpowers/plans/     the implementation plan (how, task by task)
│   └── notes/                 logistics.md, this file
└── pyproject.toml
```

`src` is a directory, not a file, and the name is a Python convention called
the **src layout**. The package is `flywheel`; it lives one level down at
`src/flywheel/`.

The reason for the extra level: without it, running `pytest` from the project
root puts the project root on the import path, so `import flywheel` picks up the
local folder whether or not the package is correctly installed. Broken packaging
then passes every test and fails only for whoever installs it. With the src
layout, the local folder is not importable, so the tests import the *installed*
package — you test what actually ships. `uv` installs the project in editable
mode automatically, so edits still take effect immediately.

You will never type `src` in Python. Imports are `from flywheel.domain import
...`, never `from src.flywheel...`.

## Daily commands

```bash
uv run pytest -q                  # all tests, quiet
uv run pytest tests/test_wheel.py -v      # one file, verbose
uv run pytest -k "naked" -v               # tests whose name matches
uv run ruff check src tests               # lint
uv run ruff format src tests              # auto-format
uv add <package>                          # add a dependency
```

If `uv run` complains about a missing package, run `uv sync`.

## The loop for each task

The plan at `docs/superpowers/plans/2026-08-22-flywheel-implementation.md` is
written as numbered tasks, each already containing the test code and the
implementation code. The order matters — later tasks import from earlier ones.

For task N:

1. Read the task in the plan. Note the **Files** and **Interfaces** blocks.
2. Create the test file exactly as written. **Test first, always.**
3. Run it. It must fail, and the failure must be
   `ModuleNotFoundError` — that proves the test is actually reaching for the
   thing you are about to build. A test that passes before you write the code
   is testing nothing.
4. Create the implementation file.
5. Run the tests again. All green.
6. `uv run ruff check src tests && uv run ruff format src tests`
7. Commit with the message given at the end of the task.

Do not skip step 3. It is the only step that proves the test works.

## The plan is a draft, not scripture

Four problems have already been found in it, every one caught because the tests
were written before the code:

- **Net delta sign.** The plan computed a short put's delta contribution as
  `-delta * contracts * 100`, which gives −30 for a −0.30 delta put, while its
  own comment said +30. Position delta is `quantity * per-share delta`, and
  quantity is negative for a short, so the leading minus was wrong. Selling a
  put is a *bullish* position; the sign matters because the whole net-delta
  band depends on it.
- **A test fixture that tested the wrong thing.** The concentration test
  dropped equity to 200,000 but left `peak_equity` at 300,000, which is a 33%
  drawdown. The drawdown check runs first and short-circuits, so the test was
  passing on the wrong rejection reason. `peak_equity` now tracks equity.
- **A fixture that hid every other filter (Task 6).** The default quote was
  1.00/1.10, a 9.5% spread against a 5% limit, so every row was dropped on
  liquidity before the delta or expiry checks could run. One test would have
  failed outright; worse, another would have *passed* while testing nothing.
  The default is now 1.00/1.04.
- **A test named after the wrong thing (Task 7).** "An infeasible problem
  returns empty" — but a zero capital budget is perfectly feasible, since
  selling nothing satisfies every constraint. The test proves the empty
  return, not the infeasible path. Renamed.

A fifth was found by connecting the live paper account rather than by a test.
It is resolved; the section below records what it was and how the fix works,
because the fix is the kind that looks like it does nothing.

Expect more of these.

## Resolved: the vega convention

**First, a correction.** The initial diagnosis of this was wrong. It claimed the
gate and the optimizer disagreed with each other about units and that `max_vega`
was therefore either inert or vetoed everything. Checking against live SPY
quotes showed the code was already self-consistent under one reading. The real
defect was narrower and more insidious: *nothing anywhere said which reading was
intended*, so the consistency was luck, and the next person to touch it — adding
a plausible-looking factor of 100 to make the vega check resemble the delta
check — would have broken it without any test objecting.

Vega is quoted three ways in the wild, and they differ by factors of 100:

| Convention | An ATM SPY put, 9 days out |
|---|---|
| Textbook: per share, per 1.00 of volatility | 44.5 |
| Alpaca's chain: per share, per one point | 0.445 |
| **Ours: per contract, per one point** | **44.5** |

The first and third are numerically identical, because dividing by 100 for the
smaller volatility step and multiplying by 100 shares per contract cancel
exactly. That coincidence is the whole trap. It let the code be right for the
wrong reason, and it means a units bug introduced later would change no number
that any existing test looks at.

**The convention is: dollars lost per one point rise in implied volatility, per
contract.** A portfolio vega of 300 means IV going from 18 to 19 costs $300.

What enforces it now:

- `payoff.py::contract_vega` is the only function anything should call. It
  exists purely to give the units a name — its body is a no-op multiplication,
  and the docstring says so, so nobody deletes it as redundant.
- `bs_vega` is still there, still textbook, and its docstring now warns that it
  is almost never the number you want.
- Two tests in `test_payoff.py` pin it down. One is empirical: it reprices a
  contract at 20 and at 21 volatility and asserts the difference *is* the vega.
  A test that ties a unit to something observable cannot drift.
- `gate.py::_vega` and `model.py` carry comments explaining why, unlike delta,
  they apply no `SHARES_PER_CONTRACT` factor — the exact "fix" that would break
  this.
- `risk.yaml` states the units on `max_vega` and shows the arithmetic behind
  the number: 500 vega against a 30 point IV spike is $15,000, or 1.5% of a
  1,000,000 account, one tenth of the drawdown budget.

Live SPY quotes confirmed the scale: puts 5-14 days out in the 0.15-0.35 delta
band carry 33-46 vega per contract, and our `contract_vega` matched Alpaca's
reported vega to within 0.5% across the band.

**Still open, and worth deciding before D6:** Alpaca returns greeks directly, so
the live agent could use theirs instead of computing Black-Scholes. The argument
against is consistency — the backtest must compute its own, and if the two
sources disagree the backtest stops predicting live behaviour. `payoff.py` is
needed either way for `loss_scenarios` and `assignment_prob`. When the plan's code and the plan's test disagree, the
test is usually closer to the intent — but think it through rather than
patching until green. If you change something, say why in the commit message.

## Done so far

| Task | What it produced | Tests |
|---|---|---|
| 1 | `settings.py` — typed config, refuses to start unless `ALPACA_PAPER_TRADE=true` | 4 |
| 2 | `domain.py` — `WheelState`, `OpenContract`, `ProposedOrder`, `Portfolio`, `Verdict` | 4 |
| 3 | `wheel.py` — the state machine, raises `IllegalTransition` on anything naked | 11 |
| 4 | `risk/gate.py` + `risk/limits.py` + `config/*.yaml` — the deterministic veto | 18 |
| 5 | `optimizer/payoff.py` — Black-Scholes price, delta, vega, assignment proxy, loss scenarios | 9 |
| 6 | `optimizer/candidates.py` — chain rows in, filtered and priced candidates out | 6 |
| 7 | `optimizer/model.py` — the MILP that picks contracts and counts | 8 |
| — | `optimizer/payoff.py::contract_vega` — the project's vega convention, pinned | 2 |

62 tests, all passing. Nothing so far touches the network, so none of it needs
API keys.

The first `pytest` run after installing scipy takes a minute or two while it
warms its caches. Every run after that is a few seconds. This is normal.

## Next, in order

- **Task 8** — historical data for the backtest.

Task 8 is the last of the offline maths, and the only one of them that needs the
network, for its data download.

The first task that needs a working Alpaca account is **Task 9**.

## Still blocked on you

- The final team name (record it in `docs/notes/logistics.md`).
- Two paper accounts at 1,000,000 each: `dev` and `judging`.
- The options level actually granted — check Account → Configure and write the
  number into `logistics.md`.
- Create the public GitHub repository:
  ```bash
  gh repo create flywheel-agent --public --source=. --remote=origin --push
  ```

## Secrets

Never paste API keys into a chat, a commit, or an MCP client config.

```bash
cp .env.example .env        # then fill in the dev account keys
cp .env.example .env.judging   # then fill in the judging account keys
```

Both are gitignored. `.gitignore` has `.env.*` with an exception for
`.env.example`, so a new `.env.anything` cannot be committed by accident.

`settings.py` raises at construction if `ALPACA_PAPER_TRADE` is not `true`, so
the program refuses to start rather than trading real money. The Alpaca CLI has
the same default — paper unless you explicitly opt into live.

## If something breaks

- `ModuleNotFoundError: No module named 'flywheel.x'` right after writing a
  test — expected, that is step 3.
- The same error *after* writing the implementation — check the file is under
  `src/flywheel/`, and that any new sub-package has an `__init__.py`.
- `uv run` cannot find a dependency — `uv sync`.
- `RuntimeWarning: invalid value encountered in reduce` during the optimizer
  tests — comes from inside cvxpy, which sums an uninitialised array purely to
  read its shape. Harmless, and left unsilenced on purpose: a blanket filter
  would also hide a genuine NaN in our own maths.
- Ruff reformats a file you just wrote — that is fine, commit the reformatted
  version. Run `ruff format` before `git add` to avoid the round trip.
