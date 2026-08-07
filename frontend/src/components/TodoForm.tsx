import { useState } from "react";
import type { FormEvent } from "react";
import type { UseMutationResult } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import type {
  Priority,
  RecurrenceUnit,
  Todo,
  TodoCreatePayload,
  TodoUpdatePayload,
} from "../api/types";

const PRIORITIES: Priority[] = ["low", "medium", "high"];
const RECURRENCE_UNITS: RecurrenceUnit[] = ["day", "week", "month"];

/**
 * The API models recurrence as unit + interval, but nobody thinks in those
 * terms — they think "weekly". Presets cover the common cases in one click
 * and only reveal the raw pair when someone actually wants "every 3 days".
 */
type PresetId = "never" | "day" | "week" | "month" | "custom";

const REPEAT_PRESETS: { id: PresetId; label: string }[] = [
  { id: "never", label: "Never" },
  { id: "day", label: "Daily" },
  { id: "week", label: "Weekly" },
  { id: "month", label: "Monthly" },
  { id: "custom", label: "Custom" },
];

function presetFor(unit: RecurrenceUnit | "", interval: string): PresetId {
  if (unit === "") return "never";
  return interval === "1" ? unit : "custom";
}

const FIELD_ERROR_TARGETS = new Set([
  "name",
  "description",
  "due_date",
  "priority",
  "recurrence_unit",
  "recurrence_interval",
]);

interface TodoFormProps {
  /** The todo being edited, or null for create mode. */
  todo: Todo | null;
  create: UseMutationResult<Todo, Error, TodoCreatePayload>;
  update: UseMutationResult<Todo, Error, { todo: Todo; changes: TodoUpdatePayload }>;
  /** Called after a successful create/update (and after a no-op edit). */
  onDone: () => void;
  onCancel: () => void;
  /** Called when a mutation rejects with VERSION_CONFLICT so the app-wide banner can show. */
  onConflict: (stale: Todo, current: Todo) => void;
}

/** Server ISO timestamp -> `<input type="datetime-local">` value (local wall time). */
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

