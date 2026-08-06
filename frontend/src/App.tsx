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
  const [loadedCount, setLoadedCount] = useState(0);
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

  const showAside = formMode !== null || selectedId !== null;

  return (
    <main className="app">
      <header className="app-head">
        <h1 className="app-title">
          Todos <span>/ shared board</span>
        </h1>
        <div className="app-stats">
          <span>
            <b>{loadedCount.toLocaleString()}</b> loaded
          </span>
          <button type="button" className="btn btn-primary" onClick={() => setFormMode("create")}>
            New todo
          </button>
        </div>
      </header>

      {conflict !== null && (
        <ConflictBanner
          stale={conflict.stale}
          current={conflict.current}
          onReload={reloadAfterConflict}
          onDismiss={() => setConflict(null)}
        />
      )}

      <FilterBar filters={filters} onChange={setFilters} />

      <div className="app-body">
        <TodoList
          filters={filters}
          onSelect={openDetail}
          onConflict={reportConflict}
          selectedId={selectedId}
          onCount={setLoadedCount}
        />

        {showAside && (
          <aside className="side">
            {formMode === "create" && (
              <TodoForm
                key="create"
                todo={null}
                create={createTodo}
                update={updateTodo}
                onDone={() => setFormMode(null)}
                onCancel={() => setFormMode(null)}
                onConflict={reportConflict}
              />
            )}

            {editingTodo !== null && (
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
            )}

            {selectedId !== null && formMode === null && (
              <div className="panel">
                {detail.isLoading && <p className="empty">Loading…</p>}
                {detail.isError && (
                  <div className="panel-body">
                    <p className="alert" role="alert">
                      Could not load this todo. {renderError(detail.error)}
                    </p>
                  </div>
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
              </div>
            )}
          </aside>
        )}
      </div>
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
      <div className="panel-head" data-status={todo.status}>
        <i className="dot" data-status={todo.status} />
        <h2>{todo.name}</h2>
        <button
          type="button"
          className="btn btn-sm"
          onClick={onClose}
          style={{ marginLeft: "auto" }}
          aria-label="Close detail"
        >
          Close
        </button>
      </div>

      <div className="panel-body">
        {todo.is_blocked && (
          <p className="chip chip-blocked" style={{ marginTop: 0, marginBottom: 12 }}>
            Waiting on {todo.unmet_dependency_count} unfinished{" "}
            {todo.unmet_dependency_count === 1 ? "todo" : "todos"}
          </p>
        )}
        <dl className="facts">
          <dt>Description</dt>
          <dd>{todo.description ?? "—"}</dd>
          <dt>Priority</dt>
          <dd>{todo.priority}</dd>
          <dt>Due</dt>
          <dd>{formatFullDue(todo.due_date)}</dd>
          <dt>Repeats</dt>
          <dd>
            {todo.recurrence_unit
              ? `every ${todo.recurrence_interval} ${todo.recurrence_unit}${
                  todo.recurrence_interval === 1 ? "" : "s"
                }`
              : "—"}
          </dd>
          <dt>Version</dt>
          <dd className="mono">v{todo.version}</dd>
          <dt>Updated</dt>
          <dd>{new Date(todo.updated_at).toLocaleString()}</dd>
        </dl>
      </div>

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

      <div className="panel-foot">
        <button
          type="button"
          className="btn"
          onClick={onEdit}
          disabled={todo.deleted_at !== null}
        >
          Edit
        </button>
        {todo.deleted_at !== null ? (
          <button
            type="button"
            className="btn"
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
            className="btn btn-danger"
            onClick={() =>
              deleteTodo.mutate(todo, {
                onSuccess: () => onClose(),
              })
            }
            disabled={deletePending}
          >
            {deletePending ? "Deleting…" : "Delete"}
          </button>
        )}
      </div>

      {(deleteError || restoreError) && (
        <div className="panel-body" style={{ paddingTop: 0 }}>
          <p className="alert" role="alert">
            {deleteError ? "Delete failed. " : "Restore failed. "}
            {renderError(deleteError ?? restoreError)}
          </p>
        </div>
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
