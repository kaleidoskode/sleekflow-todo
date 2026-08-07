import { useEffect } from "react";
import { ApiError } from "../api/client";
import { useRestoreTodo, useTodos } from "../api/todos";
import type { Todo, TodoFilters } from "../api/types";

interface TodoListProps {
  filters: TodoFilters;
  /** Called when a row is clicked so the app can open the detail panel. */
  onSelect?: (todo: Todo) => void;
  /** Called when a mutation rejects with VERSION_CONFLICT (app-wide banner). */
  onConflict?: (stale: Todo, current: Todo) => void;
  /** Highlights the row currently open in the detail panel. */
  selectedId?: string | null;
  /** Reports the loaded count up to the header. */
  onCount?: (loaded: number) => void;
  /** Opens the create form from the empty state. */
  onCreate?: () => void;
  /** True when any filter is narrowing the list — changes what the empty state offers. */
  hasFilters?: boolean;
  /** Clears every filter from the empty state. */
  onClearFilters?: () => void;
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <path
        d="M7 1.75v10.5M1.75 7h10.5"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

const STATUS_LABEL: Record<string, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  completed: "Completed",
  archived: "Archived",
};

/** Relative where it helps ("in 3 days", "2 days ago"), absolute beyond a week. */
function describeDue(dueDate: string | null): { text: string; overdue: boolean } {
  if (!dueDate) return { text: "No due date", overdue: false };

  const due = new Date(dueDate);
  const days = Math.round((due.getTime() - Date.now()) / 86_400_000);

  if (days < -7 || days > 7) {
    return {
      text: due.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }),
      overdue: days < 0,
    };
  }
  if (days === 0) return { text: "Due today", overdue: false };
  if (days === 1) return { text: "Due tomorrow", overdue: false };
  if (days === -1) return { text: "1 day overdue", overdue: true };
  if (days < 0) return { text: `${-days} days overdue`, overdue: true };
  return { text: `Due in ${days} days`, overdue: false };
}

export function TodoList({
  filters,
  onSelect,
  onConflict,
  selectedId,
  onCount,
  onCreate,
  hasFilters,
  onClearFilters,
}: TodoListProps) {
  const { data, isLoading, isError, error, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useTodos(filters);
  const restore = useRestoreTodo();

  // Restore is a mutation like any other: a stale version is a conflict the
  // banner should show, not just an inline row error.
  useEffect(() => {
    if (
      restore.isError &&
      restore.error instanceof ApiError &&
      restore.error.code === "VERSION_CONFLICT" &&
      restore.error.problem.current
    ) {
      const stale = data?.pages
        .flatMap((page) => page.items)
        .find((t) => t.id === restore.variables?.id);
      if (stale && onConflict) onConflict(stale, restore.error.problem.current);
    }
  }, [restore.isError, restore.error, data, onConflict]);

  const todos = data?.pages.flatMap((page) => page.items) ?? [];

  useEffect(() => {
    onCount?.(todos.length);
  }, [todos.length, onCount]);

  if (isLoading) {
    return (
      <div className="panel">
        <p className="empty">Loading todos…</p>
      </div>
    );
  }

  if (isError) {
    const detail =
      error instanceof ApiError ? `${error.code} — ${error.problem.detail}` : String(error);
    return (
      <div className="panel">
        <div className="panel-body">
          <p className="alert" role="alert">
            Could not load todos. {detail}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="panel">
      {todos.length === 0 ? (
        <div className="empty">
          {hasFilters ? (
            <>
              <b>No todos match these filters</b>
              <p>Widen the filters, or start something new.</p>
              <div className="empty-actions">
                <button type="button" className="btn" onClick={onClearFilters}>
                  Clear filters
                </button>
                <button type="button" className="btn btn-create" onClick={onCreate}>
                  <PlusIcon />
                  New todo
                </button>
              </div>
            </>
          ) : (
            <>
              <b>Nothing here yet</b>
              <p>Create your first todo to get started.</p>
              <div className="empty-actions">
                <button type="button" className="btn btn-create" onClick={onCreate}>
                  <PlusIcon />
                  New todo
                </button>
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="rows">
          {todos.map((todo) => (
            <TodoRow
              key={todo.id}
              todo={todo}
              isSelected={todo.id === selectedId}
              onSelect={onSelect}
              onRestore={() => restore.mutate(todo)}
              isRestoring={restore.isPending && restore.variables?.id === todo.id}
              restoreError={
                restore.isError && restore.variables?.id === todo.id ? restore.error : null
              }
            />
          ))}
        </div>
      )}

      {hasNextPage && (
        <div className="load-more">
          <button
            type="button"
            className="btn"
            onClick={() => fetchNextPage()}
            disabled={isFetchingNextPage}
          >
            {isFetchingNextPage ? "Loading…" : "Load more"}
          </button>
        </div>
      )}
    </div>
  );
}

interface TodoRowProps {
  todo: Todo;
  isSelected: boolean;
  onSelect?: (todo: Todo) => void;
  onRestore: () => void;
  isRestoring: boolean;
  restoreError: unknown;
}

function TodoRow({
  todo,
  isSelected,
  onSelect,
  onRestore,
  isRestoring,
  restoreError,
}: TodoRowProps) {
  const isDeleted = todo.deleted_at !== null;
  const due = describeDue(todo.due_date);

  return (
    <>
      <div
        className="row"
        data-status={todo.status}
        data-deleted={isDeleted}
        data-selected={isSelected}
      >
        <div className="row-stripe" />

        <button type="button" className="row-open" onClick={() => onSelect?.(todo)}>
          <span className="row-name">{todo.name}</span>
          <span className="row-sub">
            <span className={due.overdue ? "overdue" : undefined}>{due.text}</span>
            {todo.recurrence_unit && (
              <>
                <span aria-hidden="true">·</span>
                <span>repeats {todo.recurrence_unit}ly</span>
              </>
            )}
            {isDeleted && (
              <>
                <span aria-hidden="true">·</span>
                <span>deleted</span>
              </>
            )}
          </span>
        </button>

        <div className="row-meta">
          {/* List responses always send depends_on: [] (populating the real
              edge list per-row would be an N+1 over a 10k page). Blocked
              state comes from is_blocked / unmet_dependency_count, which
              the list endpoint does populate. */}
          {todo.is_blocked && (
            <span
              className="badge badge-blocked"
              title={`Waiting on ${todo.unmet_dependency_count} unfinished ${
                todo.unmet_dependency_count === 1 ? "todo" : "todos"
              }`}
            >
              Blocked {todo.unmet_dependency_count}
            </span>
          )}
          <span className="badge" data-prio={todo.priority}>
            {todo.priority}
          </span>
          <span className="badge" data-status={todo.status}>
            {STATUS_LABEL[todo.status] ?? todo.status}
          </span>
          <span className="ver" title={`Version ${todo.version}`}>
            v{todo.version}
          </span>
          {isDeleted && (
            <button type="button" className="btn btn-sm" onClick={onRestore} disabled={isRestoring}>
              {isRestoring ? "Restoring…" : "Restore"}
            </button>
          )}
        </div>
      </div>

      {restoreError instanceof ApiError && (
        <div className="row">
          <div className="row-stripe" />
          <p className="err" role="alert" style={{ padding: "0 14px 10px" }}>
            Restore failed — {restoreError.problem.detail}
          </p>
        </div>
      )}
    </>
  );
}
