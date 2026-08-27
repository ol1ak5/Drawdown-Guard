"""Regenerate the public status page at docs/index.html.

Run at the end of every cycle, including cycles that traded nothing. The
workflow commits the result alongside the journal and the state snapshot.
"""

from drawdownguard.journal.site import build_site

if __name__ == "__main__":
    written = build_site()
    print(f"wrote {written} ({written.stat().st_size} bytes)")
