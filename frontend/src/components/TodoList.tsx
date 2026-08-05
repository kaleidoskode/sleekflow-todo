import { ApiError } from "../api/client";
import { useRestoreTodo, useTodos } from "../api/todos";
import type { Todo, TodoFilters } from "../api/types";

interface TodoListProps {
  filters: TodoFilters;
}

function formatDueDate(dueDate: string | null): string {
  if (!dueDate) return "—";
  return new Date(dueDate).toLocaleDateString();
}

export function TodoList({ filters }: TodoListProps) {
  const { data, isLoading, isError, error, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useTodos(filters);
  const restore = useRestoreTodo();

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
  onRestore: () => void;
  isRestoring: boolean;
  restoreError: unknown;
}

function TodoRow({ todo, onRestore, isRestoring, restoreError }: TodoRowProps) {
  const isDeleted = todo.deleted_at !== null;

  return (
    <>
      <tr style={isDeleted ? { opacity: 0.5 } : undefined}>
        <td>{todo.name}</td>
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
