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
}

function formatDueDate(dueDate: string | null): string {
  if (!dueDate) return "—";
  return new Date(dueDate).toLocaleDateString();
}

export function TodoList({ filters, onSelect, onConflict }: TodoListProps) {
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
      const stale = data?.pages.flatMap((page) => page.items).find((t) => t.id === restore.variables?.id);
      if (stale && onConflict) onConflict(stale, restore.error.problem.current);
    }
  }, [restore.isError, restore.error, data, onConflict]);

  if (isLoading) return <p>Loading…</p>;

  if (isError) {
    const detail = error instanceof ApiError ? `${error.code}: ${error.problem.detail}` : String(error);
    return <p role="alert">Failed to load todos: {detail}</p>;
  }

  const todos = data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Priority</th>
            <th>Due date</th>
            <th>Blocked</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {todos.map((todo) => (
            <TodoRow
              key={todo.id}
              todo={todo}
              onSelect={onSelect}
              onRestore={() => restore.mutate(todo)}
              isRestoring={restore.isPending && restore.variables?.id === todo.id}
              restoreError={
                restore.isError && restore.variables?.id === todo.id ? restore.error : null
              }
            />
          ))}
        </tbody>
      </table>

      {todos.length === 0 && <p>No todos match these filters.</p>}

      {hasNextPage && (
        <button onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
          {isFetchingNextPage ? "Loading more…" : "Load more"}
        </button>
      )}
    </div>
  );
}

interface TodoRowProps {
  todo: Todo;
  onSelect?: (todo: Todo) => void;
  onRestore: () => void;
  isRestoring: boolean;
  restoreError: unknown;
}

function TodoRow({ todo, onSelect, onRestore, isRestoring, restoreError }: TodoRowProps) {
  const isDeleted = todo.deleted_at !== null;

  return (
    <>
      <tr style={isDeleted ? { opacity: 0.5 } : undefined}>
        <td>
          <button type="button" onClick={() => onSelect?.(todo)}>
            {todo.name}
          </button>
        </td>
        <td>{todo.status}</td>
        <td>{todo.priority}</td>
        <td>{formatDueDate(todo.due_date)}</td>
        <td>
          {/* List responses always send depends_on: [] (populating the real
              edge list per-row would be an N+1 over a 10k page). Blocked
              state must come from is_blocked / unmet_dependency_count,
              which the list endpoint does populate. */}
          {todo.is_blocked && <span>Blocked ({todo.unmet_dependency_count})</span>}
        </td>
        <td>
          {isDeleted && (
            <button onClick={onRestore} disabled={isRestoring}>
              {isRestoring ? "Restoring…" : "Restore"}
            </button>
          )}
        </td>
      </tr>
      {restoreError instanceof ApiError && (
        <tr>
          <td colSpan={6} role="alert">
            Restore failed: {restoreError.code} — {restoreError.problem.detail}
          </td>
        </tr>
      )}
    </>
  );
}
