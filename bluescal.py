from datetime import date, datetime
from hashlib import sha256
import os
import re
import tempfile
import time
from threading import RLock
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import icalendar as ical
import recurring_ical_events
import requests
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta

CAL_URL = "https://calendar.google.com/calendar/ical/seattlebluesdancecollective%40gmail.com/public/basic.ics"
CAL_FILE = "bluescal.ics"
CAL_CACHE_TTL_SECONDS = 10
CAL_MAX_BYTES = 64 * 1024 * 1024
CAL_REQUEST_TIMEOUT = (5, 20)
SEATTLE_TZ = ZoneInfo("America/Los_Angeles")

MONTH_EVENTS_DB = {}
CACHE_LOCK = RLock()
MAX_CACHED_MONTHS = 24

ALLOWED_DESCRIPTION_TAGS = {
    "a", "b", "blockquote", "br", "code", "div", "em", "h2", "h3", "h4", "hr", "i",
    "li", "ol", "p", "pre", "small", "span", "strong", "sub", "sup", "time", "u", "ul",
    "wbr",
}
ALLOWED_LINK_SCHEMES = {"http", "https", "mailto"}
URL_PATTERN = re.compile(r'(?<![="\'])(https?://[^\s<>"\']+)(?![="\'])')


def clear_event_caches():
    with CACHE_LOCK:
        MONTH_EVENTS_DB.clear()


def read_cached_calendar():
    with open(CAL_FILE, "rb") as calendar_file:
        return ical.Calendar.from_ical(calendar_file.read())


def write_cached_calendar(calendar_data):
    cache_directory = os.path.dirname(os.path.abspath(CAL_FILE))
    with tempfile.NamedTemporaryFile(dir=cache_directory, delete=False) as temporary_file:
        temporary_file.write(calendar_data)
        temporary_path = temporary_file.name
    os.replace(temporary_path, CAL_FILE)

def refresh():
    cache_is_fresh = (
        os.path.exists(CAL_FILE)
        and os.path.getmtime(CAL_FILE) > time.time() - CAL_CACHE_TTL_SECONDS
    )
    if cache_is_fresh:
        try:
            return read_cached_calendar()
        except (OSError, ValueError):
            pass

    try:
        response = requests.get(CAL_URL, timeout=CAL_REQUEST_TIMEOUT)
        response.raise_for_status()
        calendar_data = response.content
        if not 0 < len(calendar_data) <= CAL_MAX_BYTES:
            raise ValueError(f"Calendar response has invalid size: {len(calendar_data)} bytes")
        calendar = ical.Calendar.from_ical(calendar_data)
        write_cached_calendar(calendar_data)
        return calendar
    except (OSError, requests.RequestException, ValueError):
        if os.path.exists(CAL_FILE):
            return read_cached_calendar()
        raise

def read_events(calendar, month, year):
    month_start = fix_datetime(datetime(year, month, 1))
    month_end = month_start + relativedelta(months=2)
    events = recurring_ical_events.of(calendar).between(month_start, month_end)
    return sorted(
        (event for event in events if event.get("DTSTART")),
        key=lambda event: fix_datetime(event["DTSTART"]),
    )


def sanitize_description(description):
    soup = BeautifulSoup(description, "html.parser")

    for tag in soup.find_all(["iframe", "object", "script", "style"]):
        tag.decompose()

    for text_node in list(soup.find_all(string=URL_PATTERN)):
        if text_node.parent.name == "a":
            continue
        linked_text = URL_PATTERN.sub(r'<a href="\1">\1</a>', str(text_node))
        replacement = BeautifulSoup(linked_text, "html.parser")
        text_node.replace_with(*replacement.contents)

    for tag in list(soup.find_all(True)):
        if tag.name not in ALLOWED_DESCRIPTION_TAGS:
            tag.unwrap()
            continue

        if tag.name != "a":
            tag.attrs = {}
            continue

        href = tag.get("href", "").strip()
        if urlparse(href).scheme.lower() not in ALLOWED_LINK_SCHEMES:
            tag.unwrap()
            continue

        tag.attrs = {"href": href, "rel": "noopener", "target": "_blank"}
        if len(tag.text) > 40 and tag.text == href and "@" not in tag.text:
            tag.string = f"{tag.text[:40]}[...]"

    return str(soup)


def process_events(cal, month: int, year: int):
    month_cache_key = (id(cal), month, year)
    with CACHE_LOCK:
        cached_events = MONTH_EVENTS_DB.pop(month_cache_key, None)
        if cached_events is not None:
            MONTH_EVENTS_DB[month_cache_key] = cached_events
            return cached_events

    events = []
    for cal_event in read_events(cal, month, year):
        uid_val = "|".join([
            str(cal_event.get("UID", cal_event.get("SUMMARY", str(cal_event)))),
            str(cal_event.get("RECURRENCE-ID", "")),
            cache_key(cal_event),
        ])
        uid = sha256(str(uid_val).encode("utf-8")).hexdigest()

        event = {"uid": uid, "title": str(cal_event.get("SUMMARY", ""))}
        local_start = fix_datetime(cal_event["DTSTART"])
        local_end = fix_datetime(cal_event.get("DTEND", local_start))
        event["date"] = local_start.strftime("%Y-%m-%d")
        try:
            cal_event.get("DTSTART").dt.time()
            start_time = local_start.strftime("%-I:%M %p")
            end_time = local_end.strftime("%-I:%M %p")
            if event["date"] != local_end.strftime("%Y-%m-%d") or start_time != end_time:
                event["time"] = f"{start_time} - {end_time}"
            else:
                event["time"] = start_time
        except AttributeError:
            event["time"] = "All Day"

        event["location"] = str(cal_event.get("LOCATION", ""))

        event["description"] = sanitize_description(str(cal_event.get("DESCRIPTION", "")))
        events.append(event)

    with CACHE_LOCK:
        if len(MONTH_EVENTS_DB) >= MAX_CACHED_MONTHS:
            MONTH_EVENTS_DB.pop(next(iter(MONTH_EVENTS_DB)))
        MONTH_EVENTS_DB[month_cache_key] = events
    return events


def cache_key(cal_event):
    return "|".join([
        str(fix_datetime(cal_event["DTSTART"])),
        str(fix_datetime(cal_event.get("DTEND", cal_event["DTSTART"]))),
        str(cal_event.get("LOCATION", "")),
        str(cal_event.get("SUMMARY", "")),
        str(cal_event.get("DESCRIPTION", "")),
    ])

def fix_datetime(value):
    if isinstance(value, ical.prop.vDDDTypes):
        dt = value.dt
    else:
        dt = value
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, datetime.min.time())
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SEATTLE_TZ)
    return dt.astimezone(SEATTLE_TZ)