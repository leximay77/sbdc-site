from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import os
import re
import time
from typing import Tuple
from zoneinfo import ZoneInfo

import icalendar as ical
import recurring_ical_events
import requests
from bs4 import BeautifulSoup

CAL_URL = "https://calendar.google.com/calendar/ical/seattlebluesdancecollective%40gmail.com/public/basic.ics"
CAL_FILE = "bluescal.ics"
CAL_CACHE_TTL_SECONDS = 10 # don't refresh if refreshed in the last X seconds
SEATTLE_TZ = ZoneInfo("America/Los_Angeles")

EVENTS_DB = {}
MONTH_EVENTS_DB = {}


def clear_event_caches():
    global EVENTS_DB, MONTH_EVENTS_DB
    EVENTS_DB = {}
    MONTH_EVENTS_DB = {}

def refresh():
    """ refreshes every 15mins (900s)
        if an error occurs, retries in 1min
        if refreshed in the last TTL sec, use the local file, else fetch
        steady state: refreshing from server every 15min, parsing & caching on-demand
    """
    if os.path.exists(CAL_FILE) and os.path.getmtime(CAL_FILE) > time.time() - CAL_CACHE_TTL_SECONDS:
        with open(CAL_FILE, 'r') as f:
            cal_data = f.read()
    else:
        response = requests.get(CAL_URL)
        if response.status_code == 200 and len(response.text) > 0 and len(response.text) < 1_048_576 * 64: # 64 MiB
            cal_data = response.text
            with open(CAL_FILE, 'w') as f:
                f.write(cal_data)
        else:
            raise ValueError(f"Bad response from {CAL_URL}")
    return ical.Calendar.from_ical(cal_data)

def read_events(calendar, month, year, logger=None):
    month_start = fix_datetime(datetime(year, month, 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    month_end = fix_datetime(month_start + relativedelta(months=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    return sorted(filter(lambda x: x.get("DTSTART"), recurring_ical_events.of(calendar).between(month_start, month_end)),
                  key=lambda x: fix_datetime(x["DTSTART"]))

def process_events(cal, month: int, year: int, do_cache=False, logger=None):
    global EVENTS_DB, MONTH_EVENTS_DB
    month_cache_key = (month, year)
    if month_cache_key in MONTH_EVENTS_DB:
        return MONTH_EVENTS_DB[month_cache_key]

    events = []
    for cal_event in read_events(cal, month, year, logger):
        uid_val = '|'.join([cal_event.get("UID", cal_event.get("SUMMARY", str(cal_event))), str(cal_event.get("RECURRENCE-ID", "")), str(cache_key(cal_event))])
        uid = sha256(str(uid_val).encode("utf-8")).hexdigest()
        # already in cache
        if EVENTS_DB.get(uid):
            events.append(EVENTS_DB[uid])
            continue

        event = {"uid": uid, "title": cal_event.get("SUMMARY", "")}

        # Parse the iCalendar date strings into datetime objects
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

        event["location"] = cal_event.get("LOCATION", "")

        description = str(cal_event.get("DESCRIPTION", ""))
        try:
            # Convert plain text URLs to links
            url_pattern = re.compile(r'(?<![="\'])(https?://[^\s<>"\']+)(?![="\'])')
            description = url_pattern.sub(r'<a href="\1">\1</a>', description)
            soup = BeautifulSoup(description, 'html.parser')
            for link in soup.find_all('a'):
                link['target'] = '_blank'
                link['rel'] = 'noopener'
                if len(link.text) > 40 and link.text == link.get("href", "") and not '@' in link.text:
                    link.string = link.text[:40] + '[...]'
            event["description"] = str(soup)
        except HTMLParser.HTMLParseError as e:
            if logger:
                logger.error("HTML parsing error: %s", e)
            # If parsing fails, use the original description
            event["description"] = escape(description)
        if do_cache:
            EVENTS_DB[uid] = event
        events.append(event)

    MONTH_EVENTS_DB[month_cache_key] = events
    return events


def cache_key(cal_event):
        return '|'.join([
            str(fix_datetime(cal_event["DTSTART"])),
            str(fix_datetime(cal_event.get("DTEND", ""))),
            str(cal_event.get("LOCATION", "")),
            str(cal_event.get("SUMMARY", "")),
            str(cal_event.get("DESCRIPTION", ""))
        ])

def fix_datetime(vddd):
    if type(vddd) is ical.prop.vDDDTypes:
        dt = vddd.dt
    else:
        dt = vddd # either a date or a datetime
    if type(dt) is date:
        dt = datetime.combine(dt, datetime.min.time())
    if not dt.tzinfo:
        return dt.replace(tzinfo=SEATTLE_TZ)
    else:
        return dt.astimezone(SEATTLE_TZ)