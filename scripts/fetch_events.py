#!/usr/bin/env python3
"""Deterministically fetch, filter, and dedupe Twin Cities events.

No AI/browsing tools involved - plain HTTP requests against known
structured endpoints (RSS feeds, embedded JSON-LD), parsed with the
standard library. Prints a JSON array of filtered events to stdout.
"""
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

KIDS_KEYWORDS = [
    "storytime", "story time", "paws to read", "kids", "kid ", "children",
    "child ", "family storytime", "family fun", "preschool", "toddler",
    "baby", "babies", "teen ", "teens", "tween", "youth ", "school age",
    "read with", "dungeons & dragons for teens", "mario mondays",
    "steam saturday", "design-a-game",
]
ADULT_HINT_KEYWORDS = ["adult", "21+", "wine", "beer", "cocktail", "happy hour"]

BC_LIBRARIES = {
    "Saint Paul Public Library": "sppl",
    "Ramsey County Library": "rclreads",
}
EVENTBRITE_URL = "https://www.eventbrite.com/d/mn--minneapolis/events/"
STILLWATER_ICS_URL = "https://events.discoverstillwater.com/wp-json/apollo/v1/calendar/subscribe/all"
COMO_ZOO_API = "https://comozooconservatory.org/wp-json/tribe/events/v1/events"
DMS_SITES = {
    "Visit Saint Paul": "https://www.visitsaintpaul.com/events-calendar/",
    "Minneapolis.org": "https://www.minneapolis.org/calendar/",
}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def is_kid_event(title, description):
    text = f"{title} {description}".lower()
    if any(k in text for k in ADULT_HINT_KEYWORDS):
        return False
    return any(k in text for k in KIDS_KEYWORDS)


def in_window(start_dt, now, horizon):
    return now <= start_dt <= horizon


def passes_time_rule(start_dt):
    if start_dt.weekday() < 5:  # Mon-Fri
        return start_dt.hour >= 18
    return True  # weekend, any time


def fetch_bibliocommons(system_slug, source_name, now, horizon, max_pages=12):
    events = []
    for page in range(1, max_pages + 1):
        url = f"https://gateway.bibliocommons.com/v2/libraries/{system_slug}/rss/events?page={page}"
        try:
            xml_text = fetch(url)
        except Exception as e:
            print(f"WARN: {source_name} page {page} failed: {e}", file=sys.stderr)
            break
        root = ET.fromstring(xml_text)
        ns = {"bc": "http://bibliocommons.com/rss/1.0/modules/event/"}
        items = root.findall(".//item")
        if not items:
            break
        page_max_date = None
        for item in items:
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            description = re.sub("<[^<]+?>", " ", item.findtext("description", "") or "")
            start_local = item.findtext("bc:start_date_local", namespaces=ns)
            location_el = item.find("bc:location", ns)
            location = location_el.findtext("bc:name", "", ns) if location_el is not None else ""
            if not start_local:
                continue
            start_dt = datetime.fromisoformat(start_local)
            page_max_date = max(page_max_date, start_dt) if page_max_date else start_dt
            if not in_window(start_dt, now, horizon):
                continue
            if is_kid_event(title, description):
                continue
            if not passes_time_rule(start_dt):
                continue
            events.append({
                "title": title,
                "start": start_dt.isoformat(),
                "venue": location,
                "description": description.strip()[:200],
                "url": link,
                "source": source_name,
            })
        if page_max_date and page_max_date > horizon:
            break
    return events


def extract_server_data(html):
    idx = html.index("window.__SERVER_DATA__")
    start = html.index("{", idx)
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(html, start)
    return data


def fetch_eventbrite(now, horizon, max_pages=2):
    events = []
    for page in range(1, max_pages + 1):
        url = EVENTBRITE_URL if page == 1 else f"{EVENTBRITE_URL}?page={page}"
        try:
            html = fetch(url)
            data = extract_server_data(html)
        except Exception as e:
            print(f"WARN: Eventbrite page {page} failed: {e}", file=sys.stderr)
            break
        for bucket in data.get("buckets", []):
            for ev in bucket.get("events", []):
                title = ev.get("name", "")
                description = ev.get("summary", "") or ""
                start_date = ev.get("start_date")
                start_time = ev.get("start_time")
                if not start_date or not start_time:
                    continue
                try:
                    start_dt = datetime.fromisoformat(f"{start_date}T{start_time}")
                except ValueError:
                    continue
                if not in_window(start_dt, now, horizon):
                    continue
                if is_kid_event(title, description):
                    continue
                if not passes_time_rule(start_dt):
                    continue
                primary_venue = ev.get("primary_venue") or {}
                venue = primary_venue.get("name", "")
                region = (primary_venue.get("address") or {}).get("region", "")
                # The Minneapolis events feed occasionally includes
                # out-of-state promoted listings (e.g. eventbrite.co.uk
                # entries with no real venue); require a Minnesota address.
                if region.strip().upper() != "MN":
                    print(f"WARN: dropped non-MN Eventbrite listing: {title!r} (region={region!r})", file=sys.stderr)
                    continue
                events.append({
                    "title": title,
                    "start": start_dt.isoformat(),
                    "venue": venue,
                    "description": description[:200],
                    "url": ev.get("url", ""),
                    "source": "Eventbrite",
                })
    return events


