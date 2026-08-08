import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch, ifMatch } from "./client";
import type {
  BulkResult,
  Status,
  Todo,
  TodoCreatePayload,
  TodoFilters,
  TodoPage,
  TodoUpdatePayload,
} from "./types";

const KEY = "todos";

function toQuery(filters: TodoFilters, cursor?: string): string {
  const params = new URLSearchParams();
  filters.status?.forEach((s) => params.append("status", s));
  filters.priority?.forEach((p) => params.append("priority", p));
  if (filters.due_before) params.set("due_before", filters.due_before);
  if (filters.due_after) params.set("due_after", filters.due_after);
  // Tri-state: `true` / `false` are distinct server-side filters from
  // omitting the param entirely ("any"). Never coerce undefined to a string.
  if (filters.blocked !== undefined) params.set("blocked", String(filters.blocked));
  if (filters.include_deleted) params.set("include_deleted", "true");
  params.set("sort", filters.sort ?? "due_date");
  params.set("limit", "50");
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

export function useTodos(filters: TodoFilters) {
  return useInfiniteQuery({
    queryKey: [KEY, filters],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => apiFetch<TodoPage>(`/api/todos?${toQuery(filters, pageParam)}`),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

function useInvalidate() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: [KEY] });
}

export function useCreateTodo() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: TodoCreatePayload) =>
      apiFetch<Todo>("/api/todos", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: invalidate,
  });
}

export function useUpdateTodo() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ todo, changes }: { todo: Todo; changes: TodoUpdatePayload }) =>
      apiFetch<Todo>(`/api/todos/${todo.id}`, {
        method: "PATCH",
        headers: ifMatch(todo.version),
        body: JSON.stringify(changes),
      }),
    onSuccess: invalidate,
  });
}

export function useChangeStatus() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ todo, status }: { todo: Todo; status: Status }) =>
      apiFetch<{ todo: Todo; next_occurrence: Todo | null }>(`/api/todos/${todo.id}/status`, {
        method: "POST",
        headers: ifMatch(todo.version),
        body: JSON.stringify({ status }),
      }),
    onSuccess: invalidate,
  });
}

export function useDeleteTodo() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (todo: Todo) =>
      apiFetch<void>(`/api/todos/${todo.id}`, { method: "DELETE", headers: ifMatch(todo.version) }),
    onSuccess: invalidate,
  });
}

export function useRestoreTodo() {
  const invalidate = useInvalidate();
  return useMutation({
    // POST /todos/{id}/restore requires If-Match like every other mutation
    // (a 428 PRECONDITION_REQUIRED otherwise) — the original brief omitted
    // this header before that requirement landed.
    mutationFn: (todo: Todo) =>
      apiFetch<Todo>(`/api/todos/${todo.id}/restore`, {
        method: "POST",
        headers: ifMatch(todo.version),
      }),
    onSuccess: invalidate,
  });
}

/** Mirrors MAX_BULK_ITEMS in backend/app/schemas/bulk.py. */
const BULK_CHUNK = 200;

/**
 * Sends a selection in server-sized chunks and merges the outcomes.
 *
 * "Select all" after paging through the list can exceed what one request
 * accepts, and a rejected batch is a worse answer than two requests. Chunks go
 * one after another rather than in parallel: each item holds a database
 * connection for its own transaction, so overlapping batches would compete for
 * the same small pool.
 */
async function inChunks(
  items: { id: string; version: number }[],
  send: (chunk: { id: string; version: number }[]) => Promise<BulkResult>,
): Promise<BulkResult> {
  const merged: BulkResult = { succeeded: 0, failed: 0, results: [] };
  for (let i = 0; i < items.length; i += BULK_CHUNK) {
    const result = await send(items.slice(i, i + BULK_CHUNK));
    merged.succeeded += result.succeeded;
    merged.failed += result.failed;
    merged.results.push(...result.results);
  }
  return merged;
}

/** Each item carries its own version — a batch has one If-Match to give. */
const refs = (todos: Todo[]) => todos.map((t) => ({ id: t.id, version: t.version }));

export function useBulkStatus() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ todos, status }: { todos: Todo[]; status: Status }) =>
      inChunks(refs(todos), (items) =>
        apiFetch<BulkResult>("/api/todos/bulk/status", {
          method: "POST",
          body: JSON.stringify({ items, status }),
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useBulkDelete() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (todos: Todo[]) =>
      inChunks(refs(todos), (items) =>
        apiFetch<BulkResult>("/api/todos/bulk/delete", {
          method: "POST",
          body: JSON.stringify({ items }),
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useAddDependency() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ todoId, dependsOnId }: { todoId: string; dependsOnId: string }) =>
      apiFetch<void>(`/api/todos/${todoId}/dependencies`, {
        method: "POST",
        body: JSON.stringify({ depends_on_id: dependsOnId }),
      }),
    onSuccess: invalidate,
  });
}

export function useRemoveDependency() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ todoId, dependsOnId }: { todoId: string; dependsOnId: string }) =>
      apiFetch<void>(`/api/todos/${todoId}/dependencies/${dependsOnId}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}
