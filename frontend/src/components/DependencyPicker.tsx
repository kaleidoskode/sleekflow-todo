import { Fragment, useMemo, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import type { UseMutationResult } from "@tanstack/react-query";
import { ApiError, apiFetch } from "../api/client";
import type { Todo } from "../api/types";
import { STATUS_LABEL } from "../labels";

const MAX_MATCHES = 20;

interface DependencyPickerProps {
  /** Detail todo: its `depends_on` is the real edge list (list items always send []). */
  todo: Todo;
  /** Existing todos to search over (App's candidates query). */
  candidates: Todo[];
  add: UseMutationResult<void, Error, { todoId: string; dependsOnId: string }>;
  remove: UseMutationResult<void, Error, { todoId: string; dependsOnId: string }>;
  /** Called after a successful add/remove so the caller can refetch the detail. */
  onChanged: (todoId: string) => void;
  /** Opens a dependency in the detail panel so it can be finished. */
  onOpenTodo?: (id: string) => void;
}

function shortId(id: string): string {
  return id.slice(0, 8);
}

/**
 * Searchable dependency picker. Searching is client-side over the loaded
 * candidates (the backend has no name-search parameter); when a query has no
 * match among them the UI says so rather than pretending.
 */
export function DependencyPicker({
  todo,
  candidates,
  add,
  remove,
  onChanged,
  onOpenTodo,
}: DependencyPickerProps) {
  const [query, setQuery] = useState("");

  /**
   * Fetch each dependency by id. The candidates pool is only the first 200
   * todos, so on a large board most dependencies are not in it and would
   * otherwise render as a truncated UUID. A todo has a handful of
   * dependencies and this is a detail view, not a list row, so the fan-out
   * is small and bounded — and it gets us each blocker's real status too.
   */
  const depQueries = useQueries({
    queries: todo.depends_on.map((d) => ({
      queryKey: ["todo", d.id],
      queryFn: () => apiFetch<Todo>(`/api/todos/${d.id}?include_deleted=true`),
      staleTime: 30_000,
    })),
  });

  const resolvedDeps = useMemo(
    () =>
      todo.depends_on.map((d, i) => ({
        id: d.id,
        addedBy: d.added_by,
        todo: depQueries[i]?.data ?? null,
        loading: depQueries[i]?.isLoading ?? false,
      })),
    // depQueries is a new array each render; key off the resolved payloads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [todo.depends_on, depQueries.map((q) => q.data?.id ?? "").join(",")],
  );

  // Resolve UUIDs (cycle paths, dependency rows) to names, preferring a
  // fetched todo, then the candidates pool, then a short id as last resort.
  const nameOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of candidates) map.set(c.id, c.name);
    for (const q of depQueries) if (q.data) map.set(q.data.id, q.data.name);
    map.set(todo.id, todo.name);
    return (id: string) => map.get(id) ?? `todo ${shortId(id)}`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates, todo.id, todo.name, depQueries.map((q) => q.data?.id ?? "").join(",")]);

  const trimmed = query.trim().toLowerCase();
  const existing = new Set(todo.depends_on.map((d) => d.id));
  const matches = useMemo(() => {
    const haystack = trimmed === "" ? candidates : candidates.filter((c) => c.name.toLowerCase().includes(trimmed));
    return haystack
      .filter((c) => c.deleted_at === null && c.id !== todo.id && !existing.has(c.id))
      .slice(0, MAX_MATCHES);
  }, [candidates, trimmed, todo.id, existing]);

  const addPending = add.isPending && add.variables?.todoId === todo.id;
  const removePending = remove.isPending && remove.variables?.todoId === todo.id;

  const addError =
    add.isError && add.variables?.todoId === todo.id && add.variables !== undefined
      ? (add.error as ApiError)
      : null;
  const removeError =
    remove.isError && remove.variables?.todoId === todo.id ? (remove.error as ApiError) : null;

  function handleAdd(id: string) {
    add.mutate(
      { todoId: todo.id, dependsOnId: id },
      {
        onSuccess: () => {
          setQuery("");
          onChanged(todo.id);
        },
      },
    );
  }

  function handleRemove(id: string) {
    remove.mutate(
      { todoId: todo.id, dependsOnId: id },
      {
        onSuccess: () => onChanged(todo.id),
      },
    );
  }

  return (
    <div className="section">
      <h3>Waiting on</h3>

      {todo.depends_on.length === 0 ? (
        <p className="hint" style={{ marginBottom: 10 }}>
          Nothing — this todo can start whenever you are ready.
        </p>
      ) : (
        <ul className="deps">
          {resolvedDeps.map(({ id, addedBy, todo: dep, loading }) => {
            const done = dep?.status === "completed";
            return (
              <li key={id} data-done={done}>
                <button
                  type="button"
                  className="dep-open"
                  onClick={() => onOpenTodo?.(id)}
                  disabled={!onOpenTodo || loading}
                  title={done ? "Open this todo" : "Open this todo to finish it"}
                >
                  <i className="dot" data-status={dep?.status} />
                  <span className="dep-name">{loading ? "Loading…" : nameOf(id)}</span>
                  {addedBy !== null && (
                    <span className="dep-by" title={`Link added by ${addedBy}`}>
                      by {addedBy}
                    </span>
                  )}
                  {dep && (
                    <span className="badge" data-status={dep.status}>
                      {STATUS_LABEL[dep.status]}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => handleRemove(id)}
                  disabled={removePending}
                >
                  {removePending && remove.variables?.dependsOnId === id ? "Removing…" : "Remove"}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <input
        id={`dep-search-${todo.id}`}
        type="search"
        aria-label="Add a dependency"
        placeholder="Search todos to add…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        disabled={addPending}
      />

      {query !== "" && matches.length === 0 && (
        <p className="hint">No match among the loaded todos.</p>
      )}
      {matches.length > 0 && (
        <ul className="deps" style={{ marginTop: 8, marginBottom: 0 }}>
          {matches.map((c) => (
            <li key={c.id}>
              <i className="dot" data-status={c.status} />
              <span>{c.name}</span>
              <span className="badge" data-status={c.status}>
                {STATUS_LABEL[c.status]}
              </span>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => handleAdd(c.id)}
                disabled={addPending}
              >
                {addPending && add.variables?.dependsOnId === c.id ? "Adding…" : "Add"}
              </button>
            </li>
          ))}
        </ul>
      )}

      {addError instanceof ApiError && addError.code === "DEPENDENCY_CYCLE" && (
        <div role="alert">
          <p className="err">{addError.problem.detail}</p>
          <CyclePath loop={[todo.id, ...(addError.problem.cycle_path ?? [])]} nameOf={nameOf} />
        </div>
      )}
      {addError instanceof ApiError && addError.code !== "DEPENDENCY_CYCLE" && (
        <p className="err" role="alert">
          {addError.problem.detail}
        </p>
      )}
      {removeError instanceof ApiError && (
        <p className="err" role="alert">
          {removeError.problem.detail}
        </p>
      )}
    </div>
  );
}

/**
 * The server's cycle_path is the chain from the candidate back to the edited
 * todo; the edge the user is about to add is todo -> candidate, so the full
 * closed loop is `[todo, ...path]` (self-dependency returns [id, id], so
 * collapse consecutive duplicates first).
 */
function CyclePath({ loop, nameOf }: { loop: string[]; nameOf: (id: string) => string }) {
  const chain: string[] = [];
  for (const id of loop) {
    if (chain[chain.length - 1] !== id) chain.push(id);
  }
  const closed = chain[0] === chain[chain.length - 1] ? chain : [...chain, chain[0]];
  return (
    <div className="cycle">
      {closed.map((id, i) => (
        <Fragment key={`${id}-${i}`}>
          {i > 0 && <i aria-hidden="true">→</i>}
          {i === 1 ? <strong>{nameOf(id)}</strong> : <span>{nameOf(id)}</span>}
        </Fragment>
      ))}
    </div>
  );
}
