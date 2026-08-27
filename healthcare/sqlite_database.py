"""The clinic database.

Returns plain dicts holding real `date` / `time` / `datetime` objects — never
sqlite3.Row, never ISO strings — because agent.py indexes and formats them
directly. Every call site in agent.py is synchronous, so this is deliberately
synchronous too; sqlite3 is fast enough that offloading to a thread would cost
more than it saves.

    from sqlite_database import SqliteDatabase
    db = SqliteDatabase("clinic.db")                # empty unless seeded
    db = SqliteDatabase(":memory:", seed=True)      # demo fixtures

Single-machine only: WAL plus a busy timeout handles several concurrent
sessions against one file, but not workers spread across machines. That
migration is a reimplementation of this class against Postgres — the twelve
methods below are the whole contract.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, datetime, time, timedelta

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS patients (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE,
    date_of_birth      TEXT NOT NULL,
    phone_number       TEXT,
    insurance          TEXT,
    outstanding_balance REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS appointments (
    id               INTEGER PRIMARY KEY,
    patient_id       INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    doctor_name      TEXT NOT NULL,
    appointment_time TEXT NOT NULL,
    visit_reason     TEXT
);

CREATE INDEX IF NOT EXISTS idx_appointments_patient ON appointments(patient_id);

CREATE TABLE IF NOT EXISTS doctors (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    accepted_insurances TEXT NOT NULL          -- JSON array
);

CREATE TABLE IF NOT EXISTS availability (
    id        INTEGER PRIMARY KEY,
    doctor_id INTEGER NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    date      TEXT NOT NULL,
    time      TEXT NOT NULL,
    UNIQUE (doctor_id, date, time)
);
"""