/** `<input type="datetime-local">` value -> ISO timestamp for the API. */
function toIso(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export function TodoForm({ todo, create, update, onDone, onCancel, onConflict }: TodoFormProps) {
  const [name, setName] = useState(todo?.name ?? "");
  const [description, setDescription] = useState(todo?.description ?? "");
  const [dueDate, setDueDate] = useState(toLocalInput(todo?.due_date ?? null));
  const [priority, setPriority] = useState<Priority>(todo?.priority ?? "medium");
  const [recurrenceUnit, setRecurrenceUnit] = useState<RecurrenceUnit | "">(
    todo?.recurrence_unit ?? "",
  );
  const [recurrenceInterval, setRecurrenceInterval] = useState(
    todo?.recurrence_interval != null ? String(todo.recurrence_interval) : "",
  );
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [recurrenceError, setRecurrenceError] = useState<string | null>(null);
  const [generalError, setGeneralError] = useState<string | null>(null);

  const recurrenceDisabled = dueDate === "";
  const isSubmitting = create.isPending || update.isPending;

  function clearErrors() {
    setFieldErrors({});
    setRecurrenceError(null);
    setGeneralError(null);
  }

  const activePreset = presetFor(recurrenceUnit, recurrenceInterval);

  function applyPreset(id: PresetId) {
    if (id === "never") {
      setRecurrenceUnit("");
      setRecurrenceInterval("");
      return;
    }
    if (id === "custom") {
      // Seed the raw controls from whatever is set so Custom never starts empty.
      setRecurrenceUnit(recurrenceUnit === "" ? "day" : recurrenceUnit);
      setRecurrenceInterval(recurrenceInterval === "" || recurrenceInterval === "1" ? "2" : recurrenceInterval);
      return;
    }
    setRecurrenceUnit(id);
    setRecurrenceInterval("1");
  }

  function handleDueDateChange(value: string) {
    setDueDate(value);
    // Recurrence needs a due_date anchor server-side; dropping the date must
    // drop the schedule rather than submit a doomed combination.
    if (value === "") {
      setRecurrenceUnit("");
      setRecurrenceInterval("");
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    clearErrors();

    const trimmedName = name.trim();
    if (trimmedName === "") {
      setFieldErrors((prev) => ({ ...prev, name: "Name is required." }));
      return;
    }

    let interval: number | null = null;
    if (recurrenceUnit !== "") {
      const parsed = Number(recurrenceInterval);
      if (!Number.isInteger(parsed) || parsed < 1 || parsed > 365) {
        setRecurrenceError("Interval must be a whole number between 1 and 365.");
        return;
      }
      interval = parsed;
    }
    // Recurrence without an anchor cannot be submitted through this form (the
    // group is disabled), but guard anyway so a programmatic submit never sends it.
    const unit: RecurrenceUnit | null = recurrenceUnit !== "" && dueDate !== "" ? recurrenceUnit : null;

    const payload = {
      name: trimmedName,
      description: description === "" ? null : description,
      due_date: toIso(dueDate),
      priority,
      recurrence_unit: unit,
      recurrence_interval: unit !== null ? interval : null,
    };

    try {
      if (todo === null) {
        await create.mutateAsync(payload);
      } else {
        const changes = diffChanges(todo, trimmedName, description, dueDate, priority, unit, interval);
        if (Object.keys(changes).length === 0) {
          onDone(); // nothing changed; nothing to send
          return;
        }
        await update.mutateAsync({ todo, changes });
      }
      onDone();
    } catch (err) {
      if (!(err instanceof ApiError)) {
        setGeneralError(err instanceof Error ? err.message : String(err));
        return;
      }
      switch (err.code) {
        case "VERSION_CONFLICT": {
          // Keep the form open so the banner's Reload -> reapply flow works.
          if (todo !== null && err.problem.current) {
            onConflict(todo, err.problem.current);
          } else {
            setGeneralError(err.problem.detail);
          }
          break;
        }
        case "INVALID_RECURRENCE":
          // Distinct from VALIDATION_ERROR: it is about the recurrence group.
          setRecurrenceError(err.problem.detail);
          break;
        case "VALIDATION_ERROR":
          for (const e of err.problem.errors ?? []) {
            if (FIELD_ERROR_TARGETS.has(e.field)) {
              setFieldErrors((prev) => ({ ...prev, [e.field]: e.message }));
            } else {
              // Model-level validators attach to the body, not a field (loc
              // ("body",) -> field ""), e.g. the recurrence pairing rule.
              setGeneralError(e.message);
            }
          }
          break;
        default:
          setGeneralError(`${err.code}: ${err.problem.detail}`);
      }
    }
  }

  return (
    <form className="panel" onSubmit={handleSubmit} noValidate>
      <div className="panel-head">
        <h2>{todo === null ? "New todo" : "Edit todo"}</h2>
        <button
          type="button"
          className="icon-btn"
          onClick={onCancel}
          disabled={isSubmitting}
          aria-label="Close"
        >
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
      <div className="panel-body">
        <div className="hero-field">
          <input
            id="todo-name"
            className="hero-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="What needs doing?"
            aria-label="Name"
            aria-invalid={fieldErrors.name !== undefined}
            autoFocus
          />
          {fieldErrors.name && (
            <p className="err" role="alert">
              {fieldErrors.name}
            </p>
          )}
        </div>

        <label className="field">
          <span>Notes</span>
          <textarea
            id="todo-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="Anything worth remembering…"
            aria-invalid={fieldErrors.description !== undefined}
          />
          {fieldErrors.description && (
            <p className="err" role="alert">
              {fieldErrors.description}
            </p>
          )}
        </label>

        <div className="row-2">
          <label className="field">
            <span>Due</span>
            <input
              id="todo-due-date"
              type="datetime-local"
              value={dueDate}
              onChange={(e) => handleDueDateChange(e.target.value)}
              aria-invalid={fieldErrors.due_date !== undefined}
            />
            {fieldErrors.due_date && (
              <p className="err" role="alert">
                {fieldErrors.due_date}
              </p>
            )}
          </label>

          <div className="field">
            <span>Priority</span>
            <div className="segmented" role="group" aria-label="Priority">
              {PRIORITIES.map((p) => (
                <button
                  key={p}
                  type="button"
                  className="seg"
                  data-prio={p}
                  aria-pressed={priority === p}
                  onClick={() => setPriority(p)}
                >
                  {p}
                </button>
              ))}
            </div>
            {fieldErrors.priority && (
              <p className="err" role="alert">
                {fieldErrors.priority}
              </p>
            )}
          </div>
        </div>

        <div className="field">
          <span>Repeat</span>
          <div className="toggles" role="group" aria-label="Repeat">
            {REPEAT_PRESETS.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className="toggle toggle-repeat"
                aria-pressed={activePreset === preset.id}
                disabled={recurrenceDisabled && preset.id !== "never"}
                onClick={() => applyPreset(preset.id)}
              >
                {preset.label}
              </button>
            ))}
          </div>

          {recurrenceDisabled ? (
            <p className="hint">Pick a due date first — the schedule counts from it.</p>
          ) : (
            activePreset === "custom" && (
              <div className="custom-repeat">
                <span>Every</span>
                <input
                  id="todo-recurrence-interval"
                  type="number"
                  min={1}
                  max={365}
                  value={recurrenceInterval}
                  onChange={(e) => setRecurrenceInterval(e.target.value)}
                  aria-label="Interval"
                  aria-invalid={
                    fieldErrors.recurrence_interval !== undefined || recurrenceError !== null
                  }
                />
                <select
                  id="todo-recurrence-unit"
                  value={recurrenceUnit}
                  onChange={(e) => setRecurrenceUnit(e.target.value as RecurrenceUnit)}
                  aria-label="Unit"
                  aria-invalid={fieldErrors.recurrence_unit !== undefined}
                >
                  {RECURRENCE_UNITS.map((u) => (
                    <option key={u} value={u}>
                      {u}s
                    </option>
                  ))}
                </select>
              </div>
            )
          )}

          {fieldErrors.recurrence_unit && (
            <p className="err" role="alert">
              {fieldErrors.recurrence_unit}
            </p>
          )}
          {fieldErrors.recurrence_interval && (
            <p className="err" role="alert">
              {fieldErrors.recurrence_interval}
            </p>
          )}
          {recurrenceError && (
            <p className="err" role="alert">
              {recurrenceError}
            </p>
          )}
        </div>

        {generalError && (
          <p className="alert" role="alert" style={{ marginTop: 4 }}>
            {generalError}
          </p>
        )}
      </div>

      <div className="panel-foot">
        <button
          type="submit"
          className={todo === null ? "btn btn-create" : "btn btn-primary"}
          disabled={isSubmitting}
        >
          {isSubmitting ? "Saving…" : todo === null ? "Create" : "Save"}
        </button>{" "}
        <button type="button" className="btn" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/**
 * Only the fields that actually changed go into the PATCH. TodoUpdate rejects
 * unknown fields (`extra="forbid"`) and status is owned by /status, so the
 * diff is also the safety boundary against accidentally sending either.
 * `due_date` is compared in local input form so an untouched datetime field
 * never round-trips into a spurious diff.
 */
function diffChanges(
  todo: Todo,
  name: string,
  description: string,
  dueDate: string,
  priority: Priority,
  unit: RecurrenceUnit | null,
  interval: number | null,
): TodoUpdatePayload {
  const changes: TodoUpdatePayload = {};
  if (name !== todo.name) changes.name = name;
  if (description !== (todo.description ?? "")) {
    changes.description = description === "" ? null : description;
  }
  if (toLocalInput(todo.due_date) !== dueDate) changes.due_date = toIso(dueDate);
  if (priority !== todo.priority) changes.priority = priority;
  if (unit !== (todo.recurrence_unit ?? null)) changes.recurrence_unit = unit;
  if (interval !== (todo.recurrence_interval ?? null)) changes.recurrence_interval = interval;
  return changes;
}
