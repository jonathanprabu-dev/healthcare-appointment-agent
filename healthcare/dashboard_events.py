"""Fire-and-forget event push from the voice agent to the dashboard backend.

The agent writes to the clinic database; the dashboard backend (`api/`) wants
to know about each write the moment it happens. This module is that pipe: every
DB mutation and every interesting session moment becomes a small JSON event
that is POSTed to the backend's ingest endpoint, which fans it out to browsers
over WebSocket.

`DASHBOARD_URL` is read at import time. Unset, the log is a no-op and the agent
behaves exactly as before — the dashboard is a strictly optional addition.

    from dashboard_events import DashboardEventLog
    events = DashboardEventLog()          # reads $DASHBOARD_URL
    db.set_event_sink(events.post)        # DB mutations land on the dashboard
    events.post({"type": "call.started", "payload": {...}})   # session moments

The log is bounded and drops the oldest events first rather than ever blocking
a voice call: losing a dashboard frame is cosmetic, holding up the caller is
not.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque

import httpx

logger = logging.getLogger("DashboardEvents")

# Max events buffered here waiting on the wire. A call can generate one
# utterance event per user turn plus a mutation or two; 2000 is about 30
# minutes of a busy call, far beyond any realistic gap between POSTs.
MAX_BUFFER = 2000

# POSTs happen in bursts (several events between turns); this adds a little
# hold-off so they batch into one request to the backend.
FLUSH_INTERVAL_SECONDS = 0.5

# Give the POST one quick retry; if it still fails, drop the batch. The agent
# never blocks a call on dashboard connectivity.
POST_TIMEOUT_SECONDS = 2.0


class DashboardEventLog:
    def __init__(self, url: str | None = None) -> None:
        self._url = (url or os.getenv("DASHBOARD_URL", "")).rstrip("/")
        self._queue: deque[dict] = deque(maxlen=MAX_BUFFER)
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._closed = False

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def post(self, event: dict) -> None:
        """Queue one event. Never raises, never blocks the caller."""
        if not self.enabled:
            return
        with self._cond:
            self._queue.append(event)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._drain_loop, name="dashboard-events", daemon=True
                )
                self._thread.start()
            self._cond.notify()

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        with self._cond:
            self._thread = threading.Thread(
                target=self._drain_loop, name="dashboard-events", daemon=True
            )
            self._thread.start()

    def close(self) -> None:
        """Drain what is left and stop the worker (best effort)."""
        if not self.enabled:
            return
        self._closed = True
        with self._cond:
            self._cond.notify()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=POST_TIMEOUT_SECONDS * 2 + 1)

    # ------------------------------------------------------------- internal

    def _drain_loop(self) -> None:
        while not self._closed:
            batch = self._take_batch()
            if batch:
                self._send(batch)
            else:
                with self._cond:
                    # No events right now; wait for the next push or flush out.
                    self._cond.wait(timeout=FLUSH_INTERVAL_SECONDS)

    def _take_batch(self) -> list[dict]:
        with self._cond:
            if not self._queue:
                return []
            if len(self._queue) == 1:
                return [self._queue.popleft()]
            batch = list(self._queue)
            self._queue.clear()
        return batch

    def _send(self, batch: list[dict]) -> None:
        for attempt in range(2):
            try:
                resp = httpx.post(
                    f"{self._url}/api/ingest/events",
                    json=batch,
                    timeout=POST_TIMEOUT_SECONDS,
                )
                if resp.status_code == 200:
                    return
                logger.warning(
                    "dashboard ingest returned %s: %s", resp.status_code, resp.text[:200]
                )
            except httpx.HTTPError as exc:
                logger.debug("dashboard ingest attempt %s failed: %s", attempt + 1, exc)
        # Give up on the batch. Log one line so lost events are visible in the
        # agent log without flooding it with one line per event.
        logger.warning(
            "dropped %s dashboard event(s): ingest unreachable at %s", len(batch), self._url
        )


def _json_default(obj: object) -> str:
    if isinstance(obj, (time.struct_time,)):
        return time.strftime("%Y-%m-%dT%H:%M:%S", obj)
    try:
        return obj.isoformat()  # type: ignore[attr-defined]
    except AttributeError:
        return str(obj)


def to_json_bytes(event: dict) -> bytes:
    """Serialize an event dict the API will accept (dates/times -> ISO)."""
    return json.dumps(
        {
            "type": event.get("type", "unknown"),
            "payload": event.get("payload", {}),
            "created_at": event.get("created_at", time.time()),
        },
        default=_json_default,
    ).encode("utf-8")