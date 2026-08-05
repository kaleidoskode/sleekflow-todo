import { useEffect, useState } from "react";
import type { UseMutationResult } from "@tanstack/react-query";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "./api/client";
import {
  useAddDependency,
  useChangeStatus,
  useCreateTodo,
  useDeleteTodo,
  useRemoveDependency,
  useRestoreTodo,
  useUpdateTodo,
} from "./api/todos";
import type { Status, Todo, TodoFilters, TodoPage } from "./api/types";
import { ConflictBanner } from "./components/ConflictBanner";
import { DependencyPicker } from "./components/DependencyPicker";
import { FilterBar } from "./components/FilterBar";
import { StatusControl } from "./components/StatusControl";
import { TodoForm } from "./components/TodoForm";
import { TodoList } from "./components/TodoList";

const queryClient = new QueryClient();

function App() {
  const [filters, setFilters] = useState<TodoFilters>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [formMode, setFormMode] = useState<"create" | "edit" | null>(null);
  const [conflict, setConflict] = useState<{ stale: Todo; current: Todo } | null>(null);
  const queryClientInstance = useQueryClient();

  // Full todo including the real depends_on list — only GET /todos/{id}
  // populates it (list responses always send []). include_deleted so a
  // soft-deleted row can be opened and restored from the panel.
  const detail = useQuery({
    queryKey: ["todo", selectedId],
    queryFn: () => apiFetch<Todo>(`/api/todos/${selectedId}?include_deleted=true`),
    enabled: selectedId !== null,
  });

  // Supporting lookup for the dependency picker: an unfiltered, server-sorted
  // page of names. The main list stays exactly as Task 12 built it.
  const candidates = useQuery({
    queryKey: ["todo-candidates"],
    queryFn: () => apiFetch<TodoPage>("/api/todos?limit=200&sort=name"),
    staleTime: 60_000,
  });

  const createTodo = useCreateTodo();
  const updateTodo = useUpdateTodo();
  const changeStatus = useChangeStatus();
  const addDependency = useAddDependency();
  const removeDependency = useRemoveDependency();
  const deleteTodo = useDeleteTodo();
  const restoreTodo = useRestoreTodo();

  const refreshDetail = (id: string) =>
    queryClientInstance.invalidateQueries({ queryKey: ["todo", id] });

  function reportConflict(stale: Todo, current: Todo) {
    setConflict({ stale, current });
  }

  function reloadAfterConflict() {
    // Invalidating the list and the detail refetches everything; the edit form
    // is keyed by id:version, so a bumped version remounts it with fresh data.
    queryClientInstance.invalidateQueries({ queryKey: ["todos"] });
    if (selectedId !== null) refreshDetail(selectedId);
    setConflict(null);
  }

  function openDetail(todo: Todo) {
    setSelectedId(todo.id);
    setFormMode(null);
  }

  const selectedTodo = selectedId !== null ? (detail.data ?? null) : null;
  const editingTodo = formMode === "edit" ? selectedTodo : null;

  return (
    <main>
      <h1>Todos</h1>

      {conflict !== null && (
        <ConflictBanner
          stale={conflict.stale}
          current={conflict.current}
          onReload={reloadAfterConflict}
          onDismiss={() => setConflict(null)}
        />
      )}

      <FilterBar filters={filters} onChange={setFilters} />
      <div style={{ marginTop: "0.75rem" }}>
        <button type="button" onClick={() => setFormMode("create")}>
          + New Todo
        </button>
      </div>
      <TodoList filters={filters} onSelect={openDetail} onConflict={reportConflict} />

      {formMode === "create" && (
        <section style={{ marginTop: "1.5rem" }}>
          <TodoForm
            key="create"
            todo={null}
            create={createTodo}
            update={updateTodo}
            onDone={() => setFormMode(null)}
            onCancel={() => setFormMode(null)}
            onConflict={reportConflict}
          />
        </section>
      )}

      {editingTodo !== null && (
        <section style={{ marginTop: "1.5rem" }}>
          <TodoForm
            key={`${editingTodo.id}:${editingTodo.version}`}
            todo={editingTodo}
            create={createTodo}
            update={updateTodo}
            onDone={() => {
              refreshDetail(editingTodo.id);
              setFormMode(null);
            }}
            onCancel={() => setFormMode(null)}
            onConflict={reportConflict}
          />
        </section>
      )}

      {selectedId !== null && formMode !== "edit" && (
        <section style={{ marginTop: "1.5rem" }}>
          {detail.isLoading && <p>Loading…</p>}
          {detail.isError && (
            <p role="alert">Failed to load todo: {renderError(detail.error)}</p>
          )}
          {detail.data && (
            <DetailPanel
              todo={detail.data}
              candidates={candidates.data?.items ?? []}
              changeStatus={changeStatus}
              addDependency={addDependency}
              removeDependency={removeDependency}
              deleteTodo={deleteTodo}
              restoreTodo={restoreTodo}
              onRefreshDetail={refreshDetail}
              onEdit={() => setFormMode("edit")}
              onClose={() => setSelectedId(null)}
              onConflict={reportConflict}
            />
          )}
        </section>
      )}
    </main>
  );
}

