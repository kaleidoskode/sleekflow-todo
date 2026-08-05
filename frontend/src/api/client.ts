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
  | "UNKNOWN_ERROR"
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

async function parseProblem(response: Response): Promise<Problem> {
  try {
    return await response.json();
  } catch {
    // Non-JSON or empty error body: synthesize one carrying the real status,
    // so a proxy timeout or unhandled 500 doesn't surface as a parse error.
    return {
      title: response.statusText || "Request failed",
      status: response.status,
      detail: `The server returned ${response.status} with no problem body.`,
      code: "UNKNOWN_ERROR",
    };
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init.headers },
  });

  if (!response.ok) {
    throw new ApiError(await parseProblem(response));
  }

  // 204, and 201s that carry no body (dependency add), have nothing to parse.
  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return undefined as T;
  }

  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Every mutation must carry the version it read. */
export function ifMatch(version: number): Record<string, string> {
  return { "If-Match": `"${version}"` };
}
