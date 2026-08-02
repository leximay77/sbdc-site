import os
import time
import logging
from datetime import date
from multiprocessing import Process, Queue, freeze_support

from flask import Flask, request, render_template, flash, jsonify

import bluescal


app = Flask(__name__)
app.secret_key = os.urandom(32)
app.logger.setLevel(logging.INFO)

CACHED_CAL = None


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

@app.route('/')
def index():
    if not app.config.get('REFRESHING'):
        app.logger.info("Starting refresher")
        ipc = app.config.get('IPC_QUEUE')
        if not ipc:
            ipc = Queue()
            app.config['IPC_QUEUE'] = ipc
        refresher = Process(target=refresh_calendar, args=(ipc,), daemon=True)
        refresher.start()
        app.config['REFRESHING'] = True
    return render_template('index.html')

@app.route('/events.json')
def events_json():
    global CACHED_CAL
    try:
        ipc = app.config.get("IPC_QUEUE")
        if not ipc:
            app.logger.error("No IPC to read calendar from")
        CACHED_CAL = ipc.get_nowait()
        bluescal.clear_event_caches()
    except Exception as e:
        cal = CACHED_CAL
    if not CACHED_CAL:
        app.logger.error(f"No calendar available to read")
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
    events = bluescal.process_events(CACHED_CAL, month, year, do_cache, app.logger)
    response = jsonify(events)
    response.add_etag()
    response.cache_control.private = True
    response.cache_control.max_age = 300
    return response.make_conditional(request)

@app.route('/recurring-events')
def recurring_events():
    return render_template('recurring_events.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/instructors')
def instructors():
    return render_template('instructors.html')

@app.route('/history')
def history():
    return render_template('history.html')

@app.route('/music')
def music():
    return render_template('music.html')

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
