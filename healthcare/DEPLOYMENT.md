# Deployment

How to put this agent in front of real users — a browser first, phones second.

Each section says plainly what has been **verified on this machine** and what
has not, because the two are very different levels of confidence.

## Prerequisites (verified)

`.env` needs three values, and only these three — STT, LLM and TTS all go
through LiveKit Inference, which authenticates with the same key and secret:

```
LIVEKIT_URL="wss://<project>.livekit.cloud"
LIVEKIT_API_KEY="..."
LIVEKIT_API_SECRET="..."
```

Point the agent at a real database and give it real data first, or callers
will be told there are no doctors:

```bash
export CLINIC_DB=clinic.db
uv run admin.py add-doctor "Dr. Ada Chen" --insurances Anthem Aetna
uv run admin.py add-slots "Dr. Ada Chen" --from 2026-09-01 --to 2026-09-30 \
    --times 09:00 09:30 10:00 --weekdays mon tue wed thu fri
uv run admin.py list-doctors
```

The agent creates patients itself during a call, so you do not need to preload
them — but `add-patient` exists for records you already hold.

## 1. Browser frontend

**Verified:** the worker starts and registers against LiveKit Cloud with
`CLINIC_DB` set —

```bash
CLINIC_DB=clinic.db uv run agent.py dev
# INFO livekit.agents - registered worker {"url": "wss://<project>.livekit.cloud", ...}
```

Once that line appears the agent is waiting for a room, and anything that joins
a room on this project reaches it.

**Not verified here:** the browser half. This machine has no browser
automation, so the steps below are from LiveKit's documentation, not from a
session I ran.

- **Fastest:** [agents-playground.livekit.io](https://agents-playground.livekit.io),
  sign in with the same LiveKit Cloud account, connect. It builds the token for
  you and gives you a mic, a transcript and the tool-call log.
- **Real app:** LiveKit's starter apps
  (`docs.livekit.io/agents/start/frontend/`) — a Next.js voice UI you own and
  restyle. It needs a token endpoint holding your API key/secret; the key must
  never reach the browser.

Keep `test_sqlite_concurrency.py`'s finding in mind when sizing this: about 32
simultaneously-writing sessions is the SQLite ceiling. Browser traffic is
unlikely to reach it; it is the number to watch if it grows.

## 2. Telephony

**Not verified at all.** The LiveKit CLI (`lk`) is not installed on this
machine, and inbound calls need a purchased phone number, so nothing in this
section has been executed. Treat it as a checklist against LiveKit's SIP
documentation (`docs.livekit.io/sip/`), not as a tested runbook.

What *is* verified is the code side — these environment variables are read by
`agent.py` and nothing else has to change to use them:

| Variable | Read at | Purpose |
|---|---|---|
| `LIVEKIT_SIP_OUTBOUND_TRUNK` | `agent.py:43` | trunk used to dial the supervisor |
| `LIVEKIT_SUPERVISOR_PHONE_NUMBER` | `agent.py:44` | who a warm transfer reaches |
| `LIVEKIT_SIP_NUMBER` | `agent.py:45` | caller ID shown to the supervisor |

Unset, `transfer_to_human` fails cleanly rather than crashing: the tool raises
`SIP_TRUNK_ID is not configured` (`agent.py:107`) and the agent apologises to
the caller. That path **is** verified — `driver.py --scenario transfer` walks it
end to end.

Two distinct pieces of SIP, easy to conflate:

- **Inbound** — a number that rings the agent. Needs a SIP trunk from a
  provider (Twilio, Telnyx…), an inbound trunk in LiveKit, and a dispatch rule
  routing calls into a room the agent serves. None of the three env vars above
  are involved.
- **Outbound** — the warm transfer to a human. This is what the variables
  configure, and it needs an *outbound* trunk plus a real supervisor number.

Do inbound first; a phone line nobody can escalate from is still useful, while
an escalation path with no inbound line is not reachable.

## Production notes

- **Seeding is opt-in.** `SqliteDatabase` will not insert the fictional
  patients unless asked (`seed-demo`, which refuses a non-empty database).
  A fresh deployment starts empty, by design.
- **The database is single-machine.** WAL plus a 5s busy timeout handles
  concurrent sessions on one box, not workers spread across several. That
  migration is a reimplementation of `SqliteDatabase` against Postgres — the
  twelve methods are the whole contract.
- **Back up `clinic.db`.** It holds every appointment. Being a single file,
  `sqlite3 clinic.db ".backup 'backup.db'"` is the safe way to copy it while
  the agent is running; copying the file directly can catch a partial WAL.
- **`.env` and `*.db` are gitignored.** Keep it that way — the database holds
  patient records.
