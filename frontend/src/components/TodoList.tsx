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
}

const STATUS_LABEL: Record<string, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  completed: "Completed",
  archived: "Archived",
};

function formatDueDate(dueDate: string | null): string {
  if (!dueDate) return "no due date";
  return new Date(dueDate).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function TodoList({ filters, onSelect, onConflict, selectedId, onCount }: TodoListProps) {
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
        <p className="empty">
          <b>Nothing matches these filters</b>
          Clear a filter, or create a todo to get started.
        </p>
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
            <span>{formatDueDate(todo.due_date)}</span>
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
