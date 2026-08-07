import { useRef } from "react";

interface DueDateFieldProps {
  /** `<input type="datetime-local">` value — local wall time, no timezone. */
  value: string;
  onChange: (value: string) => void;
  invalid?: boolean;
}

/** Local wall time in the shape `datetime-local` expects. */
function toInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

/** `days` from now at 09:00 local — a sensible default hour for a deadline. */
function daysFromNow(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setHours(9, 0, 0, 0);
  return toInputValue(d);
}

const QUICK: { label: string; days: number }[] = [
  { label: "Today", days: 0 },
  { label: "Tomorrow", days: 1 },
  { label: "Next week", days: 7 },
];

export function DueDateField({ value, onChange, invalid }: DueDateFieldProps) {
  const ref = useRef<HTMLInputElement>(null);

  /**
   * Browsers only open the picker from the small calendar glyph. `showPicker()`
   * lets the whole control be the target, which is what people expect. It
   * throws when unsupported or outside a user gesture, so failure just falls
   * back to normal typing.
   */
  function openPicker() {
    try {
      ref.current?.showPicker();
    } catch {
      /* keyboard entry still works */
    }
  }

  return (
    <div className="field">
      <span>Due</span>

      <div className="due-control" data-invalid={invalid} onClick={openPicker}>
        <svg className="due-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <rect x="2" y="3.25" width="12" height="11" rx="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <path d="M2 6.75h12M5.5 1.75v2.5M10.5 1.75v2.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>

        <input
          ref={ref}
          id="todo-due-date"
          type="datetime-local"
          className="due-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-label="Due date and time"
          aria-invalid={invalid}
        />

        {value !== "" && (
          <button
            type="button"
            className="due-clear"
            aria-label="Clear due date"
            onClick={(e) => {
              e.stopPropagation();
              onChange("");
            }}
          >
            <svg width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
              <path d="M3 3l7 7M10 3l-7 7" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
            </svg>
          </button>
        )}
      </div>

      <div className="due-quick">
        {QUICK.map(({ label, days }) => (
          <button
            key={label}
            type="button"
            className="due-chip"
            onClick={() => onChange(daysFromNow(days))}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
