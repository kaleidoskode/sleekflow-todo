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
      // The snapshot that was actually submitted — not the component's current
      // prop, which a background refetch may already have moved on.
      onConflict(mutation.variables?.todo ?? todo, mutation.error.problem.current);
    }
  }, [mutation.isError, mutation.error, mutation.variables?.todo.id, todo.id, onConflict]);

  const error =
    mutation.isError && mutation.variables?.todo.id === todo.id ? mutation.error : null;
  const pending = mutation.isPending && mutation.variables?.todo.id === todo.id;

  return (
    <div className="section">
      <h3>Status</h3>
      <div className="status-picker" role="group" aria-label="Status">
        {STATUSES.map((s) => {
          const guarded = todo.is_blocked && GUARDED_WHEN_BLOCKED.includes(s);
          const locked = guarded || todo.deleted_at !== null;
          return (
            <button
              key={s}
              type="button"
              className="status-opt"
              data-status={s}
              aria-pressed={todo.status === s}
              disabled={locked || pending}
              title={guarded ? "Finish the dependencies below first" : undefined}
              onClick={() => todo.status !== s && mutation.mutate({ todo, status: s })}
            >
              <i className="dot" data-status={s} />
              {LABEL[s]}
              {guarded && (
                <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true">
                  <rect x="2.5" y="5.5" width="7" height="5" rx="1.2" fill="currentColor" />
                  <path
                    d="M4 5.5V4a2 2 0 014 0v1.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.3"
                  />
                </svg>
              )}
            </button>
          );
        })}
      </div>

      {pending && <p className="hint">Saving…</p>}

      {error instanceof ApiError && (
        <p className="err" role="alert">
          {error.code === "BLOCKED_BY_DEPENDENCIES"
            ? blockedDetail(error, todo)
            : error.problem.detail}
        </p>
      )}

      {toast !== null && (
        <div className="toast" role="status">
          <div style={{ flex: 1 }}>
            <b>Next occurrence created</b>
            <div style={{ marginTop: 2 }}>
              “{toast.name}” — due {formatDue(toast.due_date)}
            </div>
          </div>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setToast(null)}
            aria-label="Dismiss"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

const LABEL: Record<Status, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  completed: "Completed",
  archived: "Archived",
};

/**
 * BLOCKED_BY_DEPENDENCIES bodies spread `unmet_dependency_count` at the top
 * level (backend errors.py `**exc.extra`); the shared Problem type only
 * declares the fields every error path uses, so widen locally.
 */
function blockedDetail(error: ApiError, todo: Todo): string {
  const extra = error.problem as Problem & { unmet_dependency_count?: number };
  const count = extra.unmet_dependency_count ?? todo.unmet_dependency_count;
  return `${error.problem.detail} ${count} still unfinished.`;
}
