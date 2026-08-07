import { apiFetch, clearToken, setToken } from "./client";

export interface User {
  id: string;
  username: string;
  created_at: string;
}

interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

const USER_KEY = "sleekflow.user";

/** Cached alongside the token so a reload can render immediately. */
export function storedUser(): User | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

async function submit(path: string, username: string, password: string): Promise<User> {
  const res = await apiFetch<TokenResponse>(path, {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setToken(res.access_token);
  localStorage.setItem(USER_KEY, JSON.stringify(res.user));
  return res.user;
}

export const login = (username: string, password: string) =>
  submit("/api/auth/login", username, password);

export const register = (username: string, password: string) =>
  submit("/api/auth/register", username, password);

export function logout(): void {
  clearToken();
  localStorage.removeItem(USER_KEY);
}
