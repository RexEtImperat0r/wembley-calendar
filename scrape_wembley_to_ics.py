#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

EVENTS_URL = "https://www.wembleystadium.com/events"
OUTPUT_FILE = "wembley-events.ics"

DATE_RE = re.compile(r"^\d{1,2} [A-Z][a-z]{2} \d{4}$")
TIME_RE = re.compile(r"^(?:\d{1,2}:\d{2}|TBC)$")

NOISE_LINES = {
    "Image",
    "Find Out More",
    "Buy Hospitality",
    "BUY TICKETS",
    "Buy Tickets",
    "Sold out",
}

BAD_PREFIXES = (
    "Find Out More",
    "Buy ",
    "BUY ",
    "Sold out",
    "Image",
    "Experience unforgettable",
    "Past Events",
    "Upcoming Events",
    "All dates",
    "All events",
    "Type of event",
    "By Date",
)

SKIP_TERMS = [
    "away supporters",
    "hospitality",
]


def fetch_lines(url: str) -> list[str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; WembleyCalendarBot/1.0)"
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line]


def parse_events(lines: list[str]) -> list[dict]:
    """
    Parse the Wembley events page text structure:
    DATE
    TIME or TBC
    TITLE
    OPTIONAL subtitle
    """
    events = []
    i = 0

    while i < len(lines):
        if not DATE_RE.match(lines[i]):
            i += 1
            continue

        try:
            event_date = datetime.strptime(lines[i], "%d %b %Y").date()
        except ValueError:
            i += 1
            continue

        j = i + 1
        time_str = "TBC"

        if j < len(lines) and TIME_RE.match(lines[j]):
            time_str = lines[j]
            j += 1

        while j < len(lines) and lines[j] in NOISE_LINES:
            j += 1

        if j >= len(lines):
            i += 1
            continue

        title = lines[j]
        if title.startswith(BAD_PREFIXES):
            i += 1
            continue

        subtitle = None
        k = j + 1
        while k < len(lines) and lines[k] in NOISE_LINES:
            k += 1

        if (
            k < len(lines)
            and not DATE_RE.match(lines[k])
            and not TIME_RE.match(lines[k])
            and not lines[k].startswith(BAD_PREFIXES)
        ):
            subtitle = lines[k]

        events.append(
            {
                "date": event_date,
                "time": time_str,
                "title": title,
                "subtitle": subtitle,
                "location": "Wembley Stadium",
            }
        )

        i = j + 1

    return dedupe_events_by_date(events)


def dedupe_events_by_date(events: list[dict]) -> list[dict]:
    """
    Keep one representative event per date.

    Preference order:
    1. First event on that date whose title/subtitle does not contain skip terms
    2. Otherwise the first event listed for that date
    """
    grouped = defaultdict(list)
    for event in events:
        grouped[event["date"]].append(event)

    deduped = []

    for day in sorted(grouped):
        day_events = grouped[day]

        preferred = None
        for event in day_events:
            combined = f"{event['title']} {event.get('subtitle') or ''}".lower()
            if any(term in combined for term in SKIP_TERMS):
                continue
            preferred = event
            break

        if preferred is None:
            preferred = day_events[0]

        deduped.append(preferred)

    return deduped


def ical_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\n", r"\n")
    )


def fold_ical_line(line: str) -> str:
    """
    Fold long ICS lines.
    """
    if len(line) <= 73:
        return line

    parts = []
    while len(line) > 73:
        parts.append(line[:73])
        line = " " + line[73:]
    parts.append(line)
    return "\r\n".join(parts)


def build_calendar(events: list[dict]) -> str:
    now_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Freyr//Wembley Road Access Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Wembley Stadium Event Days",
        "X-WR-CALDESC:Auto-generated all-day calendar of Wembley Stadium event dates that may affect local access.",
        "X-PUBLISHED-TTL:PT12H",
    ]

    for event in events:
        day = event["date"]
        dtstart = day.strftime("%Y%m%d")
        dtend = (day + timedelta(days=1)).strftime("%Y%m%d")

        uid_source = f"{day.isoformat()}|{event['title']}|{event['subtitle'] or ''}"
        uid = hashlib.sha256(uid_source.encode("utf-8")).hexdigest()[:24] + "@wembley-feed"

        summary = "Possible road closure - Wembley Stadium"

        description_parts = [
            f"Event: {event['title']}",
            f"Time: {event['time']}",
        ]
        if event["subtitle"]:
            description_parts.append(f"Details: {event['subtitle']}")

        description = "\\n".join(ical_escape(part) for part in description_parts)

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_utc}",
            f"DTSTART;VALUE=DATE:{dtstart}",
            f"DTEND;VALUE=DATE:{dtend}",
            fold_ical_line(f"SUMMARY:{ical_escape(summary)}"),
            fold_ical_line(f"DESCRIPTION:{description}"),
            fold_ical_line(f"LOCATION:{ical_escape(event['location'])}"),
            "STATUS:CONFIRMED",
            "TRANSP:TRANSPARENT",
           # Calculate alert time: 09:00 the day before (UTC)
alert_dt = datetime.combine(day - timedelta(days=1), datetime.min.time()) \
           .replace(hour=9)

alert_utc = alert_dt.strftime("%Y%m%dT%H%M%S")

lines.extend([
    "BEGIN:VALARM",
    "ACTION:DISPLAY",
    f"TRIGGER;VALUE=DATE-TIME:{alert_utc}",
    fold_ical_line("DESCRIPTION:Reminder: Wembley Stadium event tomorrow at 09:00"),
    "END:VALARM",
])
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main() -> None:
    lines = fetch_lines(EVENTS_URL)
    events = parse_events(lines)

    if not events:
        raise RuntimeError("No events parsed. The Wembley page structure may have changed.")

    calendar_text = build_calendar(events)
    Path(OUTPUT_FILE).write_text(calendar_text, encoding="utf-8")

    print(f"Parsed {len(events)} unique event dates")
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
