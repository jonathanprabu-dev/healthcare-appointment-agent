// The live column: what the agent is doing, as it does it. Each row is
// labelled in words as well as coloured, so the kind is never colour-alone.

function shortRoom(room) {
  if (!room) return "";
  // "call-_+919884010803_yA3iaRZmzn4E" -> "+919884010803"
  const match = /call-_(\+?\d+)_/.exec(room);
  return match ? match[1] : room;
}

const money = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

function describe(event) {
  const p = event.payload ?? {};
  switch (event.type) {
    case "call.started":
      return { kind: "call", label: "Call", detail: `Inbound from ${shortRoom(p.room)}` };
    case "call.utterance":
      return {
        kind: "call",
        label: p.is_final ? "Caller" : "Caller…",
        detail: p.transcript || "(silence)",
      };
    case "call.activity":
      return { kind: "call", label: "Agent", detail: `Ran ${p.tool}` };
    case "call.ended":
      return {
        kind: "call",
        label: "Call ended",
        detail: `${shortRoom(p.room)} after ${p.duration_seconds}s (${p.reason ?? "unknown"})`,
      };
    case "patient.created":
      return {
        kind: "patient",
        label: "New patient",
        detail: `${p.patient?.name ?? "unknown"} — ${p.patient?.insurance ?? "no insurance"}`,
      };
    case "patient.updated":
      return {
        kind: "patient",
        label: "Patient updated",
        detail: `${p.name}: ${Object.keys(p.fields ?? {}).join(", ") || "record changed"}`,
      };
    case "appointment.scheduled":
      return {
        kind: "appointment",
        label: "Booked",
        detail: `${p.patient_name} with ${p.doctor_name}`,
      };
    case "appointment.cancelled":
      return {
        kind: "appointment",
        label: "Cancelled",
        detail: `${p.patient_name} with ${p.doctor_name}`,
      };
    case "payment.processed":
      return {
        kind: "patient",
        label: "Payment",
        detail: `${p.name ?? p.patient_name ?? "patient"} paid ${money.format(p.amount ?? 0)}`,
      };
    case "doctor.created":
      return {
        kind: "appointment",
        label: "Doctor added",
        detail: `${p.doctor?.name} — ${p.doctor?.specialty}`,
      };
    case "doctor.availability_added":
      return {
        kind: "appointment",
        label: "Slots added",
        detail: `${p.added} for ${p.doctor_name}`,
      };
    default:
      return { kind: "other", label: event.type, detail: "" };
  }
}

function clockTime(seconds) {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function EventFeed({ events }) {
  return (
    <div className="card">
      <h2>Live activity</h2>
      <p className="caption">
        Pushed by the agent as each call happens. Newest first.
      </p>
      {events.length === 0 ? (
        <p className="empty-state">
          Nothing yet. Events appear here the moment a call comes in — if this
          stays empty during a call, <code>DASHBOARD_URL</code> is not set for
          the agent process.
        </p>
      ) : (
        <ul className="feed">
          {events.map((event) => {
            const { kind, label, detail } = describe(event);
            return (
              <li key={event.id ?? `${event.created_at}-${event.type}`} className={event.isNew ? "flash" : undefined}>
                <span className="chip" data-kind={kind}>
                  {label}
                </span>
                <span className="detail">{detail}</span>
                <span className="when">{clockTime(event.created_at)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
