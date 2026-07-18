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

To add a new source, find its RSS/iCal/JSON endpoint (check page source for `rss.php`, `ical_subscribe`, `application/ld+json`, or a `window.__*_DATA__` blob) rather than relying on WebFetch/WebSearch against the rendered page — that's what made earlier runs of this workflow unreliable.
