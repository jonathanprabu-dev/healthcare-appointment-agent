---
name: run-healthcare-agent
description: Run, launch, start, drive, or smoke-test the LiveKit healthcare voice agent (appointment scheduling and billing). Use when asked to run the agent, test a conversation flow end-to-end, check that a change to agent.py or sqlite_database.py still works, or reproduce a scheduling/billing/transfer scenario headlessly.
---

# Run the healthcare agent

A LiveKit Agents 1.7 voice agent (`agent.py`) for appointment scheduling and
billing, backed by SQLite (`sqlite_database.py`). STT/LLM/TTS all come from
LiveKit Inference, so the only credentials needed are the LiveKit ones.

**The agent path is `driver.py`** — a headless text driver that starts the same
`HealthcareAgent` in a text-only `AgentSession`, feeds it scripted user turns,
and dumps the database afterwards. Use it instead of `console`: the `console`
subcommand is a Rich TUI that reads keystrokes from a real terminal and
**silently ignores piped stdin**, so an agent driving it sees the session sit
idle and hang up.

All paths below are relative to `healthcare/`. Verified on Windows 11 /
PowerShell + Git Bash; not tried on Linux or macOS.

**Every run costs real LiveKit Inference credits** — it makes live LLM calls.
A full `schedule` run is ~8 LLM turns and takes 2-4 minutes.

## Prerequisites

`uv` (installs its own Python — the pinned 3.13 in `.python-version` is
deliberate, see Gotchas):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
$env:Path = "C:\Users\$env:USERNAME\.local\bin;$env:Path"
```

## Setup

```bash
uv sync
uv run -m livekit.agents download-files   # Silero VAD weights
```

`.env` must hold three real values (get them from cloud.livekit.io — the same
key/secret authenticates Inference, so no OpenAI key is needed):

```
LIVEKIT_URL="wss://<project>.livekit.cloud"
LIVEKIT_API_KEY="..."
LIVEKIT_API_SECRET="..."
```

## Run (agent path)

```powershell
$env:PYTHONUTF8=1
uv run .claude/skills/run-healthcare-agent/driver.py --scenario schedule
```

Same thing from bash (PowerShell has no inline env-var prefix, so the one-liner
form is bash-only):

```bash
PYTHONUTF8=1 uv run .claude/skills/run-healthcare-agent/driver.py --scenario schedule
```

Scenarios, all verified end-to-end against live inference:

| `--scenario` | Exercises | Exit 0 when |
|---|---|---|
| `schedule` | auth fast-path -> doctor choice -> slot -> visit reason | Mary Jane has an appointment AND Jekyll is under 3 slots |
| `billing` | auth fast-path -> balance lookup | always (read-only) |
| `transfer` | out-of-scope medical question -> `transfer_to_human` | always (read-only) |

Other flags: `--verbose` (livekit DEBUG logs), `--turn-timeout` (default 120s),
`--db PATH` (see Database below).

The `schedule` exit code is a real smoke-test signal — it checks the booking,
not just that some field changed. It prints its own verdict:

```
APPOINTMENTS FOR MARY JANE: 1
DR. HENRY JEKYLL SLOTS LEFT: 2 (was 3)
APPOINTMENT BOOKED: True
```

Output is a transcript — `[tool call]`, `[tool out]`, `[handoff]`, assistant
messages — then the full `patient_records` and `doctor_records` JSON, then
`DATABASE MUTATED: True/False`. **Read the database dump, not just the
transcript.** The agent will happily *say* it booked something; the proof is
an `appointments` entry on the patient and the matching slot gone from the
doctor's `availability`.

A passing `schedule` run ends with, on Mary Jane:

```json
"appointments": [
  {"doctor_name": "Dr. Henry Jekyll", "appointment_time": "2026-08-29T09:30:00",
   "visit_reason": "persistent cough"}
]
```

and Dr. Henry Jekyll down from 3 availability slots to 2.

### Database

SQLite, one backend, `sqlite_database.py`. It returns plain dicts holding real
`date`/`time`/`datetime` objects, and every method is synchronous because all
16 call sites in `agent.py` are.

`entrypoint()` opens `CLINIC_DB` (default `clinic.db`). The driver defaults to
`--db :memory:`, seeded, so runs are isolated and leave nothing behind. Point
`--db` at a file to inspect the result afterwards, with a **fresh path each
run** — `schedule` asserts an unconsumed slot exists, so a reused file
correctly fails the second time:

```bash
PYTHONUTF8=1 uv run .claude/skills/run-healthcare-agent/driver.py     --scenario schedule --db "$(mktemp -d)/clinic.db"
```

**Seeding is opt-in** (`SqliteDatabase(path, seed=True)`) and off by default.
The fixtures are two fictional patients and two fictional doctors; a
deployment must not invent patient records on first boot. Only the driver and
the tests pass `seed=True`.

After any change to the database, run the behaviour tests (no LLM, no network,
no credentials, ~1s):

```bash
uv run test_sqlite_database.py
```

Its oracle is `_ReferenceDatabase`, the original in-memory implementation,
kept inside the test file and nowhere else — a differential comparison catches
more than literal expectations. It asserts two **intentional** divergences
from that reference rather than hiding them: SQLite omits the `appointments`
key after a cancel where the reference leaves it empty (`agent.py` reads it
via `.get("appointments", [])` either way), and SQLite returns availability in
chronological order where the reference appends a restored slot at the end.

### Writing a new scenario

Add a list of user turns to `SCENARIOS` in `driver.py`. Keep them blunt and
declarative ("Book me with Dr. Henry Jekyll", not "maybe sometime next
week?") — a live LLM picks the tools, so hedging makes runs nondeterministic.
Add read-only scenarios to the `READ_ONLY` set so they are not failed for
leaving the database alone.

Seed data worth knowing (`SqliteDatabase._seed`): `Mary Jane` / 2001-06-10 /
Anthem, and `Peter Parker` / 2001-08-10 / Aetna. Anthem reaches both doctors,
Aetna only Dr. Edward Hyde. Giving a name+DOB already in the table triggers
the auth fast-path (`ProfileFound`); any other name goes down the longer
profile-creation branch and needs turns for phone number and insurance.

## Run (human path)

```powershell
$env:PYTHONUTF8=1
uv run agent.py console            # mic + speakers
uv run agent.py console --text     # typed
```

Only useful from a real terminal. `console --text` is the form verified here;
the mic/speakers form was never launched (no audio device in this environment).

`uv run agent.py dev` instead registers a worker against LiveKit Cloud so a
browser frontend (agents-playground.livekit.io) can connect — verified as far
as `registered worker`, not through an actual browser session.

## Gotchas

- **Piped stdin into `console` does nothing.** The Rich TUI reads a TTY. The
  agent sees zero user turns, logs `user idle — checking if they're still
  there` every 10s, then calls `EndCallTool` and closes with
  `reason: user_initiated`. It looks like the agent broke; nothing was ever
  delivered to it. This is the entire reason `driver.py` exists.
- **`PYTHONUTF8=1` is mandatory** wherever output is not a UTF-8 console. Rich
  prints a rocket emoji during startup and cp1252 kills the process:
  `UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f680'`.
