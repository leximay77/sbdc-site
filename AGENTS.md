# AGENTS.md

Guidance for coding agents working in this repository.

## Project Overview

- Project: Seattle Blues Dance Collective website.
- Purpose: Informational Flask website for Seattle-area blues dance events, recurring events, instructors, history/resources, and music.
- Public site: `https://seattlebluesdance.com`.
- Runtime: Python Flask app served on port `8080`.
- Deployment: Fly.io app `blues-underground`, deployed from `main` via GitHub Actions.

## Tech Stack

- Python `>=3.13`; `.python-version` currently pins `3.13`.
- Dependencies are declared in `pyproject.toml` and locked with `uv.lock`.
- Main dependencies: `flask`, `gunicorn`, `requests`, `icalendar`, `recurring-ical-events`, `beautifulsoup4`.
- Frontend is server-rendered Jinja plus plain CSS and vanilla JavaScript.
- Dockerfile currently builds from Python `3.14` images; keep this version difference in mind when changing Python-specific behavior.

## Repository Structure

- `app.py`: Flask application, routes, background calendar refresher process, `/events.json` API.
- `bluescal.py`: Google Calendar `.ics` fetching/parsing, event normalization, recurrence expansion, feature tagging, optional Google Maps neighborhood lookup.
- `templates/base.html`: Shared layout, navigation menu, banner, analytics, shared blocks.
- `templates/index.html`: Event calendar page and most client-side event rendering/filtering logic.
- `templates/recurring_events.html`: Static recurring-event content.
- `templates/about.html`, `templates/instructors.html`, `templates/history.html`, `templates/music.html`: Static content pages.
- `static/css/style.css`: Global design system and responsive layout styles.
- `static/js/menu.js`: Off-canvas menu behavior.
- `static/images/`: Site, venue, social, and instructor images.
- `bluescal.ics`: Local calendar cache file. It is ignored by git via `*.ics`.
- `Dockerfile`, `fly.toml`, `.github/workflows/fly-deploy.yml`: Container and Fly.io deployment config.

## Local Setup

Preferred setup with `uv`:

```sh
uv sync
```

README-documented setup:

```sh
uv venv .venv
source .venv/bin/activate
uv pip install -r pyproject.toml
```

Run locally:

```sh
uv run python app.py
```

Then open `http://localhost:8080`.

## Useful Commands

Syntax check:

```sh
uv run python -m py_compile app.py bluescal.py
```

Run the Flask app directly:

```sh
uv run python app.py
```

Run the container locally:

```sh
docker build -t blues-underground .
docker run --rm -p 8080:8080 blues-underground
```

There is currently no configured test runner, formatter, linter, type checker, or package script. Do not invent new tooling unless the task explicitly calls for it.

## Runtime Behavior

- `app.py` starts a daemon `multiprocessing.Process` to refresh the public Google Calendar data.
- The refresher calls `bluescal.refresh()` every 900 seconds and retries after 60 seconds on exceptions.
- `/events.json` reads the newest calendar object from an IPC queue, falls back to `CACHED_CAL`, then returns normalized JSON events for a requested `month` and `year`.
- `bluescal.refresh()` fetches from `CAL_URL` and writes `bluescal.ics`, but reuses the file if it was refreshed within `CAL_CACHE_TTL_SECONDS`.
- `bluescal.process_events()` expands recurring events, computes stable hashed IDs, infers venues/categories, sanitizes descriptions with BeautifulSoup, and optionally caches nearby-month events in memory.
- Client-side code in `templates/index.html` fetches `/events.json`, renders the current/next week list, renders a monthly calendar, filters by location/time/features, and supports URL fragments for dates and event IDs.

## Environment Variables

- `BLUESCAL_GMAPS_ENABLE=1`: Enables Google Maps geocoding for neighborhood lookup. Default is disabled.
- `MAPS_API_KEY`: Required only when Google Maps geocoding is enabled.
- Do not commit secrets, `.env` files, API tokens, or generated credentials.

## Deployment

- Fly.io config is in `fly.toml`; internal service port is `8080`, region is `sea`, and at least one machine is kept running.
- GitHub Actions deploys on pushes to `main` unless only ignored paths changed: `README.md`, `.gitignore`, `.dockerignore`, or `.github/**`.
- Dependabot is configured weekly for `uv`, Docker, and GitHub Actions.
- For deployment-impacting changes, verify the Docker build in addition to local Python checks when feasible.

## Coding Guidelines

- Keep changes small and targeted. This is a simple Flask/static site; avoid introducing frameworks or build systems without a clear requirement.
- Preserve the existing Jinja + vanilla JS + global CSS architecture unless asked to refactor.
- Prefer route/template changes over adding new abstractions for one-off static pages.
- Keep user-facing content respectful of the stated mission: inclusive, culturally respectful blues dance community rooted in Black American blues dance traditions.
- Use `target="_blank"` links with `rel="noopener"`, matching existing templates.
- Preserve mobile behavior when touching layout, filters, menu, slide-over, or calendar controls.
- Avoid committing generated/cache files such as `bluescal.ics`, `__pycache__/`, `.DS_Store`, or `.venv/`.
- Keep image/license-sensitive content in mind. README notes the logo and instructor images are used with permission.

## Calendar/Event Change Notes

- Event JSON shape consumed by the frontend includes `uid`, `title`, `date`, `time`, `location`, `neighborhood`, optional `venue`, `categories`, and `description`.
- If you change fields in `bluescal.process_events()`, update the JavaScript in `templates/index.html` at the same time.
- Date/time behavior should remain Seattle/Pacific oriented. Python uses `ZoneInfo("America/Los_Angeles")`; JavaScript uses `toLocaleString` with `America/Los_Angeles`.
- Be careful with all-day events; frontend time parsing assumes a clock time in several places.
- Calendar fetches are network-dependent. Local behavior may depend on the cached `bluescal.ics` file.

## Verification Expectations

For Python changes:

- Run `uv run python -m py_compile app.py bluescal.py`.
- Run `uv run python app.py` and manually check relevant pages/API when behavior changes.

For template, CSS, or JavaScript changes:

- Run the app and manually check desktop and mobile widths.
- Check `/`, `/recurring-events`, `/about`, `/instructors`, `/history`, and `/music` if shared layout or CSS changes.
- Check calendar month navigation, filter toggles, slide-over behavior, share links, and menu behavior when touching `templates/index.html`, `static/css/style.css`, or `static/js/menu.js`.

For deployment changes:

- Run a local Docker build when feasible.
- Confirm `fly.toml` port and Docker/Flask port remain aligned on `8080`.
