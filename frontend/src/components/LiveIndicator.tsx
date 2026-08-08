import { useEffect, useState } from "react";
import type { BoardEvent, LiveStatus } from "../api/events";

const STATUS_WORD: Record<string, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  completed: "Completed",
  archived: "Archived",
};

const LABEL: Record<LiveStatus, string> = {
  connecting: "Connecting",
  live: "Live",
  offline: "Offline",
};

const TITLE: Record<LiveStatus, string> = {
  connecting: "Opening the update stream…",
  live: "Connected — changes from other tabs appear automatically",
  offline: "Not receiving updates. Reconnecting…",
};

/** A dot and a word: whether this tab is hearing about other people's writes. */
export function LiveIndicator({ status }: { status: LiveStatus }) {
  return (
    <span className="live" data-state={status} title={TITLE[status]}>
      <span className="live-dot" aria-hidden="true" />
      <span className="live-word">{LABEL[status]}</span>
    </span>
  );
}

function describe(event: BoardEvent): string {
  const who = event.actor;
  const what = event.name ? `“${event.name}”` : "a todo";

  switch (event.action) {
    case "created":
      return `${who} added ${what}`;
    case "updated":
      return `${who} edited ${what}`;
    case "status_changed":
      return `${who} moved ${what} to ${STATUS_WORD[event.status ?? ""] ?? event.status}`;
    case "deleted":
      return `${who} deleted ${what}`;
    case "restored":
      return `${who} restored ${what}`;
    case "dependency_added":
      return `${who} added a dependency`;
    case "dependency_removed":
      return `${who} removed a dependency`;
    case "bulk_status_changed": {
      const n = event.count ?? 0;
      const to = STATUS_WORD[event.status ?? ""] ?? event.status;
      return `${who} moved ${n} ${n === 1 ? "todo" : "todos"} to ${to}`;
    }
    case "bulk_deleted": {
      const n = event.count ?? 0;
      return `${who} deleted ${n} ${n === 1 ? "todo" : "todos"}`;
    }
    default:
      return `${who} changed the board`;
  }
}

interface LiveToastProps {
  event: BoardEvent | null;
  /** Suppresses the toast for your own writes — you already saw them happen. */
  currentUser: string;
}

const VISIBLE_MS = 4200;

/**
 * Announces someone else's change. The list has already refreshed by the time
 * this appears — the toast exists so the refresh is explicable rather than the
 * board silently rearranging itself under the cursor.
 */
export function LiveToast({ event, currentUser }: LiveToastProps) {
  const [shown, setShown] = useState<BoardEvent | null>(null);

  useEffect(() => {
    if (event === null || event.actor === currentUser) return;
    setShown(event);
    const timer = window.setTimeout(() => setShown(null), VISIBLE_MS);
    // Re-armed on every event, so a burst of changes shows the latest for the
    // full duration instead of the first one expiring mid-sequence.
    return () => window.clearTimeout(timer);
  }, [event, currentUser]);

  if (shown === null) return null;

  return (
    <div className="live-toast" role="status" aria-live="polite">
      <span className="live-toast-dot" aria-hidden="true" />
      <span>{describe(shown)}</span>
    </div>
  );
}