- **You must wait for the agent to settle between turns.** `session.run()`
  resolves when *its own* speech handle completes, but this agent hands off
  into inline `AgentTask`s (`GetNameTask`, `ScheduleAppointmentTask`) that keep
  generating afterwards. Sending the next turn mid-flight interrupts the speech
  that awaited the inline task and the tool dies with `RuntimeError: the speech
  that awaited the inline task is interrupted` — the flow then stalls, turns
  return zero events, and nothing reaches the database. `driver.py::_settle()`
  polls `session.agent_state` until it is `listening`/`idle` for 2s straight.
- **`conversation_item_added` does not only carry chat messages.** `AgentHandoff`
  items arrive on the same event and have no `.role`; touching it blindly
  raises inside the emitter (`'AgentHandoff' object has no attribute 'role'`).
  Use `getattr(ev.item, "role", None)`.
- **Do not reuse `entrypoint()`** from `agent.py` in a driver. It is decorated
  with `@server.rtc_session()` and wants a `JobContext`. Rebuild the session
  (10 lines) and skip the `user_state_changed` idle-nudge wiring — with no
  audio there is no `away` state, and that loop otherwise fires nonsense
  replies into a silent session.
- **`agent.ProfileFound` in the logs is not an error.** It is control flow: the
  auth task raises it to signal the fast-path.
- **Balances are `random.uniform(20, 3000)` per process**, so billing output
  differs every run. Do not assert on the number.
- **`transfer` degrades gracefully without SIP.** The tool returns
  `SIP_TRUNK_ID is not configured` and the agent apologises. Exercising the
  real warm transfer needs `LIVEKIT_SIP_OUTBOUND_TRUNK`,
  `LIVEKIT_SUPERVISOR_PHONE_NUMBER`, `LIVEKIT_SIP_NUMBER` — untested here.
- **Run everything from `healthcare/`.** `agent.py` does a top-level
  `from sqlite_database import SqliteDatabase`; the driver compensates with
  `sys.path.insert(0, ".")`, which still assumes that cwd.
- **`add_appointment` is one transaction** in `sqlite_database.py`: it inserts
  the appointment and deletes the doctor's availability slot together. Split
  them and a crash in between double-books the doctor.
- **The database interface is synchronous** and all 16 call sites in `agent.py`
  assume it. Keep it that way — an async driver (asyncpg) turns every one of
  them into an `await`, including the ones inside `@function_tool` bodies.
- **Python is pinned to 3.13** in `.python-version`. The `livekit-plugins-silero`
  dependency pulls `onnxruntime`, which is not a safe bet on 3.14; uv fetches a
  managed 3.13 automatically.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ValueError: api_key is required, or set LIVEKIT_API_KEY` — preceded by `python-dotenv could not parse statement starting at line N` | Malformed `.env` line. An unbalanced quote (e.g. a key pasted next to the leftover placeholder: `KEY="real"your_api_key"`) makes dotenv drop the rest of the file. One `KEY="value"` per line. |
| `UnicodeEncodeError: 'charmap' codec ... '\U0001f680'` | Set `PYTHONUTF8=1`. |
| `RuntimeError: the speech that awaited the inline task is interrupted` | Turns are being sent while the agent is still working — increase the `quiet` window in `_settle()`. |
| A turn prints `--- turn N ---` with no events, and later turns do nothing | Same cause as above: an inline task was killed earlier and the flow is stuck. |
| `download-files ... is deprecated as of 1.5.10` | Use `uv run -m livekit.agents download-files`, not `uv run agent.py download-files`. |
| Worker starts but `registered worker` never appears | Wrong `LIVEKIT_URL`, or credentials belong to a different project. |
