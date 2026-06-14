import { describe, it, expect } from "vitest";
import { cn, paiseToRupees, dowLabel, fmtDateTime, fmtMs, parseMaybeJson } from "@/lib/utils";

describe("paiseToRupees", () => {
  it("converts 0 to ₹0.00", () => {
    const result = paiseToRupees(0);
    expect(result).toMatch(/^₹/);
    expect(result).toContain("0.00");
  });

  it("converts 12345 paise to ₹123.45", () => {
    const result = paiseToRupees(12345);
    expect(result).toMatch(/^₹/);
    expect(result).toContain("123.45");
  });

  it("handles null -> ₹0.00", () => {
    const result = paiseToRupees(null);
    expect(result).toMatch(/^₹/);
    expect(result).toContain("0.00");
  });

  it("handles undefined -> ₹0.00", () => {
    const result = paiseToRupees(undefined);
    expect(result).toMatch(/^₹/);
    expect(result).toContain("0.00");
  });
});

describe("dowLabel", () => {
  it("returns Sun for 0", () => {
    expect(dowLabel(0)).toBe("Sun");
  });

  it("returns Mon for 1", () => {
    expect(dowLabel(1)).toBe("Mon");
  });

  it("returns Sat for 6", () => {
    expect(dowLabel(6)).toBe("Sat");
  });

  it("returns String(n) for out-of-range index", () => {
    expect(dowLabel(7)).toBe("7");
    expect(dowLabel(-1)).toBe("-1");
  });
});

describe("fmtMs", () => {
  it("returns — for undefined", () => {
    expect(fmtMs(undefined)).toBe("—");
  });

  it("returns — for null", () => {
    expect(fmtMs(null)).toBe("—");
  });

  it("returns ms string for values under 1000", () => {
    expect(fmtMs(250)).toBe("250ms");
  });

  it("returns seconds string for values >= 1000", () => {
    expect(fmtMs(1500)).toBe("1.50s");
  });
});

describe("fmtDateTime", () => {
  it("returns — for undefined", () => {
    expect(fmtDateTime(undefined)).toBe("—");
  });

  it("returns — for empty string", () => {
    expect(fmtDateTime("")).toBe("—");
  });

  it("returns — for invalid ISO string", () => {
    expect(fmtDateTime("not-a-date")).toBe("—");
  });

  it("returns a non-— string for a valid ISO date", () => {
    const result = fmtDateTime("2024-01-15T10:30:00.000Z");
    expect(result).not.toBe("—");
    expect(typeof result).toBe("string");
    expect(result.length).toBeGreaterThan(0);
  });
});

describe("parseMaybeJson", () => {
  it("passes through arrays as-is", () => {
    const arr = [1, 2, 3];
    expect(parseMaybeJson(arr, [])).toBe(arr);
  });

  it("passes through objects as-is", () => {
    const obj = { a: 1 };
    expect(parseMaybeJson(obj, {})).toBe(obj);
  });

  it("parses a JSON string", () => {
    expect(parseMaybeJson<number[]>('["x","y"]', [])).toEqual(["x", "y"]);
  });

  it("returns fallback on invalid JSON string", () => {
    const fallback = { default: true };
    expect(parseMaybeJson("{bad json}", fallback)).toBe(fallback);
  });

  it("returns fallback on null", () => {
    const fallback = ["fallback"];
    expect(parseMaybeJson(null, fallback)).toBe(fallback);
  });
});

describe("cn", () => {
  it("merges multiple class strings", () => {
    const result = cn("foo", "bar");
    expect(result).toContain("foo");
    expect(result).toContain("bar");
  });

  it("dedupes conflicting tailwind classes (last wins)", () => {
    const result = cn("p-2", "p-4");
    expect(result).toContain("p-4");
    expect(result).not.toContain("p-2");
  });

  it("handles falsy values gracefully", () => {
    const result = cn("text-sm", undefined, false, "font-bold");
    expect(result).toContain("text-sm");
    expect(result).toContain("font-bold");
  });
});
