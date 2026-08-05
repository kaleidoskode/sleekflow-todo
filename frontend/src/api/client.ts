import type { Todo } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

/**
 * Error codes the backend is known to emit (see backend/app/errors.py and
 * the RequestValidationError handler in backend/app/main.py). Kept as a
 * union — widened with `string & {}` — so callers get autocomplete for the
 * known set without the type rejecting a code the backend adds later.
 */
export type ProblemCode =
  | "VALIDATION_ERROR"
  | "NOT_FOUND"
  | "VERSION_CONFLICT"
  | "PRECONDITION_REQUIRED"
  | "MALFORMED_PRECONDITION"
  | "INVALID_RECURRENCE"
  | "INVALID_TRANSITION"
  | "BLOCKED_BY_DEPENDENCIES"
  | "DEPENDENCY_CYCLE"
  | (string & {});

export interface Problem {
  title: string;
  status: number;
  detail: string;
  code: ProblemCode;
  errors?: { field: string; message: string }[];
  current?: Todo;
  cycle_path?: string[];
}

export class ApiError extends Error {
  problem: Problem;

  constructor(problem: Problem) {
    super(problem.detail);
    this.problem = problem;
  }
  get code() {
    return this.problem.code;
  }
  get status() {
    return this.problem.status;
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init.headers },
  });

  if (!response.ok) {
    throw new ApiError(await response.json());
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

/** Every mutation must carry the version it read. */
export function ifMatch(version: number): Record<string, string> {
  return { "If-Match": `"${version}"` };
}
