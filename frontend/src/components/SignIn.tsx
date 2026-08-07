import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError } from "../api/client";
import { login, register } from "../api/auth";
import type { User } from "../api/auth";

interface SignInProps {
  onSignedIn: (user: User) => void;
}

type Mode = "login" | "register";

export function SignIn({ onSignedIn }: SignInProps) {
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    setFieldErrors({});
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setFieldErrors({});
    setBusy(true);
    try {
      const user = await (mode === "login" ? login : register)(username, password);
      onSignedIn(user);
    } catch (err) {
      if (!(err instanceof ApiError)) {
        setError(err instanceof Error ? err.message : String(err));
      } else if (err.code === "VALIDATION_ERROR") {
        const next: Record<string, string> = {};
        for (const e of err.problem.errors ?? []) next[e.field] = e.message;
        setFieldErrors(next);
        if (Object.keys(next).length === 0) setError(err.problem.detail);
      } else {
        setError(err.problem.detail);
      }
    } finally {
      setBusy(false);
    }
  }

  const isRegister = mode === "register";

  return (
    <div className="signin-page">
      <form className="signin" onSubmit={handleSubmit} noValidate>
        <div className="signin-brand">
          <span className="signin-mark" aria-hidden="true" />
          <div>
            <h1>Todos</h1>
            <p>A shared board for the whole team</p>
          </div>
        </div>

        <div className="signin-tabs" role="group" aria-label="Sign in or create an account">
          <button
            type="button"
            className="signin-tab"
            aria-pressed={!isRegister}
            onClick={() => switchMode("login")}
          >
            Sign in
          </button>
          <button
            type="button"
            className="signin-tab"
            aria-pressed={isRegister}
            onClick={() => switchMode("register")}
          >
            Create account
          </button>
        </div>

        <label className="field">
          <span>Username</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            placeholder="ada"
            aria-invalid={fieldErrors.username !== undefined}
            autoFocus
          />
          {fieldErrors.username && (
            <p className="err" role="alert">
              {fieldErrors.username}
            </p>
          )}
        </label>

        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={isRegister ? "new-password" : "current-password"}
            placeholder={isRegister ? "At least 8 characters" : "••••••••"}
            aria-invalid={fieldErrors.password !== undefined}
          />
          {fieldErrors.password && (
            <p className="err" role="alert">
              {fieldErrors.password}
            </p>
          )}
        </label>

        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}

        <button type="submit" className="btn btn-create signin-submit" disabled={busy}>
          {busy ? "Just a moment…" : isRegister ? "Create account" : "Sign in"}
        </button>

        <p className="signin-note">
          Everyone signs in to the same board — accounts control access, not ownership.
        </p>
      </form>
    </div>
  );
}
