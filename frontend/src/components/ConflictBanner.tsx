import type { Todo } from "../api/types";

interface ConflictBannerProps {
  /** The snapshot the write was attempted against. */
  stale: Todo;
  /** problem.current from the 409 — what the other user changed it to. */
  current: Todo;
  /** Invalidate queries so the form re-opens against fresh data. */
  onReload: () => void;
  onDismiss: () => void;
}

const DIFF_FIELDS: { key: keyof Todo; label: string }[] = [
  { key: "name", label: "Name" },
  { key: "description", label: "Description" },
  { key: "due_date", label: "Due date" },
  { key: "priority", label: "Priority" },
  { key: "status", label: "Status" },
  { key: "recurrence_unit", label: "Recurrence unit" },
  { key: "recurrence_interval", label: "Recurrence interval" },
];

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" && value.includes("T")) {
    const d = new Date(value);
    if (!Number.isNaN(d.getTime())) return d.toLocaleString();
  }
  return String(value);
}

/**
 * App-wide conflict banner. Any mutation that rejects with VERSION_CONFLICT
 * reports here with the stale snapshot it wrote against; the diff against
 * problem.current is what the other user changed.
 */
export function ConflictBanner({ stale, current, onReload, onDismiss }: ConflictBannerProps) {
  const changed = DIFF_FIELDS.filter(({ key }) => stale[key] !== current[key]);

  return (
    <div
      role="alert"
      style={{
        position: "fixed",
        top: "0.5rem",
        left: "0.5rem",
        right: "0.5rem",
        zIndex: 20,
        padding: "0.75rem 1rem",
        border: "2px solid #b3261e",
        background: "#fff",
        boxShadow: "0 2px 8px rgba(0,0,0,0.25)",
      }}
    >
      <p style={{ margin: 0, fontWeight: 600 }}>
        Conflict: “{stale.name}” was modified by someone else (version {stale.version} →{" "}
        {current.version}).
      </p>
      {changed.length === 0 ? (
        <p style={{ margin: "0.25rem 0 0" }}>No tracked fields differ.</p>
      ) : (
        <ul style={{ margin: "0.25rem 0" }}>
          {changed.map(({ key, label }) => (
            <li key={key}>
              {label}: {display(stale[key])} → {display(current[key])}
            </li>
          ))}
        </ul>
      )}
      <div style={{ marginTop: "0.5rem" }}>
        <button type="button" onClick={onReload}>
          Reload
        </button>{" "}
        <button type="button" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
