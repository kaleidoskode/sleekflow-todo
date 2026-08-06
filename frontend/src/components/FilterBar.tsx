import type { Priority, Status, TodoFilters } from "../api/types";

const STATUSES: Status[] = ["not_started", "in_progress", "completed", "archived"];
const PRIORITIES: Priority[] = ["low", "medium", "high"];

const STATUS_LABEL: Record<Status, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  completed: "Completed",
  archived: "Archived",
};

const SORT_LABEL: Record<string, string> = {
  due_date: "Due date ↑",
  "-due_date": "Due date ↓",
  priority: "Priority ↑",
  "-priority": "Priority ↓",
  status: "Status",
  name: "Name",
};

interface FilterBarProps {
  filters: TodoFilters;
  onChange: (filters: TodoFilters) => void;
}

/**
 * Purely controlled: holds no state of its own. Every interaction derives a
 * new `TodoFilters` from the current `filters` prop and hands it to
 * `onChange` — the parent owns state and re-fetching.
 */
export function FilterBar({ filters, onChange }: FilterBarProps) {
  function toggleStatus(status: Status) {
    const current = filters.status ?? [];
    const next = current.includes(status)
      ? current.filter((s) => s !== status)
      : [...current, status];
    onChange({ ...filters, status: next.length ? next : undefined });
  }

  function togglePriority(priority: Priority) {
    const current = filters.priority ?? [];
    const next = current.includes(priority)
      ? current.filter((p) => p !== priority)
      : [...current, priority];
    onChange({ ...filters, priority: next.length ? next : undefined });
  }

  function setBlocked(value: string) {
    // Three distinct server behaviours: omit the param ("any"), or send an
    // explicit true/false. Never let "any" collapse into `false`.
    if (value === "any") {
      onChange({ ...filters, blocked: undefined });
    } else {
      onChange({ ...filters, blocked: value === "true" });
    }
  }

  const blockedValue = filters.blocked === undefined ? "any" : String(filters.blocked);
  const activeCount =
    (filters.status?.length ?? 0) +
    (filters.priority?.length ?? 0) +
    (filters.blocked === undefined ? 0 : 1) +
    (filters.include_deleted ? 1 : 0);

  return (
    <div className="filters">
      <div className="field">
        <span>Status</span>
        <div className="toggles">
          {STATUSES.map((status) => (
            <button
              key={status}
              type="button"
              className="toggle"
              data-status={status}
              aria-pressed={filters.status?.includes(status) ?? false}
              onClick={() => toggleStatus(status)}
            >
              <i className="dot" data-status={status} />
              {STATUS_LABEL[status]}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <span>Priority</span>
        <div className="toggles">
          {PRIORITIES.map((priority) => (
            <button
              key={priority}
              type="button"
              className="toggle"
              aria-pressed={filters.priority?.includes(priority) ?? false}
              onClick={() => togglePriority(priority)}
            >
              {priority}
            </button>
          ))}
        </div>
      </div>

      <label className="field">
        <span>Blocked</span>
        <select value={blockedValue} onChange={(e) => setBlocked(e.target.value)}>
          <option value="any">Any</option>
          <option value="true">Blocked only</option>
          <option value="false">Unblocked only</option>
        </select>
      </label>

      <label className="field">
        <span>Sort by</span>
        <select
          value={filters.sort ?? "due_date"}
          onChange={(e) => onChange({ ...filters, sort: e.target.value })}
        >
          {Object.entries(SORT_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>

      <label className="check">
        <input
          type="checkbox"
          checked={filters.include_deleted ?? false}
          onChange={(e) => onChange({ ...filters, include_deleted: e.target.checked || undefined })}
        />
        Show deleted
      </label>

      {activeCount > 0 && (
        <button
          type="button"
          className="btn btn-sm"
          style={{ marginBottom: 1 }}
          onClick={() => onChange({ sort: filters.sort })}
        >
          Clear {activeCount}
        </button>
      )}
    </div>
  );
}
