import { useCallback, useEffect, useRef, useState } from "react";
import { endpoints, subscribeToEvents } from "./api.js";
import StatTiles from "./components/StatTiles.jsx";
import AvailabilityChart from "./components/AvailabilityChart.jsx";
import EventFeed from "./components/EventFeed.jsx";
import DataTables from "./components/DataTables.jsx";

// Events that mean the clinic tables changed underneath us. A live utterance
// does not; refetching on every syllable would hammer the API for nothing.
const MUTATIONS = new Set([
  "patient.created",
  "patient.updated",
  "appointment.scheduled",
  "appointment.cancelled",
  "payment.processed",
  "doctor.created",
  "doctor.availability_added",
]);

const MAX_FEED = 200;

export default function App() {
  const [summary, setSummary] = useState(null);
  const [doctors, setDoctors] = useState([]);
  const [patients, setPatients] = useState([]);
  const [appointments, setAppointments] = useState([]);
  const [billing, setBilling] = useState([]);
  const [events, setEvents] = useState([]);
  const [status, setStatus] = useState("connecting");
  const [error, setError] = useState(null);
  const [theme, setTheme] = useState(
    () => document.documentElement.dataset.theme ?? "",
  );

  // Held in a ref so the WebSocket callback never closes over a stale copy.
  const refreshRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const [s, d, p, a, b] = await Promise.all([
        endpoints.summary(),
        endpoints.doctors(),
        endpoints.patients(),
        endpoints.appointments(),
        endpoints.billing(),
      ]);
      setSummary(s);
      setDoctors(d);
      setPatients(p);
      setAppointments(a);
      setBilling(b);
      setError(null);
    } catch (err) {
      setError(
        `Cannot reach the dashboard API (${err.message}). Start it with: uv run uvicorn api.main:app --port 8000`,
      );
    }
  }, []);

  refreshRef.current = refresh;

  useEffect(() => {
    refresh();
    endpoints
      .history(MAX_FEED)
      .then((rows) => setEvents(rows.slice().reverse()))
      .catch(() => {
        /* The feed catching up is optional; live events still arrive. */
      });

    const stop = subscribeToEvents({
      onStatus: setStatus,
      onEvent: (event) => {
        setEvents((prev) => [{ ...event, isNew: true }, ...prev].slice(0, MAX_FEED));
        if (MUTATIONS.has(event.type)) refreshRef.current?.();
      },
    });
    return stop;
  }, [refresh]);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
  };

  return (
    <div className="app">
      <header className="topbar">
        <h1>Clinic operations</h1>
        <span className="spacer" />
        <span className="status" data-state={status}>
          <span className="dot" />
          {status === "live" ? "Live" : status[0].toUpperCase() + status.slice(1)}
        </span>
        <button className="ghost" onClick={refresh}>
          Refresh
        </button>
        <button className="ghost" onClick={toggleTheme}>
          {theme === "dark" ? "Light" : "Dark"}
        </button>
      </header>

      {error && <div className="error">{error}</div>}

      <StatTiles summary={summary} />

      <div className="grid">
        <AvailabilityChart doctors={doctors} />
        <EventFeed events={events} />
      </div>

      <DataTables
        appointments={appointments}
        patients={patients}
        billing={billing}
      />
    </div>
  );
}