type StatusChangeResult = { todo: Todo; next_occurrence: Todo | null };

interface DetailPanelProps {
  todo: Todo;
  candidates: Todo[];
  changeStatus: UseMutationResult<StatusChangeResult, Error, { todo: Todo; status: Status }>;
  addDependency: UseMutationResult<void, Error, { todoId: string; dependsOnId: string }>;
  removeDependency: UseMutationResult<void, Error, { todoId: string; dependsOnId: string }>;
  deleteTodo: UseMutationResult<void, Error, Todo>;
  restoreTodo: UseMutationResult<Todo, Error, Todo>;
  onRefreshDetail: (id: string) => void;
  onEdit: () => void;
  onClose: () => void;
  onConflict: (stale: Todo, current: Todo) => void;
}

function DetailPanel({
  todo,
  candidates,
  changeStatus,
  addDependency,
  removeDependency,
  deleteTodo,
  restoreTodo,
  onRefreshDetail,
  onEdit,
  onClose,
  onConflict,
}: DetailPanelProps) {
  const deletePending = deleteTodo.isPending && deleteTodo.variables?.id === todo.id;
  const restorePending = restoreTodo.isPending && restoreTodo.variables?.id === todo.id;
  const deleteError =
    deleteTodo.isError && deleteTodo.variables?.id === todo.id ? deleteTodo.error : null;
  const restoreError =
    restoreTodo.isError && restoreTodo.variables?.id === todo.id ? restoreTodo.error : null;

  // Delete/restore are the two mutations that do not live inside a component;
  // route their 409s to the app-wide banner just like the rest.
  useEffect(() => {
    if (
      deleteTodo.isError &&
      deleteTodo.error instanceof ApiError &&
      deleteTodo.error.code === "VERSION_CONFLICT" &&
      deleteTodo.error.problem.current
    ) {
      onConflict(todo, deleteTodo.error.problem.current);
    }
  }, [deleteTodo.isError, deleteTodo.error, deleteTodo.variables?.id, todo, onConflict]);
  useEffect(() => {
    if (
      restoreTodo.isError &&
      restoreTodo.error instanceof ApiError &&
      restoreTodo.error.code === "VERSION_CONFLICT" &&
      restoreTodo.error.problem.current
    ) {
      onConflict(todo, restoreTodo.error.problem.current);
    }
  }, [restoreTodo.isError, restoreTodo.error, restoreTodo.variables?.id, todo, onConflict]);

  return (
    <>
      <h2>{todo.name}</h2>
      <dl>
        <dt>Description</dt>
        <dd>{todo.description ?? "—"}</dd>
        <dt>Status</dt>
        <dd>{todo.status}</dd>
        <dt>Priority</dt>
        <dd>{todo.priority}</dd>
        <dt>Due date</dt>
        <dd>{formatFullDue(todo.due_date)}</dd>
        <dt>Recurrence</dt>
        <dd>
          {todo.recurrence_unit
            ? `every ${todo.recurrence_interval} ${todo.recurrence_unit}${todo.recurrence_interval === 1 ? "" : "s"}`
            : "—"}
        </dd>
        <dt>Series</dt>
        <dd>{todo.recurrence_series_id ?? "—"}</dd>
        <dt>Version</dt>
        <dd>{todo.version}</dd>
        <dt>Updated</dt>
        <dd>{new Date(todo.updated_at).toLocaleString()}</dd>
      </dl>

      <StatusControl
        todo={todo}
        mutation={changeStatus}
        onChanged={onRefreshDetail}
        onConflict={onConflict}
      />

      <DependencyPicker
        todo={todo}
        candidates={candidates}
        add={addDependency}
        remove={removeDependency}
        onChanged={onRefreshDetail}
      />

      <div style={{ marginTop: "0.75rem" }}>
        <button type="button" onClick={onEdit} disabled={todo.deleted_at !== null}>
          Edit
        </button>{" "}
        {todo.deleted_at !== null ? (
          <button
            type="button"
            onClick={() =>
              restoreTodo.mutate(todo, {
                onSuccess: () => onRefreshDetail(todo.id),
              })
            }
            disabled={restorePending}
          >
            {restorePending ? "Restoring…" : "Restore"}
          </button>
        ) : (
          <button
            type="button"
            onClick={() =>
              deleteTodo.mutate(todo, {
                onSuccess: () => onClose(),
              })
            }
            disabled={deletePending}
          >
            {deletePending ? "Deleting…" : "Delete"}
          </button>
        )}{" "}
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      {deleteError && (
        <p role="alert" style={{ color: "#b3261e" }}>
          Delete failed: {renderError(deleteError)}
        </p>
      )}
      {restoreError && (
        <p role="alert" style={{ color: "#b3261e" }}>
          Restore failed: {renderError(restoreError)}
        </p>
      )}
    </>
  );
}

function renderError(err: unknown): string {
  if (err instanceof ApiError) return `${err.code}: ${err.problem.detail}`;
  return err instanceof Error ? err.message : String(err);
}

function formatFullDue(dueDate: string | null): string {
  if (!dueDate) return "—";
  return new Date(dueDate).toLocaleString();
}

export default function Root() {
  return (
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  );
}
