"""Behaviour tests for SqliteDatabase.

The oracle is `_ReferenceDatabase` below — the original in-memory
implementation this backend replaced, kept here and nowhere else. Comparing
against a known-good reference catches far more than literal expectations
would, and keeping it in the test file keeps it out of the application.

No LLM, no network, no credentials. Run after any change to the database:

    uv run test_sqlite_database.py
"""

from __future__ import annotations

import random
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlite_database import SqliteDatabase


class _ReferenceDatabase:
    def __init__(self):
        self._patient_records = [
            {
                "name": "Mary Jane",
                "date_of_birth": date(2001, 6, 10),
                "phone_number": "18005882300",
                "insurance": "Anthem",
                "outstanding_balance": round(random.uniform(20, 3000), 2),
            },
            {
                "name": "Peter Parker",
                "date_of_birth": date(2001, 8, 10),
                "phone_number": "17185551962",
                "insurance": "Aetna",
                "outstanding_balance": round(random.uniform(20, 3000), 2),
            },
        ]
        today = date.today()
        self._doctor_records = [
            {
                "name": "Dr. Henry Jekyll",
                "accepted_insurances": ["Anthem", "HealthFirst"],
                "availability": [
                    {"date": today + timedelta(days=2), "time": time(9, 30)},
                    {"date": today + timedelta(days=4), "time": time(14, 30)},
                    {"date": today + timedelta(days=7), "time": time(11, 0)},
                ],
            },
            {
                "name": "Dr. Edward Hyde",
                "accepted_insurances": ["Anthem", "Aetna", "EmblemHealth"],
                "availability": [
                    {"date": today + timedelta(days=1), "time": time(10, 0)},
                    {"date": today + timedelta(days=3), "time": time(14, 30)},
                    {"date": today + timedelta(days=5), "time": time(15, 45)},
                ],
            },
        ]

    @property
    def patient_records(self) -> list:
        return self._patient_records

    @property
    def doctor_records(self) -> list:
        return self._doctor_records

    def get_patient_by_name(self, name: str) -> dict | None:
        return next(
            (record for record in self._patient_records if record["name"] == name),
            None,
        )

    def get_patient_by_name_and_dob(self, name: str, dob: date) -> dict | None:
        return next(
            (
                record
                for record in self._patient_records
                if record["name"] == name and record["date_of_birth"] == dob
            ),
            None,
        )

    def get_doctor_by_name(self, name: str) -> dict | None:
        return next(
            (record for record in self._doctor_records if record["name"] == name),
            None,
        )

    def get_compatible_doctors(self, insurance: str) -> list:
        return [
            doctor for doctor in self._doctor_records if insurance in doctor["accepted_insurances"]
        ]

    def update_patient_record(self, patient_name: str, **fields) -> bool:
        record = self.get_patient_by_name(patient_name)
        if record is None:
            return False
        record.update(fields)
        return True

    def add_appointment(self, name: str, appointment: dict) -> bool:
        record = self.get_patient_by_name(name)
        if record is None:
            return False
        record.setdefault("appointments", []).append(appointment)
        appt_time = appointment["appointment_time"]
        if isinstance(appt_time, str):
            appt_time = datetime.fromisoformat(appt_time)
        self.remove_doctor_availability(
            appointment["doctor_name"],
            {
                "date": appt_time.date(),
                "time": appt_time.time(),
            },
        )
        return True

    def cancel_appointment(self, name: str, appointment: dict) -> bool:
        record = self.get_patient_by_name(name)
        if record is None or "appointments" not in record:
            return False
        try:
            record["appointments"].remove(appointment)
        except ValueError:
            return False
        doctor = self.get_doctor_by_name(appointment["doctor_name"])
        if doctor is not None:
            appt_time = appointment["appointment_time"]
            if isinstance(appt_time, str):
                appt_time = datetime.fromisoformat(appt_time)
            doctor["availability"].append(
                {
                    "date": appt_time.date(),
                    "time": appt_time.time(),
                }
            )
        return True

    def add_patient_record(self, info: dict) -> None:
        info.setdefault("outstanding_balance", round(random.uniform(20, 3000), 2))
        self._patient_records.append(info)

    def get_outstanding_balance(self, name: str) -> float | None:
        record = self.get_patient_by_name(name)
        if record is None:
            return None
        return record.get("outstanding_balance", 0.0)

    def apply_payment(self, name: str, amount: float) -> float | None:
        record = self.get_patient_by_name(name)
        if record is None:
            return None
        record["outstanding_balance"] = round(record.get("outstanding_balance", 0.0) - amount, 2)
        return record["outstanding_balance"]

    def remove_doctor_availability(self, doctor_name: str, appointment_time: dict) -> None:
        for doctor in self._doctor_records:
            if doctor["name"] == doctor_name:
                doctor["availability"] = [
                    slot
                    for slot in doctor["availability"]
                    if not (
                        slot["date"] == appointment_time["date"]
                        and slot["time"] == appointment_time["time"]
                    )
                ]


