"use client";
import * as React from "react";
import {
  addMonths,
  eachDayOfInterval,
  endOfMonth,
  endOfWeek,
  format,
  isSameDay,
  isSameMonth,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

const TIME_SLOTS = [
  "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
  "14:00", "14:30", "15:00", "15:30", "16:00", "16:30",
];

/**
 * Inline date + time calendar picker. `value` / `onChange` work on an ISO
 * datetime string (or null while nothing is selected).
 */
export function CalendarPicker({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (iso: string | null) => void;
}) {
  const selected = value ? new Date(value) : null;
  const [month, setMonth] = React.useState<Date>(selected ?? new Date());

  const days = React.useMemo(() => {
    const start = startOfWeek(startOfMonth(month), { weekStartsOn: 0 });
    const end = endOfWeek(endOfMonth(month), { weekStartsOn: 0 });
    return eachDayOfInterval({ start, end });
  }, [month]);

  const selectedTime = selected ? format(selected, "HH:mm") : null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const pickDay = (day: Date) => {
    const next = new Date(day);
    const [h, m] = (selectedTime ?? "10:00").split(":").map(Number);
    next.setHours(h, m, 0, 0);
    onChange(next.toISOString());
  };

  const pickTime = (t: string) => {
    const base = selected ?? new Date();
    const [h, m] = t.split(":").map(Number);
    const next = new Date(base);
    next.setHours(h, m, 0, 0);
    onChange(next.toISOString());
  };

  return (
    <div className="grid gap-4 sm:grid-cols-[1fr_auto]">
      <div className="rounded-lg border border-gray-200 p-3">
        <div className="mb-2 flex items-center justify-between">
          <button
            type="button"
            onClick={() => setMonth((m) => addMonths(m, -1))}
            className="rounded p-1 text-gray-500 hover:bg-gray-100"
            aria-label="Previous month"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-sm font-medium text-gray-900">{format(month, "MMMM yyyy")}</span>
          <button
            type="button"
            onClick={() => setMonth((m) => addMonths(m, 1))}
            className="rounded p-1 text-gray-500 hover:bg-gray-100"
            aria-label="Next month"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
        <div className="grid grid-cols-7 gap-1 text-center">
          {["S", "M", "T", "W", "T", "F", "S"].map((d, i) => (
            <div key={i} className="py-1 text-[10px] font-semibold uppercase text-gray-400">
              {d}
            </div>
          ))}
          {days.map((day) => {
            const isSel = selected && isSameDay(day, selected);
            const inMonth = isSameMonth(day, month);
            const isPast = day < today;
            return (
              <button
                key={day.toISOString()}
                type="button"
                disabled={isPast}
                onClick={() => pickDay(day)}
                className={cn(
                  "h-8 rounded-md text-sm transition",
                  !inMonth && "text-gray-300",
                  isPast && "cursor-not-allowed text-gray-300",
                  !isPast && inMonth && !isSel && "text-gray-700 hover:bg-brand-50",
                  isSel && "bg-brand-600 font-semibold text-white hover:bg-brand-600"
                )}
              >
                {format(day, "d")}
              </button>
            );
          })}
        </div>
      </div>

      <div className="w-full sm:w-40">
        <p className="mb-2 text-xs font-medium text-gray-600">Time slot</p>
        <div className="grid max-h-[214px] grid-cols-3 gap-1.5 overflow-y-auto sm:grid-cols-2">
          {TIME_SLOTS.map((t) => {
            const isSel = selectedTime === t;
            return (
              <button
                key={t}
                type="button"
                onClick={() => pickTime(t)}
                className={cn(
                  "rounded-md border px-2 py-1.5 text-xs transition",
                  isSel
                    ? "border-brand-600 bg-brand-600 font-medium text-white"
                    : "border-gray-200 text-gray-700 hover:bg-gray-50"
                )}
              >
                {t}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/**
 * Deterministic faux-QR block for the receptionist payment slip. It's purely
 * decorative — a real build would render an actual UPI / payment QR here.
 */
export function FakeQR({ seed, size = 132 }: { seed: string; size?: number }) {
  const cells = 21;
  const grid = React.useMemo(() => {
    // Simple seeded PRNG so the same seed always renders the same pattern.
    let h = 0;
    for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
    const rand = () => {
      h = (h * 1103515245 + 12345) & 0x7fffffff;
      return h / 0x7fffffff;
    };
    return Array.from({ length: cells * cells }, () => rand() > 0.5);
  }, [seed]);

  const isFinder = (r: number, c: number) => {
    const inBox = (br: number, bc: number) => r >= br && r < br + 7 && c >= bc && c < bc + 7;
    return inBox(0, 0) || inBox(0, cells - 7) || inBox(cells - 7, 0);
  };

  const cell = size / cells;
  return (
    <svg width={size} height={size} className="rounded-lg border border-gray-200 bg-white p-1">
      {grid.map((on, i) => {
        const r = Math.floor(i / cells);
        const c = i % cells;
        const dark = isFinder(r, c) ? true : on;
        if (!dark) return null;
        return (
          <rect key={i} x={c * cell} y={r * cell} width={cell} height={cell} fill="#111827" />
        );
      })}
    </svg>
  );
}