def parse_ics_dt(value):
    value = value.strip()
    if len(value) == 8:  # all-day event, DATE only (no time) - can't apply time-of-day rule
        return None
    return datetime.strptime(value[:15], "%Y%m%dT%H%M%S")


def fetch_stillwater(now, horizon, source_name="Still Water Events"):
    events = []
    try:
        ics_text = fetch(STILLWATER_ICS_URL)
    except Exception as e:
        print(f"WARN: {source_name} failed: {e}", file=sys.stderr)
        return events
    # Minimal VEVENT parser (unfold folded lines per RFC 5545, then split on BEGIN/END).
    unfolded = re.sub(r"\r?\n[ \t]", "", ics_text)
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, re.S):
        fields = {}
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.split(";")[0]  # drop params like DTSTART;VALUE=DATE
            fields[key] = val
        dtstart = fields.get("DTSTART")
        if not dtstart:
            continue
        start_dt = parse_ics_dt(dtstart)
        if start_dt is None:
            continue
        if not in_window(start_dt, now, horizon):
            continue
        title = fields.get("SUMMARY", "").replace("\\,", ",").replace("\\n", " ")
        description = fields.get("DESCRIPTION", "").replace("\\,", ",").replace("\\n", " ")
        if is_kid_event(title, description):
            continue
        if not passes_time_rule(start_dt):
            continue
        events.append({
            "title": title,
            "start": start_dt.isoformat(),
            "venue": fields.get("LOCATION", "").replace("\\,", ","),
            "description": description[:200],
            "url": fields.get("URL", ""),
            "source": source_name,
        })
    return events


def fetch_como_zoo(now, horizon, source_name="Como Zoo Conservatory", max_pages=6):
    events = []
    for page in range(1, max_pages + 1):
        url = (f"{COMO_ZOO_API}?per_page=50&page={page}"
               f"&start_date={now.strftime('%Y-%m-%d')}&end_date={horizon.strftime('%Y-%m-%d')}")
        try:
            raw = fetch(url)
            data = json.loads(raw)
        except Exception as e:
            print(f"WARN: {source_name} page {page} failed: {e}", file=sys.stderr)
            break
        items = data.get("events", [])
        if not items:
            break
        for ev in items:
            start_str = ev.get("start_date")
            if not start_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_str)
            except ValueError:
                continue
            if not in_window(start_dt, now, horizon):
                continue
            title = ev.get("title", "")
            description = re.sub("<[^<]+?>", " ", ev.get("description", "") or "")
            if is_kid_event(title, description):
                continue
            if not passes_time_rule(start_dt):
                continue
            events.append({
                "title": title,
                "start": start_dt.isoformat(),
                "venue": (ev.get("venue") or {}).get("venue", ""),
                "description": description.strip()[:200],
                "url": ev.get("url", ""),
                "source": source_name,
            })
        if page >= data.get("total_pages", page):
            break
    return events


