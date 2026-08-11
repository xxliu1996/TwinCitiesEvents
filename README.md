# TwinCitiesEvents

A weekly automated digest of local Twin Cities events, filtered for adults and generated
without relying on AI-driven web scraping.

## How it works

1. **Schedule**: a Claude cloud routine fires every Thursday at 6:30 PM America/Chicago.
2. **Fetch**: the routine runs [`scripts/fetch_events.py`](scripts/fetch_events.py), a
   deterministic Python script (standard library only) that pulls events directly from each
   publisher's structured data endpoint — an RSS feed or embedded JSON — rather than scraping
   rendered HTML pages. See [`EventPulisherList.md`](EventPulisherList.md) for the current
   source list and why this approach was necessary.
3. **Filter**: the script applies three rules to every event:
   - **Adult-oriented** — excludes events with kids/family/teen keywords in the title or
     description (storytime, STEAM Saturday, family fun, etc.)
   - **Timing window** — only events in the next 14 days
   - **Time-of-day** — weekday events must start at or after 6:00 PM; weekend events can be
     any time
   - It also drops out-of-state promoted listings that occasionally leak into the Eventbrite
     feed (checked via venue address, not domain).
4. **Write**: the routine writes a dated Markdown file to `events/YYYY-MM-DD-twin-cities-events.md`
   containing a short human-written summary followed by the full event list grouped by date.
5. **Commit & push**: the file is committed and pushed to `main` so the digest history
   accumulates in `events/`.

## Repo layout

| Path | Purpose |
| --- | --- |
| `EventPulisherList.md` | Source list + instructions for adding a new publisher |
| `scripts/fetch_events.py` | Deterministic fetch/filter script run by the routine |
| `events/` | One dated Markdown digest per run |

## Running it manually

```bash
python3 scripts/fetch_events.py
```

Prints a JSON array of filtered events to stdout (title, start time, venue, description, url,
source). Warnings about failed sources go to stderr. The routine takes this JSON and writes the
formatted Markdown digest + summary on top of it.

## Notes on the cloud routine setup

- The routine's environment has a network allowlist that must include `gateway.bibliocommons.com`
  and `www.eventbrite.com` (Claude Code cloud sandboxes proxy all outbound traffic through a
  domain allowlist, separate from any target site's own bot-blocking).
- Git push from the sandbox uses a fine-grained GitHub personal access token (Contents:
  read/write, scoped to this repo only) embedded directly in the routine's push step, because
  the sandbox's default git credential proxy did not have write access to this repo.
