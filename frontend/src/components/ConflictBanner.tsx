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

  const movedOn = current.version > stale.version;

  return (
    <div className="banner" role="alert">
      <div className="banner-head">
        <svg className="banner-icon" width="17" height="17" viewBox="0 0 17 17" aria-hidden="true">
          <circle cx="8.5" cy="8.5" r="7" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path
            d="M8.5 4.75v4.5M8.5 11.75v.5"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        </svg>

        <div className="banner-text">
          <h2>Someone else changed “{stale.name}”</h2>
          <p>
            {changed.length > 0
              ? "Your copy is out of date. Reload to pick up their version, then reapply your change."
              : "Your copy is out of date. Reload to continue."}
            {movedOn && (
              <span className="mono banner-ver">
                {" "}
                v{stale.version} → v{current.version}
              </span>
            )}
          </p>
        </div>

        <button type="button" className="icon-btn" onClick={onDismiss} aria-label="Dismiss">
          <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden="true">
            <path
              d="M3.5 3.5l8 8M11.5 3.5l-8 8"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>

      {changed.length > 0 && (
        <div className="diff">
          <div className="h">Field</div>
          <div className="h">Your copy</div>
          <div className="h">Theirs</div>
          {changed.map(({ key, label }) => (
            <Fragment key={key}>
              <div className="k">{label}</div>
              <div className="was">{display(stale[key])}</div>
              <div className="now">{display(current[key])}</div>
            </Fragment>
          ))}
        </div>
      )}

      <div className="banner-foot">
        <button type="button" className="btn" onClick={onDismiss}>
          Keep editing
        </button>
        <button type="button" className="btn btn-primary" onClick={onReload}>
          Reload latest
        </button>
      </div>
    </div>
  );
}
