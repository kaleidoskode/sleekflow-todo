import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getToken } from "./client";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export interface BoardEvent {
  action:
    | "created"
    | "updated"
    | "status_changed"
    | "deleted"
    | "restored"
    | "dependency_added"
    | "dependency_removed";
  todo_id: string;
  actor: string;
  at: string;
  name?: string;
  version?: number;
  status?: string;
}

export type LiveStatus = "connecting" | "live" | "offline";

/** Longest gap between reconnect attempts. */
const MAX_BACKOFF_MS = 15_000;

/**
 * Subscribes to the server's change stream and refreshes the board when
 * someone else writes.
 *
 * Read with `fetch` rather than `EventSource` for one reason: `EventSource`
 * cannot set request headers, so it cannot carry the bearer token. The
 * alternatives were putting the token in the query string — where it lands in
 * every access log — or reading the stream manually. This reads it manually.
 */
export function useLiveUpdates(enabled: boolean) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [lastEvent, setLastEvent] = useState<BoardEvent | null>(null);

  // Held in a ref so reconnecting never re-runs the effect: the effect owns
  // the connection, and restarting it would drop a healthy stream.
  const queryClientRef = useRef(queryClient);
  queryClientRef.current = queryClient;

  useEffect(() => {
    if (!enabled) {
      setStatus("offline");
      return;
    }

    const controller = new AbortController();
    let retryTimer: number | undefined;
    let attempt = 0;
    let stopped = false;

    /** Events are signals, not state — re-read rather than patch the cache. */
    function refresh() {
      const qc = queryClientRef.current;
      // Matches ["todos", filters] and ["todo", id] by prefix. Refetching only
      // happens for queries that are actually mounted, so an event costs one
      // request for the visible page — not one per cached page.
      qc.invalidateQueries({ queryKey: ["todos"] });
      qc.invalidateQueries({ queryKey: ["todo"] });
      qc.invalidateQueries({ queryKey: ["todo-candidates"] });
    }

    function handleFrame(raw: string) {
      // ": keep-alive" — a comment frame, and the only thing separating a live
      // connection from a dead one during a quiet period.
      if (raw.startsWith(":")) return;

      let name = "message";
      const data: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trim());
      }
      if (data.length === 0) return;

      if (name === "ready") {
        attempt = 0; // a clean connection resets the backoff
        setStatus("live");
        return;
      }

      try {
        const event = JSON.parse(data.join("\n")) as BoardEvent;
        setLastEvent(event);
        refresh();
      } catch {
        // A malformed frame is not worth tearing the stream down for.
      }
    }

    async function connect() {
      try {
        const token = getToken();
        const response = await fetch(`${BASE}/api/events`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: controller.signal,
        });
        if (!response.ok || response.body === null) {
          throw new Error(`Event stream returned ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          // stream: true so a multi-byte character split across two chunks is
          // held back rather than decoded into a replacement character.
          buffer += decoder.decode(value, { stream: true });

          // Frames end at a blank line, and a chunk can cut one in half — keep
          // the trailing partial in the buffer until its terminator arrives.
          let boundary = buffer.indexOf("\n\n");
          while (boundary !== -1) {
            handleFrame(buffer.slice(0, boundary));
            buffer = buffer.slice(boundary + 2);
            boundary = buffer.indexOf("\n\n");
          }
        }
        throw new Error("Event stream closed");
      } catch {
        if (stopped || controller.signal.aborted) return;
        setStatus("offline");
        // Exponential backoff, capped: a backend restart during the demo
        // should reconnect on its own without hammering a server that is
        // still coming up.
        const delay = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS);
        attempt += 1;
        retryTimer = window.setTimeout(connect, delay);
      }
    }

    void connect();

    return () => {
      stopped = true;
      window.clearTimeout(retryTimer);
      controller.abort();
    };
  }, [enabled]);

  return { status, lastEvent };
}
