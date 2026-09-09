# espressolab

Logs shots pulled on the lab's Decent DE1 (via [Decaid](https://github.com/decaid-app), running
on this touchscreen Windows PC) to a database, visualizes them in Grafana, and adds a simple
"who's brewing?" profile picker in front of Decaid's own web portal so every shot gets attributed
to a person.

Runs on SQLite by default — zero setup, just works locally. Point `DATABASE_URL` at Postgres
(e.g. your lab server) instead when you want that; nothing else changes.

## How it fits together

```
touchscreen browser
      │
      ▼
espressolab portal (this PC, port 5000)
  "who's brewing?" picker
      │  tags Decaid's pending shot via PUT /api/v1/workflow
      │  (context.drinkerName + context.extras.espressolab_user_id)
      ▼
Decaid's own web portal, embedded (this PC, port 3000)
  the person actually pulls the shot here
      │
      ▼
Decaid local REST/WebSocket API (this PC, port 8080)
      │  ws/v1/machine/shotState → terminal event → GET /api/v1/shots/{id}
      ▼
espressolab logger (this PC, background service)
      │
      ▼
SQLite (local) or Postgres (lab server)  ──►  Grafana (if using Postgres)
```

Decaid already exposes a full local REST/WebSocket API (see `api/rest_v1.yml` and
`api/websocket_v1.yml` bundled inside the Decaid install) and — importantly — its shot workflow
already has `context.drinkerName` / `context.baristaName` fields plus a free-form
`context.extras` bag. So instead of guessing after the fact which shot belonged to whom, the
portal sets that context *before* the shot is pulled, and the logger just reads it back off the
finished shot record.

Two small Python services run on this Windows PC:

- **`espressolab.portal`** — a FastAPI app. Shows the profile picker, calls Decaid's API to tag
  the pending shot, then shows Decaid's own web portal (iframed) with a small "Brewing as ..."
  bar and a Switch user button on top. Also has `/admin` for managing the roster of lab members.
- **`espressolab.logger`** — listens to Decaid's `ws/v1/machine/shotState` WebSocket; when a shot
  finishes it fetches the full record and writes it into Postgres.

## One-time setup

### 1. Python environment (on this Windows PC)

```powershell
.\scripts\setup-venv.ps1
```

Then copy `.env.example` to `.env`. The default `DATABASE_URL` is SQLite (`sqlite:///espressolab.db`)
— nothing else to set up, tables are created automatically the first time a service runs. Fill in
the rest:

- `SECRET_KEY` — any random string (used to sign the "who's selected" cookie).
- `ADMIN_PASSWORD` — passcode for the `/admin` user-management page (HTTP Basic, username `admin`).
- Leave `DECAID_*` at their defaults unless you've changed Decaid's ports.

### 2. Using Postgres instead (optional)

Only needed if you want the data on the lab server rather than a local SQLite file (e.g. so
Grafana on that server can read it directly). Create a database there, then point `DATABASE_URL`
at it in `.env`:

```
DATABASE_URL=postgresql://user:pass@lab-server:5432/espressolab
```

No manual schema step — tables are created automatically here too, the first time the portal or
logger connects.

### 3. Decaid

Make sure Decaid has a default WebUI skin set (Settings → WebUI/skins in the app — pick and set
one as default) at least once. The portal will start Decaid's WebUI server automatically on
launch if it isn't already running, but it can't pick a skin for you.

### 4. Add lab members

Run the portal (see below), then visit `http://localhost:5000/admin` and add people (name +
emoji/color avatar). No passwords for lab members themselves — anyone can tap a name on the
picker; the admin passcode only guards the roster itself.

### 5. Run the services

```powershell
# in one window
.\scripts\start-portal.ps1

# in another window
.\scripts\start-logger.ps1
```

Point the touchscreen's kiosk browser at `http://localhost:5000`.

To run these unattended at boot, the simplest option is
[NSSM](https://nssm.cc/) — install each as a Windows service pointing at
`.venv\Scripts\python.exe` with argument `run_portal.py` / `run_logger.py` and working directory
set to this repo.

### 6. Backfill existing shot history (optional, one-off)

If Decaid already has shots logged from before this system existed:

```powershell
.\.venv\Scripts\Activate.ps1
python run_backfill.py
```

This pages through everything in Decaid's shot history and ingests it (unattributed, since there
was no picker back then — `drinker_name`/`user_id` will be null for those rows).

## Troubleshooting

- **Decaid's portal shows blank inside the iframe.** Some browsers/servers refuse to be framed
  (`X-Frame-Options` / CSP `frame-ancestors`). Decaid's WebUI server is a plain static file
  server so this is unlikely, but if it happens, use the "Open in new window ↗" link next to
  "Switch user" as a workaround, or drop the iframe from `brew.html` and just redirect instead.
- **This machine has no Python installed yet.** Install Python 3.11+ (e.g. from
  python.org or `winget install Python.Python.3.12`) before running `scripts\setup-venv.ps1`.
- **Shots aren't showing up.** Check the logger's console output — it logs every shot it sees
  finish and any fetch/DB errors. Confirm Decaid's REST API is reachable at
  `http://localhost:8080/api/v1/machine/info`.

## Grafana

Needs Postgres (see step 2 above) — Grafana can't read a local SQLite file. Import
`dashboards/espressolab-overview.json` into your Grafana (Dashboards → New → Import → Upload
JSON), pointing it at a Postgres datasource for the `espressolab` database. It includes:

- shots today / avg shot duration / most active person this week
- shots per day, shots per person (30d)
- a shot picker + full pressure/flow/weight curve for that shot
- a searchable table of recent shots with dose/yield/TDS/EY/notes

## Repo layout

```
espressolab/
  models.py                 schema (users, shots, shot_samples) — auto-creates on startup
  config.py                 env var loading
  db.py                     SQLAlchemy async engine (SQLite or Postgres, from DATABASE_URL)
  decaid_client.py          Decaid REST API client
  ingest.py                 ShotRecord → database rows
  logger.py                 WebSocket listener service
  backfill.py               one-off/catch-up ingestion of all shots
  session.py                signed "current user" cookie helpers
  portal/                   FastAPI app (picker, embedded Decaid webui, admin)
dashboards/                 Grafana dashboard JSON
scripts/                    PowerShell setup/launch scripts
run_portal.py, run_logger.py, run_backfill.py   entry points
```
