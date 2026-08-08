export type Status = "not_started" | "in_progress" | "completed" | "archived";
export type Priority = "low" | "medium" | "high";
export type RecurrenceUnit = "day" | "week" | "month";

export interface Todo {
  id: string;
  name: string;
  description: string | null;
  due_date: string | null;
  status: Status;
  priority: Priority;
  recurrence_unit: RecurrenceUnit | null;
  recurrence_interval: number | null;
  recurrence_series_id: string | null;
  unmet_dependency_count: number;
  is_blocked: boolean;
  /**
   * Populated only by GET /todos/{id}. List responses always send `[]` here
   * (populating it per-row would be an N+1 across a 10k-item page) — never
   * infer "no dependencies" from this being empty in a list context. Use
   * `is_blocked` / `unmet_dependency_count` instead, which the list endpoint
   * does populate correctly.
   */
  depends_on: string[];
  /** Username of whoever last changed this. Null for seeded rows. */
  updated_by: string | null;
  version: number;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TodoPage {
  items: Todo[];
  next_cursor: string | null;
}

export interface TodoFilters {
  status?: Status[];
  priority?: Priority[];
  due_before?: string;
  due_after?: string;
  blocked?: boolean;
  include_deleted?: boolean;
  sort?: string;
}

/**
 * Fields `POST /api/todos` accepts (backend `TodoCreate`). `name` is
 * required; everything else is optional and defaults server-side.
 */
export interface TodoCreatePayload {
  name: string;
  description?: string | null;
  due_date?: string | null;
  priority?: Priority;
  recurrence_unit?: RecurrenceUnit | null;
  recurrence_interval?: number | null;
}

/**
 * Fields `PATCH /api/todos/{id}` accepts (backend `TodoUpdate`, which sets
 * `extra="forbid"`). Deliberately excludes `status` (its own endpoint),
 * `id`, `version`, `depends_on`, and every server-computed field — sending
 * any of those is a 422 VALIDATION_ERROR, not a silent no-op.
 */
export interface TodoUpdatePayload {
  name?: string;
  description?: string | null;
  due_date?: string | null;
  priority?: Priority;
  recurrence_unit?: RecurrenceUnit | null;
  recurrence_interval?: number | null;
}

export interface BulkItemResult {
  id: string;
  ok: boolean;
  /** The new version on success; null when the item was refused. */
  version: number | null;
  /** The same problem code the single-item endpoint would have returned. */
  code: string | null;
  detail: string | null;
}

export interface BulkResult {
  succeeded: number;
  failed: number;
  results: BulkItemResult[];
}
