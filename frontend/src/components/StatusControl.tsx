import { useEffect, useState } from "react";
import type { UseMutationResult } from "@tanstack/react-query";
import { ApiError, type Problem } from "../api/client";
import type { Status, Todo } from "../api/types";

const STATUSES: Status[] = ["not_started", "in_progress", "completed", "archived"];

// Backend transitions.py: in_progress and completed require every dependency
// to be complete (`DEPENDENCY_GUARDED_TARGETS`). archived is always legal.
const GUARDED_WHEN_BLOCKED: Status[] = ["in_progress", "completed"];

interface StatusControlProps {
  todo: Todo;
  mutation: UseMutationResult<
    { todo: Todo; next_occurrence: Todo | null },
    Error,
    { todo: Todo; status: Status }
  >;
  /** Called after a successful change so the caller can refetch the detail. */
  onChanged: (todoId: string) => void;
  onConflict: (stale: Todo, current: Todo) => void;
}

function formatDue(dueDate: string | null): string {
  if (!dueDate) return "no due date";
  return new Date(dueDate).toLocaleString();
}

/**
 * Dropdown over the four statuses. The spawn toast lives here because the
 * mutation's response is the only place next_occurrence exists — this is the
 * moment completing a recurring todo becomes visible.
 */
export function StatusControl({ todo, mutation, onChanged, onConflict }: StatusControlProps) {
  const [toast, setToast] = useState<Todo | null>(null);

  // Surfacing a spawned occurrence: when the last change targeting this todo
  // completed a recurring item, show the occurrence that was just created.
  useEffect(() => {
    if (
      mutation.isSuccess &&
      mutation.variables?.todo.id === todo.id &&
      mutation.data.next_occurrence !== null
    ) {
      setToast(mutation.data.next_occurrence);
    }
  }, [mutation.isSuccess, mutation.data, mutation.variables?.todo.id, todo.id]);

  // Auto-dismiss the toast after 10s so a completed series doesn't pin it forever.
  useEffect(() => {
    if (toast === null) return;
    const timer = setTimeout(() => setToast(null), 10_000);
    return () => clearTimeout(timer);
  }, [toast]);

  // A successful change targeting this todo means its detail view (depends_on,
  // counts, version) is stale — tell the caller to refetch it.
  useEffect(() => {
    if (mutation.isSuccess && mutation.variables?.todo.id === todo.id) {
      onChanged(todo.id);
    }
  }, [mutation.isSuccess, mutation.variables?.todo.id, todo.id, onChanged]);

  // A stale version means someone else changed this todo: hand the banner the
  // stale snapshot (this component's todo) and the server's current one.
  useEffect(() => {
    if (
      mutation.isError &&
      mutation.variables?.todo.id === todo.id &&
      mutation.error instanceof ApiError &&
      mutation.error.code === "VERSION_CONFLICT" &&
      mutation.error.problem.current
    ) {
      onConflict(todo, mutation.error.problem.current);
    }
  }, [mutation.isError, mutation.error, mutation.variables?.todo.id, todo.id, onConflict]);

  const error =
    mutation.isError && mutation.variables?.todo.id === todo.id ? mutation.error : null;
  const pending = mutation.isPending && mutation.variables?.todo.id === todo.id;

  return (
    <div>
      <label htmlFor={`status-${todo.id}`}>Status</label>{" "}
      <select
        id={`status-${todo.id}`}
        value={todo.status}
        onChange={(e) => mutation.mutate({ todo, status: e.target.value as Status })}
        disabled={pending || todo.deleted_at !== null}
      >
        {STATUSES.map((s) => {
          const guarded = todo.is_blocked && GUARDED_WHEN_BLOCKED.includes(s);
          return (
            <option key={s} value={s} disabled={guarded}>
              {s}
              {guarded ? " (blocked)" : ""}
            </option>
          );
        })}
      </select>
      {todo.is_blocked && (
        <p style={{ fontSize: "0.9em", marginTop: "0.25rem" }}>
          Blocked: {todo.unmet_dependency_count} incomplete{" "}
          {todo.unmet_dependency_count === 1 ? "dependency" : "dependencies"} must be completed
          before this todo can start or be completed.
        </p>
      )}
      {pending && <span> Changing…</span>}
      {error instanceof ApiError && (
        <p role="alert" style={{ color: "#b3261e" }}>
          {error.code === "BLOCKED_BY_DEPENDENCIES"
            ? blockedDetail(error, todo)
            : `${error.code}: ${error.problem.detail}`}
        </p>
      )}
      {toast !== null && (
        <div
          role="status"
          style={{
            position: "fixed",
            bottom: "1rem",
            right: "1rem",
            maxWidth: "22rem",
            padding: "0.75rem 1rem",
            border: "1px solid currentColor",
            background: "#fff",
            boxShadow: "0 2px 8px rgba(0,0,0,0.25)",
            zIndex: 10,
          }}
        >
          <strong>Recurring occurrence created:</strong> “{toast.name}”
          <div>Next due: {formatDue(toast.due_date)}</div>
          <button type="button" onClick={() => setToast(null)}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * BLOCKED_BY_DEPENDENCIES bodies spread `unmet_dependency_count` at the top
 * level (backend errors.py `**exc.extra`); the shared Problem type only
 * declares the fields every error path uses, so widen locally.
 */
function blockedDetail(error: ApiError, todo: Todo): string {
  const extra = error.problem as Problem & { unmet_dependency_count?: number };
  const count = extra.unmet_dependency_count ?? todo.unmet_dependency_count;
  return `${error.problem.detail} (unmet dependency count: ${count})`;
}
