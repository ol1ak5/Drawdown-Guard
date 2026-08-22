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

Two real bugs were already found in it while implementing Task 4, both caught
because the tests were written before the code:

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

Expect more of these. When the plan's code and the plan's test disagree, the
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

46 tests, all passing. Nothing so far touches the network, so none of it needs
API keys.

The first `pytest` run after installing scipy takes a minute or two while it
warms its caches. Every run after that is a few seconds. This is normal.

## Next, in order

- **Task 6** — candidate construction: turn an option chain into `ProposedOrder`s.
- **Task 7** — the CVXPY optimizer that picks among candidates.
- **Task 8** — historical data for the backtest.

Tasks 5 to 8 are all offline maths. They need no API keys and no network except
Task 8's data download, so they can be done at any time.

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
- Ruff reformats a file you just wrote — that is fine, commit the reformatted
  version. Run `ruff format` before `git add` to avoid the round trip.
