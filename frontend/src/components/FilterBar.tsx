import type { Priority, Status, TodoFilters } from "../api/types";

const STATUSES: Status[] = ["not_started", "in_progress", "completed", "archived"];
const PRIORITIES: Priority[] = ["low", "medium", "high"];
const SORTS = ["due_date", "-due_date", "priority", "-priority", "status", "name"] as const;

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

  function setIncludeDeleted(checked: boolean) {
    onChange({ ...filters, include_deleted: checked || undefined });
  }

  function setSort(sort: string) {
    onChange({ ...filters, sort });
  }

  const blockedValue = filters.blocked === undefined ? "any" : String(filters.blocked);

  return (
    <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap", alignItems: "flex-start" }}>
      <fieldset>
        <legend>Status</legend>
        {STATUSES.map((status) => (
          <label key={status} style={{ display: "block" }}>
            <input
              type="checkbox"
              checked={filters.status?.includes(status) ?? false}
              onChange={() => toggleStatus(status)}
            />
            {status}
          </label>
        ))}
      </fieldset>

      <fieldset>
        <legend>Priority</legend>
        {PRIORITIES.map((priority) => (
          <label key={priority} style={{ display: "block" }}>
            <input
              type="checkbox"
              checked={filters.priority?.includes(priority) ?? false}
              onChange={() => togglePriority(priority)}
            />
            {priority}
          </label>
        ))}
      </fieldset>

      <div>
        <label>
          Blocked
          <br />
          <select name="blocked" value={blockedValue} onChange={(e) => setBlocked(e.target.value)}>
            <option value="any">Any</option>
            <option value="true">Blocked only</option>
            <option value="false">Unblocked only</option>
          </select>
        </label>
      </div>

      <div>
        <label>
          <input
            type="checkbox"
            checked={filters.include_deleted ?? false}
            onChange={(e) => setIncludeDeleted(e.target.checked)}
          />
          Show deleted
        </label>
      </div>

      <div>
        <label>
          Sort
          <br />
          <select name="sort" value={filters.sort ?? "due_date"} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map((sort) => (
              <option key={sort} value={sort}>
                {sort}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}
