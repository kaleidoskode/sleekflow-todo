import { useEffect, useState } from "react";
import type { UseMutationResult } from "@tanstack/react-query";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch, getToken, setSessionExpiredHandler } from "./api/client";
import { logout, storedUser } from "./api/auth";
import type { User } from "./api/auth";
import { SignIn } from "./components/SignIn";
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
import { Modal } from "./components/Modal";
import { StatusControl } from "./components/StatusControl";
import { TodoForm } from "./components/TodoForm";
import { TodoList } from "./components/TodoList";

const queryClient = new QueryClient();

interface AppProps {
  user: User;
  onSignOut: () => void;
}

function App({ user, onSignOut }: AppProps) {
  const [filters, setFilters] = useState<TodoFilters>({});
  // A trail rather than a single id: opening a dependency pushes onto it, so
  // you can go finish a blocker and step back to the todo it was blocking.
  const [trail, setTrail] = useState<string[]>([]);
  const selectedId = trail.length > 0 ? trail[trail.length - 1] : null;
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
    setTrail([todo.id]); // opening from the list starts a fresh trail
    setFormMode(null);
  }

  const selectedTodo = selectedId !== null ? (detail.data ?? null) : null;
  const editingTodo = formMode === "edit" ? selectedTodo : null;

  // The form is a modal now, so the aside is purely the detail panel —
  // it stays visible behind an edit dialog, which keeps the context.
  const showAside = selectedId !== null;
  // Distinguishes "you filtered everything out" from "the board is empty",
  // so the empty state can offer the right way forward.
  const hasActiveFilters =
    (filters.status?.length ?? 0) > 0 ||
    (filters.priority?.length ?? 0) > 0 ||
    filters.blocked !== undefined ||
    filters.include_deleted === true ||
    filters.due_before !== undefined ||
    filters.due_after !== undefined;

  return (
    <main className="app">
      <header className="app-head">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <h1 className="app-title">Todos</h1>
            <p className="app-sub">
              Shared board · <b>{loadedCount.toLocaleString()}</b> loaded
            </p>
          </div>
        </div>

        <div className="head-actions">
          <button type="button" className="btn btn-create" onClick={() => setFormMode("create")}>
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
              <path
                d="M7 1.75v10.5M1.75 7h10.5"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
              />
            </svg>
            New todo
          </button>

          <span className="head-divider" aria-hidden="true" />

          <span className="whoami" title={`Signed in as ${user.username}`}>
            <span className="avatar" aria-hidden="true">
              {user.username.slice(0, 1).toUpperCase()}
            </span>
            <span className="whoami-name">{user.username}</span>
          </span>

          <button
            type="button"
            className="icon-btn"
            onClick={onSignOut}
            title="Sign out"
            aria-label="Sign out"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              <path
                d="M6.25 2.75H3.5a1 1 0 00-1 1v8.5a1 1 0 001 1h2.75M10.5 11l3-3-3-3M13.25 8H6.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
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

      {/* Without a selection there is no side panel, so the list takes the
          full width instead of leaving a 400px gap. */}
      <div className="app-body" data-aside={showAside}>
        <TodoList
          filters={filters}
          onSelect={openDetail}
          onConflict={reportConflict}
          selectedId={selectedId}
          onCount={setLoadedCount}
          onCreate={() => setFormMode("create")}
          hasFilters={hasActiveFilters}
          onClearFilters={() => setFilters({ sort: filters.sort })}
        />

        {showAside && (
          <aside className="side">
            {selectedId !== null && (
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
                    onClose={() => setTrail([])}
                    onOpenTodo={(id) => setTrail((t) => [...t, id])}
                    onBack={trail.length > 1 ? () => setTrail((t) => t.slice(0, -1)) : undefined}
                    onConflict={reportConflict}
                  />
                )}
              </div>
            )}
          </aside>
        )}
      </div>

      {formMode === "create" && (
        <Modal label="New todo" onClose={() => setFormMode(null)}>
          <TodoForm
            key="create"
            todo={null}
            create={createTodo}
            update={updateTodo}
            onDone={() => setFormMode(null)}
            onCancel={() => setFormMode(null)}
            onConflict={reportConflict}
          />
        </Modal>
      )}

      {editingTodo !== null && (
        <Modal label={`Edit ${editingTodo.name}`} onClose={() => setFormMode(null)}>
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
        </Modal>
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
  /** Opens a dependency in this panel, pushing onto the trail. */
  onOpenTodo: (id: string) => void;
  /** Present only when there is somewhere to go back to. */
  onBack?: () => void;
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
  onOpenTodo,
  onBack,
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
      onConflict(deleteTodo.variables ?? todo, deleteTodo.error.problem.current);
    }
  }, [deleteTodo.isError, deleteTodo.error, deleteTodo.variables?.id, todo, onConflict]);
  useEffect(() => {
    if (
      restoreTodo.isError &&
      restoreTodo.error instanceof ApiError &&
      restoreTodo.error.code === "VERSION_CONFLICT" &&
      restoreTodo.error.problem.current
    ) {
      onConflict(restoreTodo.variables ?? todo, restoreTodo.error.problem.current);
    }
  }, [restoreTodo.isError, restoreTodo.error, restoreTodo.variables?.id, todo, onConflict]);

  return (
    <>
      <div className="detail-head">
        {onBack && (
          <button type="button" className="icon-btn back-btn" onClick={onBack} aria-label="Back">
            <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden="true">
              <path
                d="M9 3.5L5 7.5l4 4"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        )}
        <div className="detail-title">
          <h2>{todo.name}</h2>
          <p className="detail-sub">
            <span className="mono">v{todo.version}</span>
            <span aria-hidden="true">·</span>
            <span>
              {todo.updated_by ? `${todo.updated_by} · ` : ""}
              {relativeTime(todo.updated_at)}
            </span>
            {todo.deleted_at !== null && (
              <>
                <span aria-hidden="true">·</span>
                <span style={{ color: "var(--danger)" }}>deleted</span>
              </>
            )}
          </p>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close detail">
          <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden="true">
            <path
              d="M3.5 3.5l8 8M11.5 3.5l-8 8"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>

      {todo.is_blocked && (
        <div className="detail-blocked">
          <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden="true">
            <rect x="3" y="6.5" width="9" height="6.5" rx="1.5" fill="currentColor" />
            <path
              d="M5 6.5V4.75a2.5 2.5 0 015 0V6.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            />
          </svg>
          <span>
            Waiting on <b>{todo.unmet_dependency_count}</b> unfinished{" "}
            {todo.unmet_dependency_count === 1 ? "todo" : "todos"}
          </span>
        </div>
      )}

      <StatusControl
        todo={todo}
        mutation={changeStatus}
        onChanged={onRefreshDetail}
        onConflict={onConflict}
      />

      <div className="detail-meta">
        <div className="meta-tile">
          <span className="meta-label">Due</span>
          <span className={isOverdue(todo) ? "meta-value overdue" : "meta-value"}>
            {formatFullDue(todo.due_date)}
          </span>
        </div>
        <div className="meta-tile">
          <span className="meta-label">Priority</span>
          <span className="badge" data-prio={todo.priority}>
            {todo.priority}
          </span>
        </div>
        <div className="meta-tile">
          <span className="meta-label">Repeats</span>
          <span className="meta-value">
            {todo.recurrence_unit
              ? todo.recurrence_interval === 1
                ? `Every ${todo.recurrence_unit}`
                : `Every ${todo.recurrence_interval} ${todo.recurrence_unit}s`
              : "Never"}
          </span>
        </div>
      </div>

      {todo.description && (
        <div className="section">
          <h3>Notes</h3>
          <p className="detail-notes">{todo.description}</p>
        </div>
      )}

      <DependencyPicker
        todo={todo}
        candidates={candidates}
        add={addDependency}
        remove={removeDependency}
        onChanged={onRefreshDetail}
        onOpenTodo={onOpenTodo}
      />

      <div className="panel-foot">
        <button
          type="button"
          className="btn btn-primary"
          onClick={onEdit}
          disabled={todo.deleted_at !== null}
        >
          Edit todo
        </button>
        {todo.deleted_at !== null ? (
          <button
            type="button"
            className="btn btn-create"
            style={{ marginLeft: "auto" }}
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
            style={{ marginLeft: "auto" }}
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
  if (!dueDate) return "No due date";
  return new Date(dueDate).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function isOverdue(todo: Todo): boolean {
  if (!todo.due_date || todo.status === "completed" || todo.status === "archived") return false;
  return new Date(todo.due_date).getTime() < Date.now();
}

/** "3 minutes ago", "yesterday", "2 weeks ago" — an exact timestamp here is noise. */
function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * Session gate. The board is shared, so this decides whether you are in at
 * all — it does not scope what you see once you are.
 */
function Session() {
  const [user, setUser] = useState<User | null>(() => (getToken() ? storedUser() : null));
  const queryClientInstance = useQueryClient();

  function signOut() {
    logout();
    setUser(null);
    // Drop every cached todo so the next account never sees the last one's data.
    queryClientInstance.clear();
  }

  // A token can expire mid-session; apiFetch clears it and calls this so any
  // request, from anywhere, drops us back to sign-in.
  useEffect(() => {
    setSessionExpiredHandler(() => {
      setUser(null);
      queryClientInstance.clear();
    });
  }, [queryClientInstance]);

  if (user === null) return <SignIn onSignedIn={setUser} />;
  return <App user={user} onSignOut={signOut} />;
}

export default function Root() {
  return (
    <QueryClientProvider client={queryClient}>
      <Session />
    </QueryClientProvider>
  );
}
