"use client";
import * as React from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtDateTime } from "@/lib/utils";

export type WaItem = {
  id: string;
  name: string;
  phone: string;
  body: string;
  at: string;
};

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (isNaN(m)) return "";
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return fmtDateTime(iso);
}

const COLLAPSE_AT = 120; // chars before we show the expand toggle

function MessageRow({ m }: { m: WaItem }) {
  const [open, setOpen] = React.useState(false);
  const long = m.body.length > COLLAPSE_AT;
  const shown = open || !long ? m.body : m.body.slice(0, COLLAPSE_AT).trimEnd() + "…";

  return (
    <li className="rounded-lg bg-green-50 p-2.5 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-gray-700">{m.name}</span>
        <span className="shrink-0 text-[11px] text-gray-400">{timeAgo(m.at)}</span>
      </div>
      <p className={cn("mt-0.5 whitespace-pre-wrap break-words text-xs text-gray-700")}>{shown}</p>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="truncate text-[10px] text-gray-400">{m.phone}</span>
        {long && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="inline-flex shrink-0 items-center gap-0.5 text-[11px] font-medium text-brand-700 hover:underline"
          >
            {open ? (
              <>
                Show less <ChevronUp className="h-3 w-3" />
              </>
            ) : (
              <>
                Expand <ChevronDown className="h-3 w-3" />
              </>
            )}
          </button>
        )}
      </div>
    </li>
  );
}

/** Expandable WhatsApp message list, shared by the mock and the real dashboard. */
export function WhatsAppFeed({ items }: { items: WaItem[] }) {
  return (
    <ul className="space-y-2 p-3">
      {items.map((m) => (
        <MessageRow key={m.id} m={m} />
      ))}
    </ul>
  );
}
