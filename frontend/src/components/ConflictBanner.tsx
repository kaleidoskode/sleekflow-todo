import { Fragment } from "react";
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
    <div className="banner" role="alert">
      <div className="banner-head">
        <h2>
          Someone else changed “{stale.name}”
          <span className="mono" style={{ fontWeight: 400, opacity: 0.75 }}>
            {" "}
            v{stale.version} → v{current.version}
          </span>
        </h2>
        <button type="button" className="btn btn-sm" onClick={onReload}>
          Reload
        </button>
        <button type="button" className="btn btn-sm" onClick={onDismiss}>
          Dismiss
        </button>
      </div>

      {changed.length === 0 ? (
        <p className="panel-body" style={{ margin: 0, fontSize: 13.5, color: "var(--muted)" }}>
          Your edit was rejected, but no tracked field differs — the version moved on its own.
        </p>
      ) : (
        <div className="diff">
          <div className="h">Field</div>
          <div className="h">You had</div>
          <div className="h">Now</div>
          {changed.map(({ key, label }) => (
            <Fragment key={key}>
              <div className="k">{label}</div>
              <div className="was">{display(stale[key])}</div>
              <div className="now">{display(current[key])}</div>
            </Fragment>
          ))}
        </div>
      )}
    </div>
  );
}