def fetch_dms_event_detail(url, rough_date):
    """Fetch a DMS (Tempest) event detail page and determine title/start/venue/description.

    Prefers the Event JSON-LD block (has full startDate incl. time). Some DMS
    sites (e.g. minneapolis.org) omit startDate from JSON-LD for many listings;
    fall back to the page's plain-text "Date <Mon> <Day> [h:mm AM/PM]" line,
    combining its month/day with rough_date's year (not present in that text).
    """
    html = fetch(url)
    node = None
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        graph = data.get("@graph", [data])
        for candidate in graph:
            if candidate.get("@type") == "Event":
                node = candidate
                break
        if node:
            break

    text = re.sub(r"\s+", " ", re.sub("<[^<]+?>", " ", html))

    def real_venue(title, json_ld_venue):
        # This DMS platform's JSON-LD often sets location.name to the event
        # title itself (bogus placeholder) instead of a real venue. Prefer
        # the plain-text "Event Location <name>" block when that happens.
        if json_ld_venue and json_ld_venue.strip().lower() != title.strip().lower():
            return json_ld_venue
        m = re.search(r"Event Location\s+(.+?)\s+(?:Contact|Address|Buy Tickets|Details)", text)
        return m.group(1).strip() if m else json_ld_venue

    if node and node.get("startDate"):
        try:
            start_dt = datetime.fromisoformat(node["startDate"]).replace(tzinfo=None)
        except ValueError:
            start_dt = None
        if start_dt:
            title = node.get("name", "")
            return {
                "title": title,
                "start": start_dt,
                "venue": real_venue(title, (node.get("location") or {}).get("name", "")),
                "description": node.get("description", "") or "",
                "url": node.get("url", url),
            }

    # Fallback: plain-text "Date Sep 08 7:00 PM" pattern.
    m = re.search(r"Date\s+([A-Za-z]{3})\s+(\d{1,2})\s*(?:-\s*[A-Za-z]{3}\s+\d{1,2})?\s*(\d{1,2}:\d{2}\s*[AP]M)?", text)
    if not m or not m.group(3):
        return None  # no usable time - can't apply the weekday-6pm rule
    try:
        start_dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {rough_date.year} {m.group(3)}", "%b %d %Y %I:%M %p")
    except ValueError:
        return None
    title = (node.get("name") if node else "") or ""
    if not title:
        title_m = re.search(r"<title>([^<|]+)", html)
        title = title_m.group(1).strip() if title_m else ""
    description = (node.get("description") if node else "") or ""
    venue = real_venue(title, (node.get("location") or {}).get("name", "") if node else "")
    return {"title": title, "start": start_dt, "venue": venue, "description": description, "url": url}


def fetch_dms_calendar(base_url, source_name, now, horizon, max_pages=8, max_detail_fetches=60):
    """Fetch a Tempest/DMS-platform events calendar (e.g. visitsaintpaul.com,
    minneapolis.org). Listing pages are server-rendered with a rough date per
    card; only candidates within the window get a detail-page fetch for the
    exact time (required for the weekday-6pm rule)."""
    candidates = []  # (url, rough_date)
    seen_urls = set()
    for page in range(1, max_pages + 1):
        url = base_url if page == 1 else f"{base_url}?page={page}"
        try:
            html = fetch(url)
        except Exception as e:
            print(f"WARN: {source_name} listing page {page} failed: {e}", file=sys.stderr)
            break
        page_candidates = []
        for card in re.split(r"<article\b", html)[1:]:
            url_m = re.search(r'href="(https://[^"]+/(?:events-calendar|calendar)/[a-z0-9-]+/?)"', card)
            date_m = re.search(r'aria-label="From ([A-Za-z]+ \d{1,2}, \d{4})', card)
            if not (url_m and date_m):
                continue
            try:
                rough_date = datetime.strptime(date_m.group(1), "%b %d, %Y")
            except ValueError:
                continue
            page_candidates.append((url_m.group(1), rough_date))
        if not page_candidates:
            break
        page_max_date = max(d for _, d in page_candidates)
        for u, d in page_candidates:
            if u not in seen_urls and now.date() <= d.date() <= horizon.date():
                seen_urls.add(u)
                candidates.append((u, d))
        if page_max_date.date() > horizon.date():
            break

    events = []
    for url, rough_date in candidates[:max_detail_fetches]:
        try:
            detail = fetch_dms_event_detail(url, rough_date)
        except Exception as e:
            print(f"WARN: {source_name} detail fetch failed for {url}: {e}", file=sys.stderr)
            continue
        if not detail:
            continue
        start_dt = detail["start"]
        if not in_window(start_dt, now, horizon):
            continue
        if is_kid_event(detail["title"], detail["description"]):
            continue
        if not passes_time_rule(start_dt):
            continue
        events.append({
            "title": detail["title"],
            "start": start_dt.isoformat(),
            "venue": detail["venue"],
            "description": detail["description"][:200],
            "url": detail["url"],
            "source": source_name,
        })
    return events


def clean_text_fields(events):
    for e in events:
        for field in ("title", "venue", "description"):
            e[field] = html.unescape(e.get(field, "")).strip()
    return events


def dedupe(events):
    seen = set()
    out = []
    for e in events:
        key = (e["title"].strip().lower(), e["start"][:10], e["venue"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def main():
    now = datetime.now()
    horizon = now + timedelta(days=14)
    all_events = []
    for name, slug in BC_LIBRARIES.items():
        all_events.extend(fetch_bibliocommons(slug, name, now, horizon))
    all_events.extend(fetch_eventbrite(now, horizon))
    all_events.extend(fetch_stillwater(now, horizon))
    all_events.extend(fetch_como_zoo(now, horizon))
    for name, url in DMS_SITES.items():
        all_events.extend(fetch_dms_calendar(url, name, now, horizon))
    all_events = clean_text_fields(all_events)
    all_events = dedupe(all_events)
    all_events.sort(key=lambda e: e["start"])
    print(json.dumps(all_events, indent=2))


if __name__ == "__main__":
    main()
