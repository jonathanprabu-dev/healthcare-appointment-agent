"""Concurrency test: concurrent sessions must never see "database is locked".

The agent opens one SqliteDatabase per session. LiveKit runs jobs as threads on
Windows and as separate processes on Linux, so both shapes are exercised here
against a single database file.

    uv run test_sqlite_concurrency.py

The control case at the end deliberately disables busy_timeout to show the
failure this configuration prevents — if the control stops failing, the test
has stopped proving anything.
"""

from __future__ import annotations

import multiprocessing
import sqlite3
import sys
import tempfile
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlite_database import SqliteDatabase

WORKERS = 8
BOOKINGS_PER_WORKER = 25

FAILURES: list[str] = []


def check(label: str, expected: object, actual: object) -> None:
    if expected == actual:
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}\n       expected: {expected!r}\n       actual:   {actual!r}")


def _book(path: str, worker: int, count: int, results: list) -> None:
    """One session's worth of writes: its own connection, like entrypoint()."""
    db = SqliteDatabase(path)
    try:
        for i in range(count):
            # Distinct slot per write, so nothing serializes on the same row.
            booked = db.add_appointment(
                "Mary Jane",
                {
                    "doctor_name": f"Dr. W{worker}",
                    "appointment_time": _slot_for(worker, i),
                    "visit_reason": "load",
                },
            )
            if not booked:
                raise AssertionError(f"worker {worker} write {i} was refused a prepared slot")
            db.apply_payment("Mary Jane", 0.01)
        results.append(None)
    except Exception as exc:  # noqa: BLE001 - the point is to catch lock errors
        results.append(f"worker {worker}: {type(exc).__name__}: {exc}")
    finally:
        db.close()


def _slot_for(worker: int, i: int) -> datetime:
    return datetime.combine(date(2030, 1, 1), time(0, 0)) + timedelta(minutes=worker * 1000 + i)


def prepare_load_fixtures(path: str, rounds: int) -> None:
    """Give every load writer its own doctor and its own offered slots.

    add_appointment claims a slot by deleting it, so a booking for a time that
    was never offered is refused. The load test needs real, bookable slots.
    """
    con = sqlite3.connect(path)
    with con:
        for worker in range(WORKERS):
            cur = con.execute(
                "INSERT INTO doctors (name, specialty, accepted_insurances)"
                " VALUES (?, 'General', '[]')",
                (f"Dr. W{worker}",),
            )
            con.executemany(
                "INSERT INTO availability (doctor_id, date, time) VALUES (?, ?, ?)",
                [
                    (cur.lastrowid, _slot_for(worker, i).date().isoformat(),
                     _slot_for(worker, i).time().isoformat())
                    for i in range(rounds)
                ],
            )
    con.close()


def _book_process(path: str, worker: int, count: int, queue) -> None:  # noqa: ANN001
    results: list = []
    _book(path, worker, count, results)
    queue.put(results[0])


def run_threads(path: str) -> list:
    results: list = []
    threads = [
        threading.Thread(target=_book, args=(path, w, BOOKINGS_PER_WORKER, results))
        for w in range(WORKERS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [r for r in results if r is not None]


def run_processes(path: str) -> list:
    queue: multiprocessing.Queue = multiprocessing.Queue()
    procs = [
        multiprocessing.Process(target=_book_process, args=(path, w, BOOKINGS_PER_WORKER, queue))
        for w in range(WORKERS)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    errors = [queue.get() for _ in procs if not queue.empty()]
    return [e for e in errors if e is not None]


def _control_no_busy_timeout(path: str, worker: int, results: list) -> None:
    """Same writes, but with busy_timeout at its 0ms default."""
    con = sqlite3.connect(path, check_same_thread=False)
    try:
        con.execute("PRAGMA busy_timeout=0")
        for i in range(BOOKINGS_PER_WORKER):
            with con:
                con.execute(
                    "INSERT INTO appointments (patient_id, doctor_name, appointment_time,"
                    " visit_reason) SELECT id, ?, ?, 'control' FROM patients WHERE name = ?",
                    (f"Dr. Control {worker}", f"2031-01-0{worker % 9 + 1}T0{i % 9}:00:00",
                     "Mary Jane"),
                )
        results.append(None)
    except Exception as exc:  # noqa: BLE001
        results.append(f"{type(exc).__name__}: {exc}")
    finally:
        con.close()


def race_for_one_slot() -> None:
    """32 callers confirm the same slot at once. Exactly one may win."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = str(Path(tmp) / "race.db")
        seeded = SqliteDatabase(path, seed=True)
        slot = seeded.get_doctor_by_name("Dr. Henry Jekyll")["availability"][0]
        contested = datetime.combine(slot["date"], slot["time"])
        seeded.close()

        winners: list = []
        barrier = threading.Barrier(32)

        def claim() -> None:
            db = SqliteDatabase(path)
            barrier.wait()  # everyone attempts at the same instant
            try:
                if db.add_appointment(
                    "Mary Jane",
                    {
                        "doctor_name": "Dr. Henry Jekyll",
                        "appointment_time": contested,
                        "visit_reason": "race",
                    },
                ):
                    winners.append(1)
            finally:
                db.close()

        threads = [threading.Thread(target=claim) for _ in range(32)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        check("exactly one caller wins the contested slot", 1, len(winners))
        con = sqlite3.connect(path)
        check(
            "exactly one appointment row exists",
            1,
            con.execute("SELECT COUNT(*) FROM appointments").fetchone()[0],
        )
        check(
            "the slot is gone from availability",
            0,
            con.execute(
                "SELECT COUNT(*) FROM availability WHERE date = ? AND time = ?",
                (slot["date"].isoformat(), slot["time"].isoformat()),
            ).fetchone()[0],
        )
        con.close()


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        # A file per run: add_appointment consumes the slot it books, so the
        # two runs cannot share one set of prepared slots.
        thread_db = str(Path(tmp) / "threads.db")
        process_db = str(Path(tmp) / "processes.db")
        for path in (thread_db, process_db):
            SqliteDatabase(path, seed=True).close()
            prepare_load_fixtures(path, BOOKINGS_PER_WORKER)

        print(f"\n{WORKERS} concurrent threads x {BOOKINGS_PER_WORKER} bookings (Windows shape)")
        check("no errors", [], run_threads(thread_db))

        print(f"\n{WORKERS} concurrent processes x {BOOKINGS_PER_WORKER} bookings (Linux shape)")
        check("no errors", [], run_processes(process_db))

        for label, path in (("threads", thread_db), ("processes", process_db)):
            con = sqlite3.connect(path)
            written = con.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
            check(f"every {label} write landed", WORKERS * BOOKINGS_PER_WORKER, written)
            check("journal_mode", "wal", con.execute("PRAGMA journal_mode").fetchone()[0])
            con.close()

    print("\n32 callers racing for the same slot")
    race_for_one_slot()

    print("\ncontrol: same load with busy_timeout=0")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = str(Path(tmp) / "control.db")
        SqliteDatabase(path, seed=True).close()
        results: list = []
        threads = [
            threading.Thread(target=_control_no_busy_timeout, args=(path, w, results))
            for w in range(WORKERS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        locked = [r for r in results if r and "locked" in r]
        if locked:
            print(f"  ok   busy_timeout=0 fails as expected: {locked[0]}")
        else:
            print("  WARN control did not hit a lock; it proves nothing on this machine")

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
