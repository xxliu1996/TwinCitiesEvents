## MN Twin Cities Event Publisher List

This file documents the sources the weekly workflow pulls from. The actual fetching logic
lives in `scripts/fetch_events.py`, which hits each source's structured data endpoint directly
(RSS feed or embedded JSON) instead of scraping rendered HTML — AI-driven page fetches to these
sites are unreliably blocked by anti-bot protection, but plain HTTP requests with a browser
User-Agent are not.

[1] Saint Paul Public Library — RSS: https://gateway.bibliocommons.com/v2/libraries/sppl/rss/events (human page: https://sppl.bibliocommons.com/v2/events)
[2] Ramsey County Library — RSS: https://gateway.bibliocommons.com/v2/libraries/rclreads/rss/events (human page: https://rclreads.bibliocommons.com/v2/events)
[3] Eventbrite (Minneapolis) — embedded JSON (`window.__SERVER_DATA__`) on: https://www.eventbrite.com/d/mn--minneapolis/events/

Removed (unscrapable, verified 2026-07-18):
- Facebook Events (login-walled, no public listing)
- Dakota County Library libcal calendar (fully JS-rendered; RSS endpoint exists at /rss.php but returns zero items for the aggregate calendar)
- Twin Cities AI Tinkerers (direct fetch 403s; no reliable structured endpoint found)
- Saint Paul Parks and Recreation Events Calendar (page is a Drupal shell with no server-rendered event data and no discoverable feed/API)

## Adding a new publisher

Don't just add a human-facing URL to the list above and expect the workflow to scrape it —
that's what made earlier versions of this workflow unreliable (AI-driven page fetches get
blocked by anti-bot protection, and the cloud sandbox's own network allowlist blocks arbitrary
domains too). Instead, find the site's actual structured data endpoint first:

1. **Fetch the raw HTML** of the site's events page with a real browser User-Agent and look for
   one of these patterns (an AI-summarized fetch tool will often hide these — use `curl` or
   view-source directly):
   - `<link rel="alternate" type="application/rss+xml" ... href="...">` — an RSS feed
   - `ical_subscribe.php` or `rss.php` (common on LibCal-based library calendars)
   - `gateway.bibliocommons.com/.../rss/events` (BiblioCommons-based library sites — swap in
     the library system's slug)
   - `<script type="application/ld+json">` containing `"@type": "Event"` — structured event
     data embedded directly in the page
   - `window.__SERVER_DATA__` or similar `window.__*_DATA__` blobs — a JS state object with
     richer event data (dates, times, venue) than the JSON-LD block
2. **Test the endpoint directly** (`curl` it, or fetch it in a script) and confirm it returns
   real event data with a title, date/time, and venue — not an empty shell.
3. **Add a fetch function** to `scripts/fetch_events.py` following the pattern of
   `fetch_bibliocommons()` or `fetch_eventbrite()`: parse the endpoint's format, apply
   `is_kid_event()` / `in_window()` / `passes_time_rule()`, and append to the combined event
   list in `main()`.
4. **Update this file** with the new source's structured endpoint URL (not just the human page)
   and a one-line note on the pattern used.
5. If no structured endpoint can be found after checking the above, don't add the source — note
   it under "Removed (unscrapable)" instead, so future edits don't re-attempt it. The
   environment's network allowlist (see `README.md`) also needs the new domain added, or every
   fetch will fail with a `403` regardless of how good the endpoint is.
