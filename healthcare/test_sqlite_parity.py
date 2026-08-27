"""Parity check: SqliteDatabase must behave like FakeDatabase.

Runs the same sequence of operations against both and compares the observable
state after every step. No LLM, no network, no credentials — run it after any
change to either backend:

    uv run test_sqlite_parity.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time

from fake_database import FakeDatabase
from sqlite_database import SqliteDatabase

FAILURES: list[str] = []


def check(label: str, fake: object, sql: object) -> None:
    if fake == sql:
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}\n       fake:   {fake!r}\n       sqlite: {sql!r}")


def both(step: str, fn) -> None:  # noqa: ANN001
    print(f"\n{step}")
    check(step, fn(FAKE), fn(SQL))


def _is_sorted(availability: list[dict]) -> bool:
    keys = [(slot["date"], slot["time"]) for slot in availability]
    return keys == sorted(keys)


FAKE = FakeDatabase()
SQL = SqliteDatabase(":memory:")

# Balances are random per instance; pin them so comparisons are meaningful.
for db in (FAKE, SQL):
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

SLOT = datetime.combine(FAKE.doctor_records[0]["availability"][0]["date"], time(9, 30))
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
#   * FakeDatabase leaves an empty "appointments" key behind once one existed;
#     SqliteDatabase omits it. agent.py reads it as .get("appointments", []).
#   * FakeDatabase appends a restored slot at the end of availability;
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
    # FakeDatabase mutates the caller's dict with a balance; assert the key
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
        "appointments" in FAKE.get_patient_by_name("Mary Jane"),
        "appointments" in SQL.get_patient_by_name("Mary Jane"),
    ),
)
check(
    "restored slot: fake appends, sqlite orders chronologically",
    (False, True),
    (
        _is_sorted(FAKE.get_doctor_by_name("Dr. Henry Jekyll")["availability"]),
        _is_sorted(SQL.get_doctor_by_name("Dr. Henry Jekyll")["availability"]),
    ),
)

# --- persistence: the thing FakeDatabase cannot do ---------------------------

print("\npersistence across connections")
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    path = str(Path(tmp) / "clinic.db")
    first = SqliteDatabase(path)
    first.add_appointment("Mary Jane", dict(APPOINTMENT, appointment_time=SLOT))
    first.close()

    second = SqliteDatabase(path)
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

print()
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("all checks passed")
