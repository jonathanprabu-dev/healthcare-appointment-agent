"""The dashboard API server.

Bridge between the voice agent and the web dashboard:

- the agent POSTs live events to ``POST /api/ingest/events`` (see
  ``dashboard_events.py``), which are persisted in an ``events`` table
  alongside the clinic data and fanned out to every browser over
  ``WS /api/events``;
- the React dashboard reads clinic data from the REST endpoints below and
  subscribes to the WebSocket for live drops.

Run from ``healthcare/``:

    uv run uvicorn api.main:app --host 127.0.0.1 --port 8000

Uses the same CLINIC_DB file as the agent (WAL keeps a concurrent reader and
the agent's writer happy). The ``events`` table is created lazily here and is
the only thing this server owns; the clinic tables stay the agent's.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, time as dtime, timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sqlite_database import SqliteDatabase

CLINIC_DB = os.getenv("CLINIC_DB", "clinic.db")
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "DASHBOARD_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173",
    ).split(",")
    if origin.strip()
]

# Types that survive the ingest allowlist. Anything else is dropped so a
# misbehaving agent cannot push arbitrary payloads to every dashboard.
ALLOWED_EVENT_TYPES = {
    "call.started",
    "call.utterance",
    "call.activity",
    "call.ended",
    "patient.created",
    "patient.updated",
    "appointment.scheduled",
    "appointment.cancelled",
    "payment.processed",
    "doctor.created",
    "doctor.availability_added",
}

EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    type       TEXT NOT NULL,
    payload    TEXT NOT NULL,     -- JSON string
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(type, created_at);
"""


