"""Admin CLI for the clinic database — the path that puts real data in.

The agent can create a patient mid-call, but nothing else: doctors, the
insurances they accept, and the slots they offer have to come from somewhere.
That somewhere is here.

    uv run admin.py --help
    uv run admin.py add-doctor "Dr. Ada Chen" --insurances Anthem Aetna
    uv run admin.py add-slots "Dr. Ada Chen" --from 2026-09-01 --to 2026-09-30 \
        --times 09:00 09:30 10:00 --weekdays mon tue wed thu fri
    uv run admin.py add-patient "Ada Lovelace" --dob 1990-12-10 \
        --phone 15551234567 --insurance Anthem
    uv run admin.py appointments

Operates on CLINIC_DB (default clinic.db); override with --db. The database is
created empty if it does not exist — seeding demo fixtures is a separate,
explicit command so a real deployment never gets fictional patients by
accident.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, time, timedelta

from sqlite_database import SqliteDatabase

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _time(value: str) -> time:
    return time.fromisoformat(value)


def cmd_add_patient(db: SqliteDatabase, args: argparse.Namespace) -> int:
    if db.get_patient_by_name(args.name):
        print(f"error: a patient named {args.name!r} already exists", file=sys.stderr)
        return 1
    record = {
        "name": args.name,
        "date_of_birth": args.dob,
        "phone_number": args.phone,
        "insurance": args.insurance,
    }
    if args.balance is not None:
        record["outstanding_balance"] = args.balance
    db.add_patient_record(info=record)
    print(f"added patient {args.name} (balance {record['outstanding_balance']:.2f})")
    return 0


def cmd_add_doctor(db: SqliteDatabase, args: argparse.Namespace) -> int:
    if not db.add_doctor(args.name, args.insurances):
        print(f"error: a doctor named {args.name!r} already exists", file=sys.stderr)
        return 1
    print(f"added doctor {args.name} accepting {', '.join(args.insurances) or '(nothing)'}")
    return 0


def cmd_add_slots(db: SqliteDatabase, args: argparse.Namespace) -> int:
    if db.get_doctor_by_name(args.name) is None:
        print(f"error: no doctor named {args.name!r}", file=sys.stderr)
        return 1
    if args.to < getattr(args, "from"):
        print("error: --to is before --from", file=sys.stderr)
        return 1

    wanted = {WEEKDAYS[d] for d in args.weekdays} if args.weekdays else set(range(7))
    booked = db.booked_times(args.name)

    slots: list[tuple[date, time]] = []
    day = getattr(args, "from")
    while day <= args.to:
        if day.weekday() in wanted:
            for slot_time in args.times:
                if datetime.combine(day, slot_time) not in booked:
                    slots.append((day, slot_time))
        day += timedelta(days=1)

    added = db.add_availability(args.name, slots)
    skipped = len(slots) - added
    print(f"offered {added} new slot(s) for {args.name}", end="")
    print(f" ({skipped} already offered)" if skipped else "")
    return 0


def cmd_list_patients(db: SqliteDatabase, _args: argparse.Namespace) -> int:
    records = db.patient_records
    if not records:
        print("(no patients)")
        return 0
    for record in records:
        appointments = record.get("appointments", [])
        print(
            f"{record['name']:<24} {record['date_of_birth']}  {record['insurance'] or '-':<14}"
            f" balance {record['outstanding_balance']:>9.2f}  {len(appointments)} appt(s)"
        )
    return 0


def cmd_list_doctors(db: SqliteDatabase, _args: argparse.Namespace) -> int:
    records = db.doctor_records
    if not records:
        print("(no doctors)")
        return 0
    for record in records:
        availability = record["availability"]
        window = ""
        if availability:
            window = f"  {availability[0]['date']} .. {availability[-1]['date']}"
        print(
            f"{record['name']:<24} {', '.join(record['accepted_insurances']) or '-':<34}"
            f" {len(availability):>4} open{window}"
        )
    return 0


def cmd_appointments(db: SqliteDatabase, _args: argparse.Namespace) -> int:
    records = db.appointments()
    if not records:
        print("(no appointments)")
        return 0
    for record in records:
        print(
            f"{record['appointment_time']:%Y-%m-%d %H:%M}  {record['patient_name']:<24}"
            f" {record['doctor_name']:<24} {record['visit_reason'] or '-'}"
        )
    return 0


def cmd_seed_demo(db: SqliteDatabase, _args: argparse.Namespace) -> int:
    if db.patient_records:
        print("error: database is not empty; refusing to seed", file=sys.stderr)
        return 1
    db._seed()
    print("seeded the demo fixtures (2 fictional patients, 2 fictional doctors)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        default=os.getenv("CLINIC_DB", "clinic.db"),
        help="database path (default: $CLINIC_DB, else clinic.db)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add-patient", help="register a patient")
    p.add_argument("name")
    p.add_argument("--dob", type=_date, required=True, metavar="YYYY-MM-DD")
    p.add_argument("--phone", required=True)
    p.add_argument("--insurance", required=True)
    p.add_argument("--balance", type=float, help="opening balance (default: random demo value)")
    p.set_defaults(func=cmd_add_patient)

    p = sub.add_parser("add-doctor", help="register a doctor")
    p.add_argument("name")
    p.add_argument("--insurances", nargs="*", default=[], metavar="NAME")
    p.set_defaults(func=cmd_add_doctor)

    p = sub.add_parser("add-slots", help="offer availability over a date range")
    p.add_argument("name", help="doctor name")
    p.add_argument("--from", type=_date, required=True, metavar="YYYY-MM-DD")
    p.add_argument("--to", type=_date, required=True, metavar="YYYY-MM-DD")
    p.add_argument("--times", type=_time, nargs="+", required=True, metavar="HH:MM")
    p.add_argument("--weekdays", nargs="*", choices=sorted(WEEKDAYS), metavar="DAY")
    p.set_defaults(func=cmd_add_slots)

    sub.add_parser("list-patients", help="list patients").set_defaults(func=cmd_list_patients)
    sub.add_parser("list-doctors", help="list doctors and open slots").set_defaults(
        func=cmd_list_doctors
    )
    sub.add_parser("appointments", help="list every booked appointment").set_defaults(
        func=cmd_appointments
    )
    sub.add_parser("seed-demo", help="insert the demo fixtures into an empty database").set_defaults(
        func=cmd_seed_demo
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = SqliteDatabase(args.db)
    try:
        return args.func(db, args)
    except sqlite3.IntegrityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
