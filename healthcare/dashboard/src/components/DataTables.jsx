import { useState } from "react";

const money = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

function when(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso ?? "";
  return d.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

const VIEWS = {
  Appointments: {
    columns: ["Patient", "Doctor", "When", "Reason"],
    row: (a) => [a.patient_name, a.doctor_name, when(a.appointment_time), a.visit_reason],
    key: (a) => `${a.patient_name}-${a.appointment_time}`,
    empty: "No appointments booked yet.",
  },
  Patients: {
    columns: ["Name", "Date of birth", "Phone", "Insurance"],
    row: (p) => [p.name, p.date_of_birth, p.phone_number, p.insurance],
    key: (p) => p.name,
    empty: "No patients on file. The agent creates them mid-call.",
  },
  Billing: {
    columns: ["Patient", "Insurance", "Outstanding"],
    row: (b) => [b.patient_name, b.insurance, money.format(b.outstanding_balance ?? 0)],
    key: (b) => b.patient_name,
    empty: "Nothing outstanding.",
  },
};

export default function DataTables({ appointments, patients, billing }) {
  const [view, setView] = useState("Appointments");
  const data = { Appointments: appointments, Patients: patients, Billing: billing }[view] ?? [];
  const spec = VIEWS[view];

  return (
    <div className="card" style={{ marginTop: "var(--gap)" }}>
      <div className="tabs" role="tablist" aria-label="Clinic records">
        {Object.keys(VIEWS).map((name) => (
          <button
            key={name}
            className="tab"
            role="tab"
            aria-selected={view === name}
            onClick={() => setView(name)}
          >
            {name}
          </button>
        ))}
      </div>

      {data.length === 0 ? (
        <p className="empty-state">{spec.empty}</p>
      ) : (
        <table>
          <thead>
            <tr>
              {spec.columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((item) => (
              <tr key={spec.key(item)}>
                {spec.row(item).map((cell, i) => (
                  <td key={i}>{cell ?? "—"}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
