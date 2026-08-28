// A count with no trend to show is a number, not a chart -- these stay bare
// stat tiles rather than growing sparklines that would encode nothing.

const money = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const TILES = [
  { key: "calls_today", label: "Calls today", sub: "inbound to the agent" },
  { key: "bookings_today", label: "Booked today", sub: "appointments created" },
  { key: "upcoming_appointments", label: "Upcoming", sub: "still in the future" },
  { key: "patients", label: "Patients", sub: "on file" },
  { key: "doctors", label: "Doctors", sub: "accepting patients" },
  {
    key: "outstanding_balance",
    label: "Outstanding",
    sub: "demo balances, not real money",
    format: (v) => money.format(v ?? 0),
  },
];

export default function StatTiles({ summary }) {
  return (
    <section className="kpis">
      {TILES.map((tile) => (
        <div className="card tile" key={tile.key}>
          <div className="label">{tile.label}</div>
          <div className="value">
            {summary
              ? (tile.format ? tile.format(summary[tile.key]) : (summary[tile.key] ?? 0))
              : "--"}
          </div>
          <div className="sub">{tile.sub}</div>
        </div>
      ))}
    </section>
  );
}
