import { describe, expect, it } from "vitest";
import {
  OFTEN_MISSED_BELOW, SLOTS, actionText, dayBarHeight, dayBarStyle, medTimes, oftenMissed, pctText, routineLabel, stateStyle,
} from "@/lib/circleView";

const med = (over: Partial<Record<"morning" | "noon" | "night" | "as_needed", number>> = {}) =>
  ({ morning: 0, noon: 0, night: 0, as_needed: 0, ...over });

describe("dose and routine states", () => {
  it("gives every state its own colour and keeps the text readable", () => {
    const styles = (["taken", "missed", "due", "upcoming"] as const).map(stateStyle);
    expect(new Set(styles).size).toBe(4);
    for (const s of styles) expect(s).toMatch(/text-\w+-(800|900)/); // dark text on a light badge: WCAG AA contrast
  });

  it("names who did a routine once it is done", () => {
    expect(routineLabel("taken", "Asha")).toBe("done by Asha");
    expect(routineLabel("taken", null)).toBe("done");
    expect(routineLabel("missed", "Asha")).toBe("missed");
    expect(routineLabel("upcoming", null)).toBe("upcoming");
  });
});

describe("adherence chart", () => {
  it("colours a day by how many doses were taken, matching the API's 80% rule", () => {
    expect(OFTEN_MISSED_BELOW).toBe(80);
    expect(dayBarStyle(null)).toBe("bg-slate-200");
    expect(dayBarStyle(100)).toBe("bg-green-600");
    expect(dayBarStyle(80)).toBe("bg-green-600");
    expect(dayBarStyle(79)).toBe("bg-amber-500");
    expect(dayBarStyle(50)).toBe("bg-amber-500");
    expect(dayBarStyle(49)).toBe("bg-red-600");
    expect(dayBarStyle(0)).toBe("bg-red-600");
  });

  it("keeps a visible bar even on an empty day", () => {
    expect(dayBarHeight(null)).toBe(8);
    expect(dayBarHeight(0)).toBe(8);
    expect(dayBarHeight(100)).toBe(68);
    expect(dayBarHeight(50)).toBeLessThan(dayBarHeight(51));
  });

  it("flags a medicine as often missed below 80% only", () => {
    expect(oftenMissed(79)).toBe(true);
    expect(oftenMissed(80)).toBe(false);
    expect(oftenMissed(null)).toBe(false); // nothing due yet is not "missed"
  });

  it("shows a dash when nothing was due", () => {
    expect(pctText(null)).toBe("–");
    expect(pctText(0)).toBe("0%");
    expect(pctText(92)).toBe("92%");
  });
});

describe("medicine timing", () => {
  it("lists the slots in the day's order", () => {
    expect(SLOTS).toEqual(["morning", "noon", "night"]);
    expect(medTimes(med({ night: 1, morning: 1 }))).toBe("Morning 8:00, Night 9:00");
    expect(medTimes(med({ noon: 1 }))).toBe("Afternoon 2:00");
  });

  it("says 'only when needed' instead of times for as-needed medicines", () => {
    expect(medTimes(med({ as_needed: 1, morning: 1 }))).toBe("only when needed");
  });
});

describe("activity log", () => {
  it("turns every underscore into a space", () => {
    expect(actionText("wrong_passcode")).toBe("wrong passcode");
    expect(actionText("a_b_c")).toBe("a b c");
    expect(actionText("joined")).toBe("joined");
  });
});
