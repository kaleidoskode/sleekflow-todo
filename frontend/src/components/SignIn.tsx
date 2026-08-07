import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError } from "../api/client";
import { login, register } from "../api/auth";
import type { User } from "../api/auth";

interface SignInProps {
  onSignedIn: (user: User) => void;
}

type Mode = "login" | "register";

/**
 * A still of the real board, built from the same badge classes the app uses.
 * It says what this product is — tasks that gate each other — before you are
 * even inside, and it cannot drift from the real styling.
 */
function BoardPreview() {
  return (
    <div className="preview" aria-hidden="true">
      <div className="preview-row" data-status="completed">
        <span className="preview-stripe" />
        <span className="preview-name">Finish testing</span>
        <span className="badge" data-status="completed">
          Completed
        </span>
      </div>

      <div className="preview-link">
        <svg width="13" height="26" viewBox="0 0 13 26">
          <path
            d="M6.5 1v18M2 15l4.5 4.5L11 15"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span>unblocks</span>
      </div>

      <div className="preview-row" data-status="in_progress">
        <span className="preview-stripe" />
        <span className="preview-name">Deploy to prod</span>
        <span className="badge" data-status="in_progress">
          In progress
        </span>
      </div>

      <div className="preview-row" data-status="not_started">
        <span className="preview-stripe" />
        <span className="preview-name">Announce release</span>
        <span className="badge badge-blocked">Blocked 1</span>
      </div>
    </div>
  );
}

export function SignIn({ onSignedIn }: SignInProps) {
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
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
      <div className="signin-shell">
        <aside className="signin-aside">
          <div className="signin-brand">
            <span className="signin-mark" aria-hidden="true" />
            <h1>Todos</h1>
          </div>
          <p className="signin-pitch">
            One shared board. Tasks can wait on other tasks, and nothing starts before what it
            depends on is done.
          </p>
          <BoardPreview />
        </aside>

        <form className="signin-form" onSubmit={handleSubmit} noValidate>
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

          <h2 className="signin-title">
            {isRegister ? "Create your account" : "Welcome back"}
          </h2>
          <p className="signin-lede">
            {isRegister
              ? "Pick a username and a password of at least 8 characters."
              : "Sign in to pick up where the team left off."}
          </p>

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

          <div className="field">
            <span>Password</span>
            <div className="pw-control" data-invalid={fieldErrors.password !== undefined}>
              <input
                type={showPassword ? "text" : "password"}
                className="pw-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={isRegister ? "new-password" : "current-password"}
                placeholder={isRegister ? "At least 8 characters" : "••••••••"}
                aria-label="Password"
                aria-invalid={fieldErrors.password !== undefined}
              />
              <button
                type="button"
                className="pw-toggle"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                aria-pressed={showPassword}
              >
                {showPassword ? (
                  <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                    <path
                      d="M2 8s2.4-4 6-4 6 4 6 4-2.4 4-6 4-6-4-6-4zM2.5 2.5l11 11"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.4"
                      strokeLinecap="round"
                    />
                  </svg>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                    <path
                      d="M2 8s2.4-4 6-4 6 4 6 4-2.4 4-6 4-6-4-6-4z"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.4"
                    />
                    <circle cx="8" cy="8" r="1.7" fill="currentColor" />
                  </svg>
                )}
              </button>
            </div>
            {fieldErrors.password && (
              <p className="err" role="alert">
                {fieldErrors.password}
              </p>
            )}
          </div>

          {error && (
            <p className="alert signin-error" role="alert">
              {error}
            </p>
          )}

          <button type="submit" className="btn btn-create signin-submit" disabled={busy}>
            {busy && <span className="spinner" aria-hidden="true" />}
            {busy ? "Just a moment…" : isRegister ? "Create account" : "Sign in"}
          </button>

          <p className="signin-note">
            {isRegister ? (
              <>
                Already have an account?{" "}
                <button type="button" className="linkish" onClick={() => switchMode("login")}>
                  Sign in
                </button>
              </>
            ) : (
              <>
                New here?{" "}
                <button type="button" className="linkish" onClick={() => switchMode("register")}>
                  Create an account
                </button>
              </>
            )}
          </p>
        </form>
      </div>
    </div>
  );
}
