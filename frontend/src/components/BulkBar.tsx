import type { BulkResult } from "../api/todos";
import type { Status } from "../api/types";

interface BulkBarProps {
  count: number;
  total: number;
  allSelected: boolean;
  busy: boolean;
  outcome: BulkResult | null;
  onToggleAll: () => void;
  onClear: () => void;
  onStatus: (status: Status) => void;
  onDelete: () => void;
}

const ACTIONS: { label: string; status: Status; tone: string }[] = [
  { label: "Start", status: "in_progress", tone: "ip" },
  { label: "Complete", status: "completed", tone: "cp" },
  { label: "Archive", status: "archived", tone: "ar" },
];

function CheckIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">
      <path
        d="M2.5 7.5l3 3 6-7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Summarises a partial result honestly.
 *
 * The backend answers per item, so "12 completed" alone would be a lie when
 * three were refused. Failures are grouped by reason rather than listed one by
 * one — a batch usually fails for one or two reasons, and fifteen copies of
 * the same sentence is noise. Each failing row also keeps its own message.
 */
function Summary({ outcome }: { outcome: BulkResult }) {
  if (outcome.failed === 0) {
    return (
      <p className="bulk-summary" data-kind="ok" role="status">
        <CheckIcon />
        {outcome.succeeded} {outcome.succeeded === 1 ? "todo" : "todos"} updated.
      </p>
    );
  }

  const reasons = new Map<string, number>();
  for (const r of outcome.results) {
    if (r.ok || r.detail === null) continue;
    reasons.set(r.detail, (reasons.get(r.detail) ?? 0) + 1);
  }

  return (
    <div className="bulk-summary" data-kind="partial" role="status">
      <b>
        {outcome.succeeded} updated, {outcome.failed} not.
      </b>
      <ul>
        {[...reasons].map(([detail, n]) => (
          <li key={detail}>
            {n > 1 && <span className="bulk-count">{n}×</span>}
            {detail}
          </li>
        ))}
      </ul>
      <span className="bulk-hint">The ones that failed are still selected.</span>
    </div>
  );
}

/** Sticky header for the list: select-all, batch actions, and the last result. */
export function BulkBar({
  count,
  total,
  allSelected,
  busy,
  outcome,
  onToggleAll,
  onClear,
  onStatus,
  onDelete,
}: BulkBarProps) {
  const active = count > 0;

  return (
    <div className="bulk-bar" data-active={active}>
      <div className="bulk-row">
        <label className="bulk-check">
          <input
            type="checkbox"
            checked={allSelected}
            // Distinguishes "some selected" from "all selected" — without it a
            // partial selection renders identically to none.
            ref={(el) => {
              if (el) el.indeterminate = active && !allSelected;
            }}
            onChange={onToggleAll}
            aria-label={allSelected ? "Clear selection" : `Select all ${total} loaded todos`}
          />
          <span>{active ? `${count} selected` : "Select"}</span>
        </label>

        {active && (
          <>
            <span className="bulk-sep" aria-hidden="true" />
            <div className="bulk-actions">
              {ACTIONS.map((a) => (
                <button
                  key={a.status}
                  type="button"
                  className="btn btn-sm bulk-act"
                  data-tone={a.tone}
                  disabled={busy}
                  onClick={() => onStatus(a.status)}
                >
                  {a.label}
                </button>
              ))}
              <button
                type="button"
                className="btn btn-sm bulk-act"
                data-tone="danger"
                disabled={busy}
                onClick={onDelete}
              >
                Delete
              </button>
            </div>
            <button type="button" className="bulk-clear" onClick={onClear} disabled={busy}>
              Clear
            </button>
          </>
        )}

        {busy && <span className="bulk-busy">Working…</span>}
      </div>

      {outcome !== null && !busy && <Summary outcome={outcome} />}
    </div>
  );
}