FAILURES: list[str] = []


def _timeout_under_env(value: str) -> int:
    """Reimport the module with the env var set — it is read at import time."""
    import importlib
    import os

    previous = os.environ.get("SQLITE_BUSY_TIMEOUT_MS")
    os.environ["SQLITE_BUSY_TIMEOUT_MS"] = value
    try:
        import sqlite_database

        reloaded = importlib.reload(sqlite_database)
        db = reloaded.SqliteDatabase(":memory:")
        timeout = db._con.execute("PRAGMA busy_timeout").fetchone()[0]
        db.close()
        return timeout
    finally:
        if previous is None:
            del os.environ["SQLITE_BUSY_TIMEOUT_MS"]
        else:
            os.environ["SQLITE_BUSY_TIMEOUT_MS"] = previous
        importlib.reload(sqlite_database)


def check(label: str, reference: object, sql: object) -> None:
    if reference == sql:
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}\n       reference: {reference!r}\n       sqlite:    {sql!r}")


def both(step: str, fn) -> None:  # noqa: ANN001
    print(f"\n{step}")
    check(step, fn(REF), fn(SQL))


def _is_sorted(availability: list[dict]) -> bool:
    keys = [(slot["date"], slot["time"]) for slot in availability]
    return keys == sorted(keys)


REF = _ReferenceDatabase()
SQL = SqliteDatabase(":memory:", seed=True)

# Balances are random per instance; pin them so comparisons are meaningful.
for db in (REF, SQL):
    db.update_patient_record("Mary Jane", outstanding_balance=500.00)
    db.update_patient_record("Peter Parker", outstanding_balance=1200.50)

both("seed: patient_records", lambda db: db.patient_records)
both("seed: doctor_records", lambda db: db.doctor_records)

both(
    "get_patient_by_name_and_dob (hit)",
    lambda db: db.get_patient_by_name_and_dob("Mary Jane", date(2001, 6, 10)),
)
both(
    "get_patient_by_name_and_dob (wrong dob)",
    lambda db: db.get_patient_by_name_and_dob("Mary Jane", date(1999, 1, 1)),
)
both("get_patient_by_name (miss)", lambda db: db.get_patient_by_name("Nobody"))
both("get_doctor_by_name", lambda db: db.get_doctor_by_name("Dr. Henry Jekyll"))
both("get_compatible_doctors (Aetna)", lambda db: db.get_compatible_doctors("Aetna"))
both("get_compatible_doctors (HealthFirst)", lambda db: db.get_compatible_doctors("HealthFirst"))
both("get_compatible_doctors (unknown)", lambda db: db.get_compatible_doctors("Nonesuch"))
both("get_outstanding_balance (miss)", lambda db: db.get_outstanding_balance("Nobody"))

# --- the booking path -------------------------------------------------------

SLOT = datetime.combine(REF.doctor_records[0]["availability"][0]["date"], time(9, 30))
APPOINTMENT = {
    "doctor_name": "Dr. Henry Jekyll",
    "appointment_time": SLOT,
    "visit_reason": "persistent cough",
}

both("add_appointment", lambda db: db.add_appointment("Mary Jane", dict(APPOINTMENT)))
both("after booking: patient", lambda db: db.get_patient_by_name("Mary Jane"))
both("after booking: doctor slots", lambda db: db.get_doctor_by_name("Dr. Henry Jekyll"))
both(
    "add_appointment (unknown patient)",
    lambda db: db.add_appointment("Nobody", dict(APPOINTMENT)),
)

# The caller passes back a dict it read earlier, not the one it stored.
both(
    "cancel_appointment",
    lambda db: db.cancel_appointment(
        "Mary Jane", db.get_patient_by_name("Mary Jane")["appointments"][0]
    ),
)
# Two intentional differences after a cancel, both normalized away here and
# asserted explicitly further down:
#   * the reference leaves an empty "appointments" key behind once one existed;
#     SqliteDatabase omits it. agent.py reads it as .get("appointments", []).
#   * the reference appends a restored slot at the end of availability;
#     SqliteDatabase returns availability in chronological order.
both(
    "after cancel: patient",
    lambda db: {**db.get_patient_by_name("Mary Jane"), "appointments": []},
)
both(
    "after cancel: slot restored",
    lambda db: sorted(
        (slot["date"], slot["time"])
        for slot in db.get_doctor_by_name("Dr. Henry Jekyll")["availability"]
    ),
)
both(
    "cancel_appointment (nothing to cancel)",
    lambda db: db.cancel_appointment("Mary Jane", dict(APPOINTMENT)),
)

# --- profile creation + billing ---------------------------------------------

NEW_PROFILE = {
    "name": "Gwen Stacy",
    "date_of_birth": date(2002, 3, 4),
    "phone_number": "17185550199",
    "insurance": "EmblemHealth",
}


def add_new_patient(db: object) -> dict:
    profile = dict(NEW_PROFILE)
    db.add_patient_record(info=profile)
    # the reference mutates the caller's dict with a balance; assert the key
    # appears without comparing the random value.
    profile["outstanding_balance"] = "<random>"
    return profile


