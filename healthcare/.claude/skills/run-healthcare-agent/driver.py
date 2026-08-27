"""Headless text driver for the healthcare agent.

`uv run agent.py console` is a Rich TUI that reads keystrokes from a real
terminal, so piped stdin is silently ignored and the agent just sits there
seeing no user turns. This drives the same HealthcareAgent programmatically:
a text-only AgentSession (no STT/TTS), scripted user turns, and a dump of the
FakeDatabase afterwards so you can see what the agent actually mutated.

Run from the `healthcare/` directory:
    uv run .claude/skills/run-healthcare-agent/driver.py --scenario schedule

Costs real LiveKit Inference credits on every run (live LLM calls).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime, time

from dotenv import load_dotenv

sys.path.insert(0, ".")  # agent.py does a top-level `from fake_database import ...`

from agent import HealthcareAgent, UserData  # noqa: E402
from fake_database import FakeDatabase  # noqa: E402
from sqlite_database import SqliteDatabase  # noqa: E402

from livekit.agents import AgentSession, inference  # noqa: E402

load_dotenv()

# Blunt, declarative turns. A live LLM picks the tools, so conversational
# hedging ("maybe sometime next week?") makes runs nondeterministic.
SCENARIOS: dict[str, list[str]] = {
    "schedule": [
        "I want to book an appointment.",
        "My name is Mary Jane.",
        "June 10th, 2001.",
        "Book me with Dr. Henry Jekyll.",
        "The first available slot works.",
        "The reason is a persistent cough.",
        "Yes, that is correct. Please confirm the appointment.",
    ],
    "billing": [
        "I want to pay my bill.",
        "My name is Peter Parker.",
        "August 10th, 2001.",
        "What is my outstanding balance?",
    ],
    "transfer": [
        "My name is Mary Jane.",
        "June 10th, 2001.",
        "I have chest pain, what medication should I take?",
        "Yes, please transfer me to a human agent.",
    ],
}

# Scenarios that are read-only: don't fail them for leaving the database alone.
READ_ONLY = {"billing", "transfer"}


def _json_default(obj: object) -> str:
    if isinstance(obj, (date, datetime, time)):
        return obj.isoformat()
    return str(obj)


def _short(agent: object) -> str:
    return type(agent).__name__ if agent is not None else "None"


def _print_events(label: str, result: object) -> None:
    print(f"\n--- {label} ---", flush=True)
    for ev in result.events:  # type: ignore[attr-defined]
        kind = type(ev).__name__
        if kind == "ChatMessageEvent":
            print(f"  [{ev.item.role}] {ev.item.text_content}")
        elif kind == "FunctionCallEvent":
            print(f"  [tool call] {ev.item.name}({ev.item.arguments})")
        elif kind == "FunctionCallOutputEvent":
            print(f"  [tool out ] {ev.item.output}")
        elif kind == "AgentHandoffEvent":
            print(f"  [handoff  ] {_short(ev.old_agent)} -> {_short(ev.new_agent)}")
        else:
            print(f"  [{kind}] {ev}")
    print(flush=True)


async def _settle(session: AgentSession, *, quiet: float = 2.0, timeout: float = 60.0) -> None:
    """Wait until the agent stops working.

    session.run() returns when ITS speech handle completes, but this agent
    hands off into inline AgentTasks (GetNameTask, ScheduleAppointmentTask...)
    that keep generating afterwards. Feeding the next user turn while that is
    in flight interrupts the speech that awaited the inline task, and the tool
    call dies with "the speech that awaited the inline task is interrupted".
    So: wait for agent_state to go quiet and STAY quiet.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    stable_since: float | None = None
    while loop.time() < deadline:
        if session.agent_state in ("listening", "idle"):
            if stable_since is None:
                stable_since = loop.time()
            elif loop.time() - stable_since >= quiet:
                return
        else:
            stable_since = None
        await asyncio.sleep(0.25)
    print(f"  [warn] agent still {session.agent_state!r} after {timeout}s", flush=True)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="schedule")
    ap.add_argument("--turn-timeout", type=float, default=120.0)
    ap.add_argument("--verbose", action="store_true", help="show livekit DEBUG logs")
    ap.add_argument(
        "--db",
        metavar="PATH",
        help="run against SqliteDatabase at PATH instead of the in-memory "
        "FakeDatabase. Use a throwaway path: the schedule assertions expect "
        "unconsumed availability, so a reused file fails on the second run.",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    db = SqliteDatabase(args.db) if args.db else FakeDatabase()
    print(f"database: {type(db).__name__}({args.db or 'in-memory'})", flush=True)
    # Same wiring as entrypoint() in agent.py, minus STT/TTS (text only) and
    # minus the user_state_changed idle-nudge (no audio => no "away" state).
    session = AgentSession(
        userdata=UserData(database=db, profile=None),
        llm=inference.LLM("google/gemma-4-31b-it"),
    )

    # Anything the inline tasks say after a run() resolves shows up here.
    @session.on("conversation_item_added")
    def _on_item(ev) -> None:  # noqa: ANN001
        # Items are not all ChatMessage: AgentHandoff also arrives here and has
        # no .role, which raises inside the emitter if you access it blindly.
        if getattr(ev.item, "role", None) == "assistant" and ev.item.text_content:
            print(f"  (live) [assistant] {ev.item.text_content}", flush=True)

    before = json.dumps(db.patient_records, default=_json_default, sort_keys=True)

    greeting = await session.start(agent=HealthcareAgent(database=db), capture_run=True)
    _print_events("greeting", greeting)
    await _settle(session)

    try:
        for i, turn in enumerate(SCENARIOS[args.scenario], start=1):
            print(f"\n>>> user: {turn}", flush=True)
            try:
                result = await asyncio.wait_for(
                    session.run(user_input=turn), timeout=args.turn_timeout
                )
            except asyncio.TimeoutError:
                print(f"  [warn] turn {i} timed out after {args.turn_timeout}s", flush=True)
                continue
            _print_events(f"turn {i}", result)
            await _settle(session)
    finally:
        await session.aclose()

    print("\n=== patient records after run ===")
    print(json.dumps(db.patient_records, indent=2, default=_json_default))
    print("\n=== doctor availability after run ===")
    print(json.dumps(db.doctor_records, indent=2, default=_json_default))

    changed = before != json.dumps(db.patient_records, default=_json_default, sort_keys=True)
    print(f"\nDATABASE MUTATED: {changed}")
    if args.scenario in READ_ONLY:
        return 0
    if args.scenario == "schedule":
        # `changed` alone is too weak: a phone-number update or a newly created
        # patient also flips it. Require the actual booking.
        patient = db.get_patient_by_name("Mary Jane") or {}
        appointments = patient.get("appointments") or []
        jekyll = db.get_doctor_by_name("Dr. Henry Jekyll") or {}
        slots_left = len(jekyll.get("availability", []))
        booked = bool(appointments) and slots_left < 3
        print(f"APPOINTMENTS FOR MARY JANE: {len(appointments)}")
        print(f"DR. HENRY JEKYLL SLOTS LEFT: {slots_left} (was 3)")
        print(f"APPOINTMENT BOOKED: {booked}")
        return 0 if booked else 1
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
