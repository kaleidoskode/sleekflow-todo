import type { Todo } from "./types";

/** Exported because the event stream bypasses `apiFetch` — it reads a
 *  ReadableStream rather than a JSON body — and must not drift from it. */
export const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

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
  | "UNAUTHENTICATED"
  | "INVALID_CREDENTIALS"
  | "USERNAME_TAKEN"
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

const TOKEN_KEY = "sleekflow.token";

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY);
export const setToken = (token: string): void => localStorage.setItem(TOKEN_KEY, token);
export const clearToken = (): void => localStorage.removeItem(TOKEN_KEY);

/**
 * Notified when the server rejects our token, so the app can drop to the
 * sign-in screen from anywhere without every caller handling 401.
 */
type ExpiryHandler = () => void;
let onSessionExpired: ExpiryHandler = () => {};
export function setSessionExpiredHandler(handler: ExpiryHandler): void {
  onSessionExpired = handler;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });

  if (!response.ok) {
    const problem = await parseProblem(response);
    // An expired or invalid token is not something a caller can fix — clear
    // it and let the app show the sign-in screen. Login failures are exempt:
    // they are a 401 the sign-in form must render itself.
    if (problem.status === 401 && problem.code === "UNAUTHENTICATED") {
      clearToken();
      onSessionExpired();
    }
    throw new ApiError(problem);
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