both("add_patient_record (mutates caller dict)", add_new_patient)
both(
    "new patient is retrievable",
    lambda db: {**db.get_patient_by_name("Gwen Stacy"), "outstanding_balance": "<random>"},
)

both("update_patient_record", lambda db: db.update_patient_record("Gwen Stacy", insurance="Aetna"))
both("update_patient_record (miss)", lambda db: db.update_patient_record("Nobody", insurance="X"))
both("after update", lambda db: db.get_patient_by_name("Gwen Stacy")["insurance"])

both("apply_payment", lambda db: db.apply_payment("Mary Jane", 123.45))
both("apply_payment (overpay)", lambda db: db.apply_payment("Mary Jane", 1000.00))
both("apply_payment (miss)", lambda db: db.apply_payment("Nobody", 10.0))
both("balance after payments", lambda db: db.get_outstanding_balance("Mary Jane"))

both(
    "remove_doctor_availability",
    lambda db: db.remove_doctor_availability(
        "Dr. Edward Hyde", {"date": SLOT.date(), "time": time(10, 0)}
    ),
)
both("after removal", lambda db: db.get_doctor_by_name("Dr. Edward Hyde"))
both(
    "remove_doctor_availability (unknown doctor is a no-op)",
    lambda db: db.remove_doctor_availability(
        "Dr. Nobody", {"date": SLOT.date(), "time": time(10, 0)}
    ),
)

# --- intentional differences, asserted so they stay deliberate ---------------

print("\nintentional differences")
check(
    "empty appointments key: fake keeps it, sqlite omits it",
    (True, False),
    (
        "appointments" in REF.get_patient_by_name("Mary Jane"),
        "appointments" in SQL.get_patient_by_name("Mary Jane"),
    ),
)
check(
    "restored slot: fake appends, sqlite orders chronologically",
    (False, True),
    (
        _is_sorted(REF.get_doctor_by_name("Dr. Henry Jekyll")["availability"]),
        _is_sorted(SQL.get_doctor_by_name("Dr. Henry Jekyll")["availability"]),
    ),
)

# --- persistence: the thing the reference cannot do ---------------------------

print("\npersistence across connections")

with tempfile.TemporaryDirectory() as tmp:
    path = str(Path(tmp) / "clinic.db")
    first = SqliteDatabase(path, seed=True)
    first.add_appointment("Mary Jane", dict(APPOINTMENT, appointment_time=SLOT))
    first.close()

    second = SqliteDatabase(path, seed=True)
    reloaded = second.get_patient_by_name("Mary Jane")
    check(
        "appointment survives reopen",
        [APPOINTMENT["doctor_name"]],
        [appt["doctor_name"] for appt in reloaded.get("appointments", [])],
    )
    check(
        "appointment_time round-trips as datetime",
        SLOT,
        reloaded["appointments"][0]["appointment_time"],
    )
    second.close()

print("\nconnection settings")
_settings = SqliteDatabase(":memory:")
check(
    "busy_timeout defaults to 5s",
    5000,
    _settings._con.execute("PRAGMA busy_timeout").fetchone()[0],
)
check("SQLITE_BUSY_TIMEOUT_MS overrides it", 250, _timeout_under_env("250"))
_settings.close()
# WAL is a property of the file, and ":memory:" always reports "memory";
# test_sqlite_concurrency.py checks journal_mode on a real file.

print("\nseeding is opt-in")
with tempfile.TemporaryDirectory() as tmp:
    empty = SqliteDatabase(str(Path(tmp) / "prod.db"))   # default args
    check("a fresh database with default args holds no patients", [], empty.patient_records)
    check("...and no doctors", [], empty.doctor_records)
    empty.close()


print("\nslot claiming (no reference equivalent: the reference never refused)")
_claim = SqliteDatabase(":memory:", seed=True)
_slot = _claim.get_doctor_by_name("Dr. Edward Hyde")["availability"][0]
_booking = {
    "doctor_name": "Dr. Edward Hyde",
    "appointment_time": datetime.combine(_slot["date"], _slot["time"]),
    "visit_reason": "first",
}
check("first booking of an offered slot succeeds", True, _claim.add_appointment("Mary Jane", dict(_booking)))
check(
    "second booking of the same slot is refused",
    False,
    _claim.add_appointment("Peter Parker", dict(_booking, visit_reason="second")),
)
check(
    "a time that was never offered is refused",
    False,
    _claim.add_appointment(
        "Mary Jane",
        {
            "doctor_name": "Dr. Edward Hyde",
            "appointment_time": datetime(2031, 7, 4, 3, 0),
            "visit_reason": "invented",
        },
    ),
)
check("only the winner has an appointment", 1, len(_claim.get_patient_by_name("Mary Jane")["appointments"]))
check("the loser has none", None, _claim.get_patient_by_name("Peter Parker").get("appointments"))
_claim.close()

print()
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("all checks passed")