def _json_safe(value: object) -> object:
    """Recursively render dates/times as ISO strings for JSON responses."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (date, datetime, dtime)):
        return value.isoformat()
    return value


class IngestEvent(BaseModel):
    type: str
    payload: dict = {}
    created_at: float | None = None


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, event: dict) -> None:
        message = json.dumps(event, default=str).encode("utf-8")
        stale: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_bytes(message)
            except Exception:  # noqa: BLE001 - a dead client must not kill the feed
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


class EventStore:
    """Persistent log of dashboard events, in the same SQLite file as the clinic."""

    def __init__(self, path: str) -> None:
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA busy_timeout=5000")
        self._con.executescript(EVENT_SCHEMA)

    def append(self, event: dict) -> dict:
        now = event.get("created_at") or time.time()
        with self._con:
            self._con.execute(
                "INSERT INTO events (type, payload, created_at) VALUES (?, ?, ?)",
                (event["type"], json.dumps(event.get("payload", {})), now),
            )
        event.setdefault("created_at", now)
        return event

    def history(self, limit: int = 500) -> list[dict]:
        rows = self._con.execute(
            "SELECT type, payload, created_at FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "type": row["type"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]

    def counts_since(self, event_type: str, since: float) -> int:
        row = self._con.execute(
            "SELECT COUNT(*) AS n FROM events WHERE type = ? AND created_at >= ?",
            (event_type, since),
        ).fetchone()
        return row["n"]

    def close(self) -> None:
        self._con.close()


db: SqliteDatabase
events_store: EventStore
manager: ConnectionManager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global db, events_store, manager
    db = SqliteDatabase(CLINIC_DB)
    events_store = EventStore(CLINIC_DB)
    manager = ConnectionManager()
    yield
    db.close()
    events_store.close()


app = FastAPI(title="Healthcare Dashboard API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- ingest

@app.post("/api/ingest/events")
async def ingest_events(events: list[IngestEvent]) -> dict:
    """Receive one or more events from the voice agent and fan them out."""
    accepted = [ev for ev in events if ev.type in ALLOWED_EVENT_TYPES]
    for ev in accepted:
        stored = events_store.append({"type": ev.type, "payload": ev.payload, "created_at": ev.created_at})
        await manager.broadcast(stored)
    return {
        "accepted": len(accepted),
        "rejected": len(events) - len(accepted),
    }


# ----------------------------------------------------------------- live

@app.websocket("/api/events")
async def event_stream(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            # Ingest→broadcast is server-push; this receive loop only exists to
            # detect the client going away (a bare endless sleep never notices).
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/api/events/history")
async def event_history(limit: int = 200) -> list[dict]:
    """Events already ingested, newest last — lets a dashboard catch up on load."""
    return events_store.history(limit=max(1, min(limit, 2000)))


# ------------------------------------------------------------- REST reads

@app.get("/api/patients")
async def list_patients() -> list[dict]:
    return _json_safe(db.patient_records)  # type: ignore[arg-type]


@app.get("/api/doctors")
async def list_doctors() -> list[dict]:
    return _json_safe(db.doctor_records)  # type: ignore[arg-type]


@app.get("/api/appointments")
async def list_appointments() -> list[dict]:
    records = db.appointments()
    return _json_safe(records)  # type: ignore[arg-type]


@app.get("/api/billing")
async def list_billing() -> list[dict]:
    return [
        {
            "patient_name": p["name"],
            "phone_number": p.get("phone_number"),
            "insurance": p.get("insurance"),
            "outstanding_balance": p["outstanding_balance"],
            "last_payment": None,
        }
        for p in db.patient_records
    ]


@app.get("/api/calls")
async def list_calls(limit: int = 100) -> list[dict]:
    """Call log, derived from the ingested call.* events (dynamic screen)."""
    rows = events_store._con.execute(
        "SELECT type, payload, created_at FROM events WHERE type IN"
        " ('call.started', 'call.ended') ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 2000)),),
    ).fetchall()
    calls: dict[int, dict] = {}
    for row in reversed(rows):
        payload = json.loads(row["payload"])
        stamp = row["created_at"]
        day = int(stamp // 86400)
        call = calls.setdefault(day, {"date": None, "started": 0, "ended": 0, "rooms": set()})
        if call["date"] is None:
            call["date"] = datetime.fromtimestamp(stamp).date().isoformat()
        if row["type"] == "call.started":
            call["started"] += 1
            call["rooms"].add(payload.get("room"))
        else:
            call["ended"] += 1
    return [
        {"date": c["date"], "started": c["started"], "ended": c["ended"]}
        for c in calls.values()
    ]


@app.get("/api/summary")
async def summary() -> dict:
    """KPI card numbers for the dashboard top bar."""
    today = datetime.combine(date.today(), dtime.min).timestamp()
    patients = db.patient_records
    appointments = db.appointments()
    upcoming = [a for a in appointments if a["appointment_time"] >= datetime.now()]
    return {
        "patients": len(patients),
        "doctors": len(db.doctor_records),
        "bookings_today": events_store.counts_since("appointment.scheduled", today),
        "calls_today": events_store.counts_since("call.started", today),
        "upcoming_appointments": len(upcoming),
        "payments_today": events_store.counts_since("payment.processed", today),
        "outstanding_balance": round(
            sum(p["outstanding_balance"] for p in patients), 2
        ),
    }


# ------------------------------------------------------------ admin writes

class DoctorIn(BaseModel):
    name: str
    specialty: str = "General"
    insurances: list[str] = []


class SlotsIn(BaseModel):
    from_date: date
    to_date: date
    times: list[dtime]
    weekdays: list[int] = []  # 0=Mon .. 6=Sun; empty = every day


@app.post("/api/doctors")
async def add_doctor_endpoint(doctor: DoctorIn) -> dict:
    added = db.add_doctor(doctor.name, doctor.specialty, doctor.insurances)
    if not added:
        raise HTTPException(status_code=409, detail="a doctor with that name already exists")
    event = events_store.append(
        {
            "type": "doctor.created",
            "payload": {
                "name": doctor.name,
                "specialty": doctor.specialty,
                "accepted_insurances": doctor.insurances,
            },
        }
    )
    await manager.broadcast(event)
    return {"ok": True, "doctor": {"name": doctor.name, "specialty": doctor.specialty}}


@app.post("/api/doctors/{name}/slots")
async def add_slots_endpoint(name: str, slots: SlotsIn) -> dict:
    if db.get_doctor_by_name(name) is None:
        raise HTTPException(status_code=404, detail="no such doctor")
    if slots.to_date < slots.from_date:
        raise HTTPException(status_code=400, detail="to_date is before from_date")
    booked = db.booked_times(name)
    try:
        slots_list = _expand_slots(name, slots, booked)
        added = db.add_availability(name, slots_list)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    event = events_store.append(
        {
            "type": "doctor.availability_added",
            "payload": {"doctor_name": name, "added": added},
        }
    )
    await manager.broadcast(event)
    return {"ok": True, "added": added}


def _expand_slots(
    name: str, slots: SlotsIn, booked: set[datetime]
) -> list[tuple[date, dtime]]:
    wanted = set(slots.weekdays) if slots.weekdays else set(range(7))
    result: list[tuple[date, dtime]] = []
    day = slots.from_date
    while day <= slots.to_date:
        if day.weekday() in wanted:
            for slot_time in slots.times:
                if datetime.combine(day, slot_time) not in booked:
                    result.append((day, slot_time))
        day += timedelta(days=1)
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="127.0.0.1", port=int(os.getenv("DASHBOARD_PORT", "8000")))