class SqliteDatabase:
    def __init__(self, path: str = "clinic.db", *, seed: bool = False) -> None:
        """Open (and create if needed) the clinic database.

        seed=True inserts the demo fixtures — two fictional patients and two
        fictional doctors — into an empty database. It defaults to False: a
        deployment must never invent patient records on first boot. The driver
        and the tests pass seed=True explicitly.
        """
        # check_same_thread=False: the agent runs tool calls off the event loop
        # thread in some paths; access here is serialized by SQLite's own lock.
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        # WAL lets readers run concurrently with a writer — a worker per job
        # means several sessions may touch this file at once. WAL is a property
        # of the database and persists; busy_timeout is per-connection and has
        # to be set on every open, or concurrent writes raise "database is
        # locked" immediately instead of waiting their turn.
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA busy_timeout=5000")
        self._con.executescript(SCHEMA)
        if seed and not self._con.execute("SELECT 1 FROM patients LIMIT 1").fetchone():
            self._seed()

    def close(self) -> None:
        self._con.close()

    # ------------------------------------------------------------------ seed

    def _seed(self) -> None:
        today = date.today()
        with self._con:
            for patient in (
                ("Mary Jane", date(2001, 6, 10), "18005882300", "Anthem"),
                ("Peter Parker", date(2001, 8, 10), "17185551962", "Aetna"),
            ):
                name, dob, phone, insurance = patient
                self._con.execute(
                    "INSERT INTO patients (name, date_of_birth, phone_number, insurance,"
                    " outstanding_balance) VALUES (?, ?, ?, ?, ?)",
                    (name, dob.isoformat(), phone, insurance, round(random.uniform(20, 3000), 2)),
                )
            doctors = (
                (
                    "Dr. Henry Jekyll",
                    ["Anthem", "HealthFirst"],
                    [
                        (today + timedelta(days=2), time(9, 30)),
                        (today + timedelta(days=4), time(14, 30)),
                        (today + timedelta(days=7), time(11, 0)),
                    ],
                ),
                (
                    "Dr. Edward Hyde",
                    ["Anthem", "Aetna", "EmblemHealth"],
                    [
                        (today + timedelta(days=1), time(10, 0)),
                        (today + timedelta(days=3), time(14, 30)),
                        (today + timedelta(days=5), time(15, 45)),
                    ],
                ),
            )
            for name, insurances, slots in doctors:
                cur = self._con.execute(
                    "INSERT INTO doctors (name, accepted_insurances) VALUES (?, ?)",
                    (name, json.dumps(insurances)),
                )
                self._con.executemany(
                    "INSERT INTO availability (doctor_id, date, time) VALUES (?, ?, ?)",
                    [(cur.lastrowid, d.isoformat(), t.isoformat()) for d, t in slots],
                )

    # ----------------------------------------------------------- row -> dict

    def _patient(self, row: sqlite3.Row) -> dict:
        record = {
            "name": row["name"],
            "date_of_birth": date.fromisoformat(row["date_of_birth"]),
            "phone_number": row["phone_number"],
            "insurance": row["insurance"],
            "outstanding_balance": row["outstanding_balance"],
        }
        appointments = [
            {
                "doctor_name": appt["doctor_name"],
                "appointment_time": datetime.fromisoformat(appt["appointment_time"]),
                "visit_reason": appt["visit_reason"],
            }
            for appt in self._con.execute(
                "SELECT * FROM appointments WHERE patient_id = ? ORDER BY appointment_time",
                (row["id"],),
            )
        ]
        # Omitted rather than empty when there are no appointments; agent.py
        # reads it as `record.get("appointments", [])`.
        if appointments:
            record["appointments"] = appointments
        return record

    def _doctor(self, row: sqlite3.Row) -> dict:
        return {
            "name": row["name"],
            "accepted_insurances": json.loads(row["accepted_insurances"]),
            "availability": [
                {
                    "date": date.fromisoformat(slot["date"]),
                    "time": time.fromisoformat(slot["time"]),
                }
                for slot in self._con.execute(
                    "SELECT date, time FROM availability WHERE doctor_id = ?"
                    " ORDER BY date, time",
                    (row["id"],),
                )
            ],
        }

    def _patient_id(self, name: str) -> int | None:
        row = self._con.execute("SELECT id FROM patients WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else None

    # --------------------------------------------------------- public reads

    @property
    def patient_records(self) -> list:
        return [self._patient(row) for row in self._con.execute("SELECT * FROM patients")]

    @property
    def doctor_records(self) -> list:
        return [self._doctor(row) for row in self._con.execute("SELECT * FROM doctors")]

    def get_patient_by_name(self, name: str) -> dict | None:
        row = self._con.execute("SELECT * FROM patients WHERE name = ?", (name,)).fetchone()
        return self._patient(row) if row else None

    def get_patient_by_name_and_dob(self, name: str, dob: date) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM patients WHERE name = ? AND date_of_birth = ?",
            (name, dob.isoformat()),
        ).fetchone()
        return self._patient(row) if row else None

    def get_doctor_by_name(self, name: str) -> dict | None:
        row = self._con.execute("SELECT * FROM doctors WHERE name = ?", (name,)).fetchone()
        return self._doctor(row) if row else None

    def get_compatible_doctors(self, insurance: str) -> list:
        return [
            doctor for doctor in self.doctor_records if insurance in doctor["accepted_insurances"]
        ]

    def get_outstanding_balance(self, name: str) -> float | None:
        row = self._con.execute(
            "SELECT outstanding_balance FROM patients WHERE name = ?", (name,)
        ).fetchone()
        return row["outstanding_balance"] if row else None

    # -------------------------------------------------------- public writes

    def update_patient_record(self, patient_name: str, **fields) -> bool:
        if not fields:
            return self._patient_id(patient_name) is not None
        columns = {
            "name",
            "date_of_birth",
            "phone_number",
            "insurance",
            "outstanding_balance",
        }
        unknown = set(fields) - columns
        if unknown:
            raise ValueError(f"unknown patient fields: {sorted(unknown)}")
        assignments = ", ".join(f"{column} = ?" for column in fields)
        values = [_encode(value) for value in fields.values()]
        with self._con:
            cur = self._con.execute(
                f"UPDATE patients SET {assignments} WHERE name = ?",
                (*values, patient_name),
            )
        return cur.rowcount > 0

    def add_patient_record(self, info: dict) -> None:
        # Mutates `info` in place: agent.py keeps a reference to this dict as
        # the session profile and expects the balance to appear on it.
        info.setdefault("outstanding_balance", round(random.uniform(20, 3000), 2))
        with self._con:
            self._con.execute(
                "INSERT INTO patients (name, date_of_birth, phone_number, insurance,"
                " outstanding_balance) VALUES (?, ?, ?, ?, ?)",
                (
                    info["name"],
                    _encode(info["date_of_birth"]),
                    info.get("phone_number"),
                    info.get("insurance"),
                    info["outstanding_balance"],
                ),
            )

    def add_appointment(self, name: str, appointment: dict) -> bool:
        patient_id = self._patient_id(name)
        if patient_id is None:
            return False
        appt_time = appointment["appointment_time"]
        if isinstance(appt_time, str):
            appt_time = datetime.fromisoformat(appt_time)
        # One transaction: booking the slot and consuming the doctor's
        # availability must not be separable, or a crash double-books.
        with self._con:
            self._con.execute(
                "INSERT INTO appointments (patient_id, doctor_name, appointment_time,"
                " visit_reason) VALUES (?, ?, ?, ?)",
                (
                    patient_id,
                    appointment["doctor_name"],
                    appt_time.isoformat(),
                    appointment.get("visit_reason"),
                ),
            )
            self._remove_availability(appointment["doctor_name"], appt_time.date(), appt_time.time())
        return True

    def cancel_appointment(self, name: str, appointment: dict) -> bool:
        patient_id = self._patient_id(name)
        if patient_id is None:
            return False
        appt_time = appointment["appointment_time"]
        if isinstance(appt_time, str):
            appt_time = datetime.fromisoformat(appt_time)
        # Matched by value: the caller passes back a dict read earlier, which
        # is a snapshot, not a live reference into the store.
        with self._con:
            cur = self._con.execute(
                "DELETE FROM appointments WHERE id = ("
                "  SELECT id FROM appointments WHERE patient_id = ? AND doctor_name = ?"
                "   AND appointment_time = ? LIMIT 1)",
                (patient_id, appointment["doctor_name"], appt_time.isoformat()),
            )
            if cur.rowcount == 0:
                return False
            doctor = self._con.execute(
                "SELECT id FROM doctors WHERE name = ?", (appointment["doctor_name"],)
            ).fetchone()
            if doctor is not None:
                self._con.execute(
                    "INSERT OR IGNORE INTO availability (doctor_id, date, time)"
                    " VALUES (?, ?, ?)",
                    (doctor["id"], appt_time.date().isoformat(), appt_time.time().isoformat()),
                )
        return True

    def apply_payment(self, name: str, amount: float) -> float | None:
        with self._con:
            row = self._con.execute(
                "UPDATE patients SET outstanding_balance ="
                " round(outstanding_balance - ?, 2) WHERE name = ?"
                " RETURNING outstanding_balance",
                (amount, name),
            ).fetchone()
        return row["outstanding_balance"] if row else None

    def remove_doctor_availability(self, doctor_name: str, appointment_time: dict) -> None:
        with self._con:
            self._remove_availability(
                doctor_name, appointment_time["date"], appointment_time["time"]
            )

    def _remove_availability(self, doctor_name: str, slot_date: date, slot_time: time) -> None:
        self._con.execute(
            "DELETE FROM availability WHERE date = ? AND time = ? AND doctor_id ="
            " (SELECT id FROM doctors WHERE name = ?)",
            (_encode(slot_date), _encode(slot_time), doctor_name),
        )


def _encode(value: object) -> object:
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return value
