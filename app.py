import logging
import os
import time
from datetime import date, datetime
from multiprocessing import Process, Queue, freeze_support
from queue import Empty
from threading import Lock

from flask import Flask, abort, jsonify, render_template, request

import bluescal


app = Flask(__name__)
app.logger.setLevel(logging.INFO)

CACHED_CAL = None
SITE_URL = os.environ.get("SITE_URL", "https://seattlebluesdance.com").rstrip("/")
SITE_NAME = "Seattle Blues Dance Collective"
REFRESHER_LOCK = Lock()
SITE_DESCRIPTION = ("Seattle-area blues dance events, classes, music, resources, "
                    "and community information.")


def refresh_calendar(ipc):
    while True:
        try:
            calendar = bluescal.refresh()
        except Exception:
            logging.exception("Calendar refresh failed; retrying in 60 seconds")
            time.sleep(60)
            continue

        try:
            while True:
                try:
                    ipc.get_nowait()
                except Empty:
                    break
            ipc.put(calendar)
        except (EOFError, OSError, ValueError):
            logging.exception("Calendar refresher queue failed; stopping refresher")
            return

        time.sleep(900)


def ensure_calendar_refresher():
    with REFRESHER_LOCK:
        refresher = app.config.get("CALENDAR_REFRESHER")
        if refresher and refresher.is_alive():
            return

        app.logger.info("Starting calendar refresher")
        stale_ipc = app.config.pop("IPC_QUEUE", None)
        if stale_ipc:
            stale_ipc.close()
            stale_ipc.join_thread()

        ipc = Queue()
        app.config["IPC_QUEUE"] = ipc
        refresher = Process(target=refresh_calendar, args=(ipc,), daemon=True)
        refresher.start()
        app.config["CALENDAR_REFRESHER"] = refresher


def get_calendar():
    global CACHED_CAL
    ipc = app.config.get("IPC_QUEUE")
    if ipc:
        latest_calendar = None
        while True:
            try:
                latest_calendar = ipc.get_nowait()
            except Empty:
                break
            except (OSError, ValueError):
                app.logger.exception("Unable to read from calendar refresher")
                break
        if latest_calendar is not None:
            CACHED_CAL = latest_calendar
            bluescal.clear_event_caches()

    if CACHED_CAL is None:
        try:
            CACHED_CAL = bluescal.refresh()
            bluescal.clear_event_caches()
        except Exception:
            app.logger.exception("Unable to load calendar")
    return CACHED_CAL


def render_page(template, page_title, page_description):
    return render_template(
        template,
        page_title=f"{page_title} | SBDC",
        og_title=page_title,
        page_description=page_description,
    )


@app.context_processor
def social_metadata_defaults():
    return {
        "site_name": SITE_NAME,
        "site_url": SITE_URL,
        "site_description": SITE_DESCRIPTION,
        "social_image_url": f"{SITE_URL}/static/images/social-preview.png",
    }


@app.route('/')
def index():
    ensure_calendar_refresher()
    return render_page(
        'index.html',
        "Seattle Blues Dance Events",
        "Upcoming blues dance events, classes, and live music in the Seattle area.",
    )

@app.route('/events.json')
def events_json():
    ensure_calendar_refresher()
    cal = get_calendar()
    if cal is None:
        app.logger.error("No calendar available to read")
        return jsonify([])
    today = datetime.now(bluescal.SEATTLE_TZ).date()
    month_text = request.args.get("month")
    year_text = request.args.get("year")
    month = today.month if month_text is None else request.args.get("month", type=int)
    year = today.year if year_text is None else request.args.get("year", type=int)
    if month is None or year is None or not 1 <= month <= 12 or not 1900 <= year <= 2100:
        abort(400, description="month and year must identify a valid calendar month")

    events = bluescal.process_events(cal, month, year)
    response = jsonify(events)
    response.add_etag()
    response.cache_control.private = True
    response.cache_control.max_age = 300
    return response.make_conditional(request)


@app.route('/events/<event_date>/<uid>')
def shared_event(event_date, uid):
    try:
        parsed_date = date.fromisoformat(event_date)
    except ValueError:
        abort(404)

    ensure_calendar_refresher()
    cal = get_calendar()
    if cal is None:
        abort(503)

    events = bluescal.process_events(cal, parsed_date.month, parsed_date.year)
    event = next(
        (item for item in events if item["date"] == event_date and item["uid"] == uid),
        None,
    )
    if not event:
        abort(404)

    event_title = str(event["title"])
    preview_title = event_title if len(event_title) <= 80 else f"{event_title[:77]}..."
    preview_details = [
        parsed_date.strftime("%A, %B %-d, %Y"),
        str(event["time"]),
    ]
    if event["location"]:
        preview_details.append(str(event["location"]))

    canonical_url = f"{SITE_URL}/events/{event_date}/{uid}"
    preview_description = " | ".join(preview_details)
    if len(preview_description) > 240:
        preview_description = f"{preview_description[:237]}..."
    return render_template(
        'index.html',
        page_title=f"{preview_title} | SBDC",
        page_description=preview_description,
        og_title=preview_title,
        canonical_url=canonical_url,
        shared_event=event,
    )


@app.route('/recurring-events')
def recurring_events():
    return render_page('recurring_events.html', "Recurring Events", "Recurring blues dance classes, socials, and practice events in Seattle.")

@app.route('/about')
def about():
    return render_page('about.html', "About SBDC", "Learn about the Seattle Blues Dance Collective, our mission, and how to contact us.")

@app.route('/instructors')
def instructors():
    return render_page('instructors.html', "Instructor Bios", "Meet blues dance instructors in the Seattle community.")

@app.route('/history')
def history():
    return render_page('history.html', "Blues Resources", "Explore the history, culture, and traditions of Black American blues music and dance.")

@app.route('/music')
def music():
    return render_page('music.html', "Blues Music", "Listen to blues music playlists spanning artists, styles, and eras.")

def shutdown_calendar_refresher():
    refresher = app.config.get("CALENDAR_REFRESHER")
    if refresher and refresher.is_alive():
        refresher.terminate()
        refresher.join(timeout=3)
        if refresher.is_alive():
            app.logger.warning("Calendar refresher did not stop cleanly; killing it")
            refresher.kill()
            refresher.join(timeout=3)

    ipc = app.config.get("IPC_QUEUE")
    if ipc:
        while True:
            try:
                ipc.get_nowait()
            except (Empty, OSError, ValueError):
                break
        ipc.close()
        ipc.join_thread()

    app.config.pop("IPC_QUEUE", None)
    app.config.pop("CALENDAR_REFRESHER", None)


if __name__ == '__main__':
    freeze_support()
    ensure_calendar_refresher()
    app.logger.info("Starting server")
    try:
        app.run(debug=False, host='0.0.0.0', port=8080)
    finally:
        shutdown_calendar_refresher()
