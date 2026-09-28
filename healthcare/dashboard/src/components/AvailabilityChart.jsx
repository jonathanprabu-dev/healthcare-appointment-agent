import { useMemo, useState } from "react";

// Open slots per doctor: one measure, one series, so there is no legend --
// the heading names what the bars are. Every bar carries its own number at the
// end, which is also what discharges the palette's contrast relief rule.

function nextOpening(doctor) {
  const slot = doctor.availability?.[0];
  if (!slot) return "none open";
  const when = new Date(`${slot.date}T${slot.time}`);
  if (Number.isNaN(when.getTime())) return `${slot.date} ${slot.time}`;
  return when.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function AvailabilityChart({ doctors }) {
  const [specialty, setSpecialty] = useState("All");
  const [asTable, setAsTable] = useState(false);

  const specialties = useMemo(() => {
    const seen = new Set(doctors.map((d) => d.specialty ?? "General"));
    return ["All", ...[...seen].sort()];
  }, [doctors]);

  const rows = useMemo(() => {
    return doctors
      .filter((d) => specialty === "All" || (d.specialty ?? "General") === specialty)
      .map((d) => ({
        name: d.name,
        specialty: d.specialty ?? "General",
        open: d.availability?.length ?? 0,
        next: nextOpening(d),
        insurances: d.accepted_insurances ?? [],
      }))
      .sort((a, b) => b.open - a.open);
  }, [doctors, specialty]);

  // Scale to the widest bar in view, with a floor so a lone doctor with one
  // slot does not render as a full-width bar implying "fully booked".
  const max = Math.max(1, ...rows.map((r) => r.open));

  return (
    <div className="card">
      <h2>Open slots by doctor</h2>
      <p className="caption">
        Bars are unbooked appointment times on file. The agent offers only these.
      </p>

      <div className="tabs" role="tablist" aria-label="Filter by specialty">
        {specialties.map((s) => (
          <button
            key={s}
            className="tab"
            role="tab"
            aria-selected={specialty === s}
            onClick={() => setSpecialty(s)}
          >
            {s}
          </button>
        ))}
        <span style={{ flex: 1 }} />
        <button className="tab" onClick={() => setAsTable((v) => !v)}>
          {asTable ? "Chart view" : "Table view"}
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="empty-state">
          No doctors in this specialty. Add one with <code>admin.py add-doctor</code>.
        </p>
      ) : asTable ? (
        <table>
          <thead>
            <tr>
              <th>Doctor</th>
              <th>Specialty</th>
              <th>Open slots</th>
              <th>Next opening</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td>{r.name}</td>
                <td>{r.specialty}</td>
                <td>{r.open}</td>
                <td>{r.next}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div>
          {rows.map((r) => (
            <div
              className="bar-row"
              key={r.name}
              title={`${r.name} — ${r.specialty}\n${r.open} open slots\nNext: ${r.next}\nAccepts: ${r.insurances.join(", ")}`}
            >
              <div className="name">
                {r.name}
                <div className="specialty">{r.specialty}</div>
              </div>
              <div className="track">
                <div
                  className="fill"
                  style={{ width: `${Math.max(2, (r.open / max) * 100)}%` }}
                />
              </div>
              <div className="count">{r.open}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
