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

**Working — verified by real inbound calls on 2026-08-28.** `lk` 2.18.3 is
installed and a number is purchased. What those calls verified is *routing*: the
number rings, the worker picks up the job, and the agent speaks. They did not
verify booking — on both calls `schedule_appointment` hung until the caller gave
up (root cause and fix in `test_profile_authenticator.py`'s docstring; the fix
itself is still unverified by voice). Current state on the project:

| Thing | ID | State |
|---|---|---|
| Phone number | `PN_PPN_QwQP3U5s6VWc` (+1 484-295-1233) | ACTIVE, routes to the agent |
| Dispatch rule | `SDR_CMDNYY2FKGXb` | individual, `call-` prefix, agent `healthcare-agent` |
| Inbound trunk | — | none, and none is needed |

LiveKit Phone Numbers need **no SIP trunk** — only a dispatch rule
(`docs.livekit.io/telephony/start/phone-numbers/`). A trunk was created during
setup on a wrong hunch and deleted again; do not recreate one.

The rule is defined by `sip-dispatch-rule.json`, applied with:

```bash
set -a && . ./.env && set +a
lk sip dispatch create sip-dispatch-rule.json
```

Because the rule names an agent, the worker must register under that name.
It does so in code, with `@server.rtc_session(agent_name="healthcare-agent")`
in `agent.py`, so a plain start is enough:

```bash
CLINIC_DB=clinic.db uv run agent.py dev
# INFO livekit.agents - registered worker {"agent_name": "healthcare-agent", ...}
```

Earlier revisions of this file said to set `LIVEKIT_AGENT_NAME` instead.
LiveKit Agents 1.7 reads no such variable: the worker registered with
`"agent_name": ""`, and phone calls rang with nobody to answer
(verified 2026-09-28).

Note the tradeoff: a named agent uses **explicit dispatch**, so it stops
picking up rooms automatically. To reach it from the browser playground, the
room must request `healthcare-agent` by name.

**No explicit number-to-rule assignment is needed, and attempting one fails.**
`sip-dispatch-rule.json` sets neither a trunk nor an inbound number, so
`SDR_CMDNYY2FKGXb` is a catch-all occupying the `(any trunk, any number, no
PIN)` slot — and a catch-all already routes this number. Two real calls
confirmed it while `lk number get` still reported `SIP Dispatch Rules: -`; that
dash is not a sign that anything is wrong.

Both ways of assigning it explicitly fail, and for the same reason. The CLI
returns an opaque error:

```
lk number update --id PN_PPN_QwQP3U5s6VWc --sip-dispatch-rule-id SDR_CMDNYY2FKGXb
twirp error invalid_argument: twirp error unknown: Failed to update phone number
```

The dashboard (Telephony → Phone Numbers → ⋮ → *Assign dispatch rule*) is
clearer, and names the cause:

```
Dispatch rule for the same trunk, inbound number, number, and PIN combination
already exists in dispatch rule "SDR_CMDNYY2FKGXb" "healthcare-inbound"
```

The assign flow creates a *new* rule scoped to the number, which collides with
the existing catch-all. So: leave it unassigned. If a scoped rule is ever
wanted, delete `SDR_CMDNYY2FKGXb` first and recreate it with an inbound number
set — keeping `roomConfig.agents` intact, or explicit dispatch stops reaching
the named worker.

Ruled out along the way: `--number` instead of `--id`; before and after the
number went ACTIVE; with and without an inbound trunk; against
`cloud-api.livekit.io`. `--curl` shows a well-formed request, so the payload was
never the problem.

Warm transfer is a separate, still-unconfigured path (see below); note that
LiveKit Phone Numbers are **inbound only**, so a transfer needs a trunk from
another provider.

What *is* verified is the code side — these environment variables are read by
`agent.py` and nothing else has to change to use them:

| Variable | Read at | Purpose |
|---|---|---|
| `LIVEKIT_SIP_OUTBOUND_TRUNK` | `SIP_TRUNK_ID` in `agent.py` | trunk used to dial the supervisor |
| `LIVEKIT_SUPERVISOR_PHONE_NUMBER` | `SUPERVISOR_PHONE_NUMBER` in `agent.py` | who a warm transfer reaches |
| `LIVEKIT_SIP_NUMBER` | `SIP_NUMBER` in `agent.py` | caller ID shown to the supervisor |

Unset, `transfer_to_human` fails cleanly rather than crashing: the tool raises
`SIP_TRUNK_ID is not configured` (in `transfer_to_human`) and the agent apologises to
the caller. That path **is** verified — `driver.py --scenario transfer` walks it
end to end.

Two distinct pieces of SIP, easy to conflate:

- **Inbound** — a number that rings the agent. With a LiveKit Phone Number this
  is just a dispatch rule (above); with a third-party number you also need an
  inbound trunk carrying it. None of the three env vars above are involved.
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
