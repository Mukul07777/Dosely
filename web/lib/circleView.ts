// Display rules for the care circle page, kept apart from the JSX so they can be tested on their own.
import type { Dose, Med } from "@/lib/circle";

export type State = Dose["state"];
export type Slot = "morning" | "noon" | "night";

export const SLOTS: readonly Slot[] = ["morning", "noon", "night"];
export const SLOT_LABEL: Record<Slot, string> = { morning: "Morning 8:00", noon: "Afternoon 2:00", night: "Night 9:00" };

/** Below this share of doses taken, a medicine is flagged as often missed (same rule as the API and the weekly note). */
export const OFTEN_MISSED_BELOW = 80;

// Text colour on a light background of the same hue: every pair passes WCAG AA contrast for normal text.
const STATE_STYLE: Record<State, string> = {
  taken: "bg-green-100 text-green-900",
  missed: "bg-red-100 text-red-900",
  due: "bg-amber-100 text-amber-900",
  upcoming: "bg-slate-100 text-slate-800",
};

/** Badge classes for a dose or routine state. The state is also written as text, so colour is never the only cue. */
export const stateStyle = (s: State): string => STATE_STYLE[s];

/** The word shown in a routine's badge: "done by Asha" once ticked, otherwise the state itself. */
export function routineLabel(state: State, doneBy: string | null): string {
  if (state !== "taken") return state;
  return doneBy ? `done by ${doneBy}` : "done";
}

/** Bar colour for one day of the adherence chart. */
export function dayBarStyle(pct: number | null): string {
  if (pct === null) return "bg-slate-200";
  if (pct >= OFTEN_MISSED_BELOW) return "bg-green-600";
  return pct >= 50 ? "bg-amber-500" : "bg-red-600";
}

/** Bar height in pixels: a visible stub even on a 0% day, growing to 68 px at 100%. */
export const dayBarHeight = (pct: number | null): number => 8 + (pct ?? 0) * 0.6;

export const oftenMissed = (pct: number | null): boolean => pct !== null && pct < OFTEN_MISSED_BELOW;

/** "Morning 8:00, Night 9:00", or "only when needed". */
export function medTimes(m: Pick<Med, Slot | "as_needed">): string {
  if (m.as_needed) return "only when needed";
  return SLOTS.filter((s) => m[s]).map((s) => SLOT_LABEL[s]).join(", ");
}

/** "50%" or "–" when nothing was due yet. */
export const pctText = (pct: number | null): string => (pct === null ? "–" : `${pct}%`);

/** An activity-log action as words: "wrong_passcode" -> "wrong passcode". */
export const actionText = (action: string): string => action.replaceAll("_", " ");
