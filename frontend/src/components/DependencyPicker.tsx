import { useMemo, useState } from "react";
import type { UseMutationResult } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import type { Todo } from "../api/types";

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
}

function shortId(id: string): string {
  return id.slice(0, 8);
}

/**
 * Searchable dependency picker. Searching is client-side over the loaded
 * candidates (the backend has no name-search parameter); when a query has no
 * match among them the UI says so rather than pretending.
 */
export function DependencyPicker({ todo, candidates, add, remove, onChanged }: DependencyPickerProps) {
  const [query, setQuery] = useState("");

  // Resolve UUIDs (detail depends_on, cycle paths) to names when we can;
  // dependencies may not be among the loaded candidates.
  const nameOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of candidates) map.set(c.id, c.name);
    map.set(todo.id, todo.name);
    return (id: string) => map.get(id) ?? `todo ${shortId(id)}`;
  }, [candidates, todo.id, todo.name]);

  const trimmed = query.trim().toLowerCase();
  const existing = new Set(todo.depends_on);
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
    <div>
      <h3>Dependencies</h3>

      <div>
        <label htmlFor={`dep-search-${todo.id}`}>Add dependency</label>
        <input
          id={`dep-search-${todo.id}`}
          type="search"
          placeholder="Search existing todos…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={addPending}
        />
        {query !== "" && matches.length === 0 && (
          <p style={{ fontSize: "0.9em" }}>No matching todos among the loaded candidates.</p>
        )}
        {matches.length > 0 && (
          <ul style={{ margin: "0.25rem 0", paddingLeft: "1.25rem" }}>
            {matches.map((c) => (
              <li key={c.id}>
                {c.name}{" "}
                <button type="button" onClick={() => handleAdd(c.id)} disabled={addPending}>
                  {addPending && add.variables?.dependsOnId === c.id ? "Adding…" : "Add"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {addError instanceof ApiError && addError.code === "DEPENDENCY_CYCLE" && (
        <div role="alert" style={{ color: "#b3261e" }}>
          <p>{addError.problem.detail}</p>
          <p>This link would close the loop:</p>
          <CyclePath loop={[todo.id, ...(addError.problem.cycle_path ?? [])]} nameOf={nameOf} />
        </div>
      )}
      {addError instanceof ApiError && addError.code !== "DEPENDENCY_CYCLE" && (
        <p role="alert" style={{ color: "#b3261e" }}>
          {addError.code}: {addError.problem.detail}
        </p>
      )}
      {removeError instanceof ApiError && (
        <p role="alert" style={{ color: "#b3261e" }}>
          {removeError.code}: {removeError.problem.detail}
        </p>
      )}

      <h4>Currently depends on</h4>
      {todo.depends_on.length === 0 ? (
        <p style={{ fontSize: "0.9em" }}>Nothing.</p>
      ) : (
        <ul style={{ margin: "0.25rem 0", paddingLeft: "1.25rem" }}>
          {todo.depends_on.map((id) => (
            <li key={id}>
              {nameOf(id)}{" "}
              <button type="button" onClick={() => handleRemove(id)} disabled={removePending}>
                {removePending && remove.variables?.dependsOnId === id ? "Removing…" : "Remove"}
              </button>
            </li>
          ))}
        </ul>
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
    <p>
      <code>
        {closed.map((id, i) => (
          <span key={`${id}-${i}`}>
            {i > 0 && <span> → </span>}
            {i === 1 ? <strong>{nameOf(id)}</strong> : nameOf(id)}
          </span>
        ))}
      </code>
      <span style={{ fontSize: "0.9em" }}> (the new link is the first one)</span>
    </p>
  );
}
