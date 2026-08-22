"""The public status page: one static HTML file, regenerated every cycle.

The submission asks for a live demo URL and this agent has no user interface.
A static page on GitHub Pages answers that without standing up a server that
could be down when a judge clicks the link.

`render_site` is pure and performs no I/O. That is what makes it testable, and
it also makes the published artifact provably read-only: a page generator that
cannot open a socket cannot reach the broker. For the same reason the document
embeds its own CSS and links to nothing external — no CDN, no font, no script.
"""

import html
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from flywheel.journal import writer

# The repository does not exist yet, and a page must never publish a link that
# was guessed. `build_site` supplies this from the environment; unset, the
# footer simply carries no link.
REPOSITORY_URL_VAR = "FLYWHEEL_REPO_URL"
DEFAULT_OUTPUT = Path("docs/index.html")
DEFAULT_SNAPSHOT = Path("data/state/wheels.json")

# Vetoes are the system working as designed; a defect means the risk-gate
# middleware fired, which can only happen if something reached it that never
# should have. The page keeps them apart for the same reason the journal does.
_VERDICT_BY_SEVERITY = {"veto": "rejected", "defect": "defect"}

_STYLE = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0 auto; padding: 2rem;
       max-width: 60rem; }
h1 { font-size: 1.4rem; margin-bottom: .2rem; }
h2 { font-size: 1.05rem; margin-top: 2rem; }
p.lede { margin-top: 0; opacity: .75; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #8884; }
th { font-weight: 600; opacity: .7; }
td.detail { font-family: ui-monospace, monospace; font-size: .85rem; }
tr.rejected td { background: #e5484d22; }
tr.defect td { background: #f5a52322; font-weight: 600; }
.badge { font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; }
footer { margin-top: 2.5rem; font-size: .8rem; opacity: .6; }
.empty { opacity: .6; font-style: italic; }
"""


def entry_from_journal(line: dict) -> dict:
    """Turn one journal line into one page row.

    The journal's shape is not the page's shape, and this is the only place
    that knows both. Keeping the translation here means the journal format can
    change without the template noticing, and the page can never invent a field
    the journal did not record.
    """
    payload = line.get("payload", {})
    detail = payload.get("detail") or payload.get("reason") or ""
    if not detail:
        extras = {
            key: value
            for key, value in payload.items()
            if key not in ("symbol", "action", "regime")
        }
        detail = json.dumps(extras, default=str) if extras else ""
    return {
        "ts": line.get("timestamp", ""),
        "symbol": payload.get("symbol", ""),
        "action": payload.get("action", line.get("event", "")),
        "regime": payload.get("regime", ""),
        "verdict": _VERDICT_BY_SEVERITY.get(line.get("severity", "info"), "approved"),
        "detail": detail,
    }


def _cell(value) -> str:
    """Escape anything on its way into the document. No exceptions."""
    return html.escape("" if value is None else str(value))


def _wheel_rows(wheels: list[dict]) -> str:
    if not wheels:
        return '<tr><td colspan="4" class="empty">No wheels open.</td></tr>'
    rows = []
    for wheel in wheels:
        basis = wheel.get("basis")
        # A wheel in CASH has no basis. Showing a zero would read as a number
        # the agent knows, when in fact it is a number that does not exist yet.
        basis_cell = _cell(basis) if basis is not None else "&mdash;"
        rows.append(
            "<tr>"
            f"<td>{_cell(wheel.get('symbol'))}</td>"
            f"<td>{_cell(wheel.get('leg'))}</td>"
            f"<td>{basis_cell}</td>"
            f"<td>{_cell(wheel.get('cycles'))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _journal_rows(entries: list[dict]) -> str:
    if not entries:
        return '<tr><td colspan="5" class="empty">No cycles recorded yet.</td></tr>'
    ordered = sorted(entries, key=lambda entry: entry.get("ts", ""), reverse=True)
    rows = []
    for entry in ordered:
        verdict = str(entry.get("verdict", ""))
        css_class = verdict if verdict in ("rejected", "defect") else ""
        rows.append(
            f'<tr class="{css_class}">'
            f"<td>{_cell(entry.get('ts'))}</td>"
            f"<td>{_cell(entry.get('symbol'))}</td>"
            f"<td>{_cell(entry.get('action'))}</td>"
            f'<td><span class="badge">{_cell(verdict)}</span></td>'
            f'<td class="detail">{_cell(entry.get("detail"))}</td>'
            "</tr>"
        )
    return "\n".join(rows)


def _source_link(repository_url: str) -> str:
    """A link to the source, or nothing at all.

    Nothing at all is the right answer when the URL is unknown. A guessed link
    on a public page is worse than a missing one: it looks authoritative and
    leads somewhere that is not this project.
    """
    if not repository_url:
        return ""
    return f'<a href="{_cell(repository_url)}">source</a> &middot;\n'


def render_site(
    entries: list[dict],
    wheels: list[dict],
    generated_at: datetime,
    repository_url: str = "",
) -> str:
    """The whole page, as a string. Pure: no files, no clock, no network."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flywheel &mdash; autonomous ETF wheel overlay</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>Flywheel</h1>
<p class="lede">An autonomous ETF wheel overlay on Alpaca paper trading: it
sells cash-secured puts, takes assignment, writes covered calls against the
shares, and repeats. The LLM proposes, the optimizer decides, and a
deterministic risk gate holds veto power.</p>

<h2>Open wheels</h2>
<table>
<thead><tr><th>Symbol</th><th>Leg</th><th>Basis</th><th>Cycles</th></tr></thead>
<tbody>
{_wheel_rows(wheels)}
</tbody>
</table>

<h2>Decisions</h2>
<p class="lede">Refusals are listed alongside fills. A gate that never says no
is not a gate, so the rejections are the evidence, not the omissions.</p>
<table>
<thead><tr><th>Time (UTC)</th><th>Symbol</th><th>Action</th><th>Verdict</th>
<th>Detail</th></tr></thead>
<tbody>
{_journal_rows(entries)}
</tbody>
</table>

<footer>
Generated {_cell(generated_at.strftime("%Y-%m-%d %H:%M"))} UTC &middot;
{_source_link(repository_url)}paper trading only
</footer>
</body>
</html>
"""


def _wheels_from_snapshot(path: Path) -> list[dict]:
    """Read the committed snapshot straight, without opening the database.

    The page is built after the cycle has already exported its state, and going
    through SQLite here would give the generator a writable handle it has no
    reason to hold.
    """
    if not path.exists():
        return []
    snapshot = json.loads(path.read_text())
    return [
        {
            "symbol": symbol,
            "leg": wheel.get("leg"),
            "basis": wheel.get("basis"),
            "cycles": wheel.get("cycle_count", 0),
        }
        for symbol, wheel in sorted(snapshot.items())
    ]


def build_site(
    out_path: Path = DEFAULT_OUTPUT,
    journal_dir: Path = writer.JOURNAL_DIR,
    snapshot: Path = DEFAULT_SNAPSHOT,
    limit: int = 200,
) -> Path:
    """Regenerate the status page from the journal and the state snapshot.

    Runs even on a cycle that traded nothing: "considered and declined" is a
    state worth publishing, and a page that only updates on fills would imply
    the agent was asleep on the days it was most careful.
    """
    entries = [
        entry_from_journal(line)
        for line in writer.read_entries(limit=limit, directory=journal_dir)
    ]
    wheels = _wheels_from_snapshot(Path(snapshot))
    document = render_site(
        entries, wheels, datetime.now(UTC), os.environ.get(REPOSITORY_URL_VAR, "")
    )
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return destination
