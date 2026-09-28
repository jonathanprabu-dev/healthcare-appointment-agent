# Healthcare Appointment & Billing Agent

A voice agent that answers a clinic's phone line. Callers can book, reschedule,
or cancel appointments and pay outstanding balances, and a live dashboard
shows every call and every database write as it happens.

Built on [LiveKit Agents](https://docs.livekit.io/agents/) 1.7. Speech-to-text,
the LLM, and text-to-speech all run through LiveKit Inference, so the only
credentials needed are a LiveKit Cloud project's URL, API key and secret.

```
 caller ──► +1 484-295-1233 ──► LiveKit Cloud ──► agent.py (voice worker) ──► clinic.db (SQLite)
                              (SIP dispatch rule)        │
                                                         │ POST /api/ingest/events
                                                         ▼
                                            api/main.py (FastAPI) ──WS──► dashboard/ (React)
```

## What's in the repo

Everything lives under [`healthcare/`](healthcare/):

| Path | What it is |
|---|---|
| `agent.py` | The voice agent: greeting, patient authentication, scheduling, billing, and warm transfer to a human |
| `sqlite_database.py` | The clinic database (patients, doctors, availability, appointments) |
| `admin.py` | CLI for loading real doctors, availability, and patients |
| `api/main.py` | Dashboard API: REST endpoints for the clinic data and a WebSocket of live agent events |
| `dashboard_events.py` | Fire-and-forget event pipe from the agent to the API (optional; off unless `DASHBOARD_URL` is set) |
| `dashboard/` | React + Vite dashboard: stat tiles, charts, tables, and a live event feed |
| `sip-dispatch-rule.json` | The LiveKit SIP dispatch rule that routes the phone number to the agent |
| `test_*.py` | Database behaviour, concurrency, and authentication tests (no network needed) |
| `.claude/skills/run-healthcare-agent/driver.py` | Headless text driver that runs full conversations against live inference |

## Quick start

All commands run from `healthcare/`. You need [uv](https://docs.astral.sh/uv/)
and Node.js.

1. **Credentials.** Create `healthcare/.env`:

   ```
   LIVEKIT_URL="wss://<project>.livekit.cloud"
   LIVEKIT_API_KEY="..."
   LIVEKIT_API_SECRET="..."
   ```

2. **Install.**

   ```bash
   uv sync
   uv run -m livekit.agents download-files      # Silero VAD weights
   (cd dashboard && npm install)
   ```

3. **Load clinic data.** A fresh database is empty by design: without doctors
   and availability, the agent tells every caller there's no one to book with.

   ```bash
   uv run admin.py add-doctor "Dr. Ada Chen" --specialty Orthopedics --insurances Medicaid Aetna
   uv run admin.py add-slots "Dr. Ada Chen" --from 2026-10-01 --to 2026-10-31 \
       --times 09:00 09:30 10:00 --weekdays mon tue wed thu fri
   uv run admin.py list-doctors
   ```

   For a throwaway demo, use `uv run admin.py seed-demo`, which only works on an empty database.

4. **Run the three processes**, each in its own terminal:

   ```bash
   # dashboard API
   uv run uvicorn api.main:app --host 127.0.0.1 --port 8000

   # voice agent (streams events to the API)
   DASHBOARD_URL=http://127.0.0.1:8000 uv run agent.py dev

   # dashboard UI -> http://localhost:5173
   cd dashboard && npm run dev
   ```

   Wait for `registered worker {"agent_name": "healthcare-agent", ...}`, then
   call the clinic number.

## Talking to the agent

- **By phone:** the LiveKit number is routed to the worker named
  `healthcare-agent` by dispatch rule `SDR_CMDNYY2FKGXb`. Setup details and
  pitfalls are in [`healthcare/DEPLOYMENT.md`](healthcare/DEPLOYMENT.md).
- **Scripted, without audio:** the text driver runs whole conversations and
  checks the database afterwards:

  ```bash
  PYTHONUTF8=1 uv run .claude/skills/run-healthcare-agent/driver.py --scenario schedule
  ```

- **In a terminal:** `uv run agent.py console --text`.

The agent registers under a fixed name, so it uses **explicit dispatch**: it
answers the phone line, but the LiveKit browser playground reaches it only if
it asks for `healthcare-agent` by name.

## Tests

```bash
uv run test_sqlite_database.py       # behaviour, ~1s
uv run test_sqlite_concurrency.py    # locking under concurrent writers, ~20s
uv run test_profile_authenticator.py
```

These make no network calls and need no credentials. `driver.py` scenarios do
make live LLM calls, which cost LiveKit Inference credits.

## Configuration

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | agent | — | LiveKit Cloud project and Inference credentials |
| `CLINIC_DB` | agent, API, admin | `clinic.db` | SQLite database path |
| `DASHBOARD_URL` | agent | unset (off) | Where the agent POSTs live events |
| `DASHBOARD_ORIGINS` | API | `http://localhost:5173,…` | CORS allowlist for the dashboard |
| `DASHBOARD_PORT` | API (`python -m api.main`) | `8000` | API port |
| `VITE_API_BASE` | dashboard | `http://127.0.0.1:8000` | API base URL the UI calls |
| `SQLITE_BUSY_TIMEOUT_MS` | database | `5000` | How long a writer waits for a lock |
| `LIVEKIT_SIP_OUTBOUND_TRUNK`, `LIVEKIT_SUPERVISOR_PHONE_NUMBER`, `LIVEKIT_SIP_NUMBER` | agent | unset | Warm transfer to a human (not yet configured) |

## Status and known issues

Working, and verified on real phone calls:
- Inbound routing.
- The greeting.
- Recognizing an existing patient by name and date of birth.
- Recording the visit reason and routing to a specialty.

Still open:
- **Doctor and time-slot selection over the phone** hasn't been completed on a
  live call since the latest fixes; the text driver does complete it.
- **Insurance step can loop:** the insurance update can re-run several times
  from one caller turn, and speech recognition has confused "Medicaid" with
  "Medicare".
- **Slow shutdown:** about 10 seconds after every call, logged as `job shutdown
  is taking too much time`. The likely cause is the background ambience
  player. The caller doesn't hear it.
- **Temporary diagnostics:** `agent.py` still has a greeting watchdog and
  `faulthandler.enable()` from the silent-greeting investigation.

### Windows notes

- On Windows, LiveKit runs every call as a thread inside one worker process,
  so a native crash in one call ends all of them. The soxr resampler race that
  caused the silent first-call greeting (and worker exit code 3) is worked
  around by the `prewarm` setup function in `agent.py`.
- The dashboard's npm scripts call `node node_modules/vite/bin/vite.js`
  directly because this checkout's path contains `&`, which breaks npm's
  `.cmd` shims.
- Set `PYTHONUTF8=1` whenever output isn't going to a UTF-8 console.

## Data and privacy

`clinic.db` holds patient records and `.env` holds credentials. Both are
gitignored; keep it that way. Back up the database while the agent is running
with `sqlite3 clinic.db ".backup 'backup.db'"`, not a file copy.
