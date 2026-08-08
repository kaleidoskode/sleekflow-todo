import type { Status } from "./api/types";

/**
 * The one place a status becomes words.
 *
 * This map previously existed five times over — as `STATUS_LABEL`, `LABEL` and
 * `STATUS_WORD`, half of them typed `Record<string, string>` — so adding a
 * status meant finding all five and renaming nothing consistently.
 */
export const STATUS_LABEL: Record<Status, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  completed: "Completed",
  archived: "Archived",
};

/**
 * For values that are not statically known to be a `Status` — event payloads,
 * anything off the wire. Falls back to the raw value rather than rendering
 * "undefined" at someone.
 */
export function statusLabel(value: string | null | undefined): string {
  if (value == null) return "";
  return STATUS_LABEL[value as Status] ?? value;
}
