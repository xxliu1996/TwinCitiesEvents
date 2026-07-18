#!/usr/bin/env python3
"""Deterministically fetch, filter, and dedupe Twin Cities events.

No AI/browsing tools involved - plain HTTP requests against known
structured endpoints (RSS feeds, embedded JSON-LD), parsed with the
standard library. Prints a JSON array of filtered events to stdout.
"""
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
    all_events = dedupe(all_events)
    all_events.sort(key=lambda e: e["start"])
    print(json.dumps(all_events, indent=2))


if __name__ == "__main__":
    main()
