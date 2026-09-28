# Healthcare voice agent

The agent behind the clinic's phone line. It authenticates the caller, books,
reschedules, and cancels appointments, takes card payments against a balance,
and hands off to a human when a question is out of scope. It works by voice or
text, and the caller can switch between them mid-conversation.

The project overview, quick start, and configuration table are in the
[top-level README](../README.md). This file covers how the agent itself works.
Other docs:

- [`DEPLOYMENT.md`](DEPLOYMENT.md): phone number, dispatch rule, and production notes.
- [`.claude/skills/run-healthcare-agent/SKILL.md`](.claude/skills/run-healthcare-agent/SKILL.md):
  running the headless text driver, the admin CLI, and a long list of gotchas.

## Stack

| Piece | Choice | Where |
|---|---|---|
| Framework | LiveKit Agents 1.7 (`AgentServer`, `AgentSession`, `AgentTask`, `TaskGroup`) | `agent.py` |
| STT | `deepgram/nova-3`, multilingual, via LiveKit Inference | `entrypoint()` |
| LLM | `google/gemma-4-31b-it` via LiveKit Inference | `entrypoint()` |
| TTS | `inworld/inworld-tts-2` (voice "Luna"), falling back to `cartesia/sonic-2` then `elevenlabs/eleven_flash_v2_5` | `entrypoint()` |
| Storage | SQLite in WAL mode, synchronous API | `sqlite_database.py` |
| Background audio | Forest ambience while the agent is thinking | `entrypoint()` |

## Call flow

```
greeting ─► caller states intent
             ├─ schedule_appointment ─► profile_authenticator ─► ScheduleAppointmentTask ─► add_appointment
             ├─ modify_appointment   ─► profile_authenticator ─► ModifyAppointmentTask   ─► cancel / rebook
             ├─ handle_billing       ─► profile_authenticator ─► GetCreditCardTask       ─► confirm payment
             ├─ retrieve_available_doctors (no authentication needed)
             └─ transfer_to_human    (out of scope, or the caller asks for a person)
```

### Authentication (`profile_authenticator`)

It runs at most once per call, before anything touches patient data. A
`TaskGroup` collects name → date of birth → phone number → insurance. After the
date of birth, the completion callback looks up the name plus DOB. If a patient
matches, it raises `ProfileFound` to skip the rest. That's control flow, not an
error. Otherwise it creates a new patient record. The caller then gets an
`update_record` tool for corrections.

The task group deliberately starts **without** the parent transcript. With it,
`GetNameTask` tried to *confirm* a name before its `confirm_name` tool existed,
and the call hung. See the comment in `profile_authenticator` and
`test_profile_authenticator.py`.

### Scheduling (`ScheduleAppointmentTask`)

1. **Visit reason → specialty.** `confirm_visit_reason` records why the caller
   is coming in. `confirm_specialty` picks one of `SPECIALTIES`, and "General"
   accepts anyone.
2. **Doctor.** Only doctors who accept the caller's insurance *and* practice
   that specialty are offered. The selection tool is built on the fly, with
   those names as an enum. If no doctor matches, the tool isn't registered at
   all, because an empty enum used to hang the model.
3. **Time.** The scheduling tool's enum lists every open slot, so any date the
   caller names can be booked. The system message shows only the next
   `SLOT_SHORTLIST` (8) slots, which keeps prompts small.
4. **Booking.** `add_appointment` claims the slot by *deleting* the
   availability row in the same transaction that inserts the appointment. If
   another caller got there first, the agent says so and offers another time.

Every subtask await is capped at `SUBTASK_TIMEOUT_SECONDS` (300s). If the model
never calls a completion tool, the caller hears an apology instead of silence.

### Idle handling

After 10 seconds of mutual silence (`user_away_timeout`), the agent checks
whether the caller is still there. `EndCallTool` ends the call politely when
the caller won't cooperate.

## Dashboard integration

When `DASHBOARD_URL` is set, `dashboard_events.DashboardEventLog` POSTs each
database write and each call event (`call.started`, and so on) to
`api/main.py`, which fans them out to browsers over `WS /api/events`. The queue
is bounded and drops the oldest events rather than ever blocking a call. When
`DASHBOARD_URL` is unset, the agent behaves as if the dashboard didn't exist.

API endpoints: `GET /api/summary`, `/api/patients`, `/api/doctors`,
`/api/appointments`, `/api/billing`, `/api/calls`, `/api/events/history`;
`POST /api/doctors`, `/api/doctors/{name}/slots`, `/api/ingest/events`;
`WS /api/events`. Interactive docs are at `http://127.0.0.1:8000/docs`.

## Running

```bash
uv run agent.py dev                 # register with LiveKit Cloud (phone line)
uv run agent.py console --text      # typed conversation in this terminal
PYTHONUTF8=1 uv run .claude/skills/run-healthcare-agent/driver.py --scenario schedule   # scripted, checks the DB
```

The worker registers as **`healthcare-agent`**, which is set in
`@server.rtc_session(agent_name=...)`. That name must match `agents` in
`sip-dispatch-rule.json`, or phone calls ring with nobody to answer. LiveKit
reads no environment variable for it.

## Windows specifics

- **Resampler warm-up (`prewarm`).** The soxr resampler bundled in
  `livekit_ffi.dll` initializes a global FFT cache on first use, without a
  lock. When two threads hit that first use at the same moment,
  `assert(FFT_LEN == -1)` fails. On Windows, the job thread then blocks on a
  hidden "Visual C++ Runtime Library" dialog (the caller hears silence where
  the greeting should be), and the worker later dies with exit code 3.
  `prewarm`, passed as `AgentServer(setup_fnc=...)`, runs the resampler once
  before any call audio flows.
- **One process for every call.** LiveKit's Windows default runs each call as
  a thread in a single process, so a native crash in one call ends them all.
  Linux uses a separate process per call.
- **Diagnosing a frozen call:** `uvx py-spy dump --pid <worker pid> --native`
  shows every thread's native and Python stacks. That's how the soxr assertion
  was found.
