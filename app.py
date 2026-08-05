import os
import time
import logging
from datetime import date
from multiprocessing import Process, Queue, freeze_support

from flask import Flask, abort, jsonify, render_template, request

import bluescal


app = Flask(__name__)
app.secret_key = os.urandom(32)
app.logger.setLevel(logging.INFO)

CACHED_CAL = None
SITE_URL = os.environ.get("SITE_URL", "https://seattlebluesdance.com").rstrip("/")
SITE_NAME = "Seattle Blues Dance Collective"
SITE_DESCRIPTION = ("Seattle-area blues dance events, classes, music, resources, "
                    "and community information.")


def refresh_calendar(ipc):
    while True:
        while not ipc.empty(): # drain IPC
            try:
                ipc.get_nowait()
            except:
                continue
        try:
            ipc.put(bluescal.refresh())
            time.sleep(900)
        except Exception as e:
            time.sleep(60)


def ensure_calendar_refresher():
    if not app.config.get('REFRESHING'):
        app.logger.info("Starting refresher")
        ipc = app.config.get('IPC_QUEUE')
        if not ipc:
            ipc = Queue()
            app.config['IPC_QUEUE'] = ipc
        refresher = Process(target=refresh_calendar, args=(ipc,), daemon=True)
        refresher.start()
        app.config['REFRESHING'] = True


def get_calendar():
    global CACHED_CAL
    ipc = app.config.get("IPC_QUEUE")
    if ipc:
        try:
            CACHED_CAL = ipc.get_nowait()
            bluescal.clear_event_caches()
        except Exception:
            pass

    if not CACHED_CAL:
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
    if not cal:
        app.logger.error("No calendar available to read")
        return jsonify([])
    today = date.today()
    month = request.args.get("month", default=today.month, type=int)
    year = request.args.get("year", default=today.year, type=int)
    do_cache = (
        # asking for a nearby month (one back, three forward)
        (year == today.year and month - today.month in range(-1, 4)) or
        # asking for last year december and it's january
        (today.year - year == 1 and today.month == 1 and month == 12) or
        # asking for next year and it's <= three forward
        (year - today.year == 1 and ((today.month + 3) % 12) >= month))
    events = bluescal.process_events(cal, month, year, do_cache, app.logger)
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
    if not cal:
        abort(503)

    events = bluescal.process_events(cal, parsed_date.month, parsed_date.year, logger=app.logger)
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

if __name__ == '__main__':
    freeze_support()

    app.logger.info("Starting refresher")
    ipc = Queue()
    app.config['IPC_QUEUE'] = ipc
    refresher = Process(target=refresh_calendar, args=(ipc,), daemon=True)
    refresher.start()
    app.config['REFRESHING'] = True
    app.logger.info("Starting server")
    try:
        app.run(debug=False, host='0.0.0.0', port=8080)
    except KeyboardInterrupt:
        app.logger.info("Shutting down gracefully...")
        # First terminate the process
        refresher.terminate()
        refresher.join(timeout=3)
        if refresher.is_alive():
            app.logger.warning("Refresher process did not terminate cleanly, forcing...")
            refresher.kill()  # Force kill if still alive
        # Clear the queue before closing
        while not ipc.empty():
            app.logger.info("Clearing IPC (may log errors)")
            try:
                _, err = ipc.get_nowait()
                if err:
                    app.logger.error(err)
            except:
                continue
        ipc.close()
        ipc.join_thread()  # Wait for the queue's feeder thread to finish
        del app.config['IPC_QUEUE']  # Remove from app config
        del app.config['REFRESHING']  # Remove from app config
        exit(0)
    except Exception as e:
        app.logger.error(f"Server error: {e}")
        # Same cleanup as above
        refresher.terminate()
        refresher.join(timeout=3)
        if refresher.is_alive():
            refresher.kill()
        # Clear the queue before closing
        while not ipc.empty():
            app.logger.info("Clearing IPC (may log errors)")
            try:
                _, err = ipc.get_nowait()
                if err:
                    app.logger.error(err)
            except:
                continue
        ipc.close()
        ipc.join_thread()  # Wait for the queue's feeder thread to finish
        del app.config['IPC_QUEUE']  # Remove from app config
        del app.config['REFRESHING']  # Remove from app config
        exit(1)
