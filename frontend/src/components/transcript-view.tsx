"use client";
import * as React from "react";
import { EmptyState } from "@/components/ui";
import { cn, fmtMs } from "@/lib/utils";
import type { TranscriptTurn } from "@/lib/types";

interface TranscriptViewProps {
  turns: TranscriptTurn[];
}

export function TranscriptView({ turns }: TranscriptViewProps) {
  if (!turns || turns.length === 0) {
    return <EmptyState title="No transcript available" hint="The transcript will appear here once the call has ended." />;
  }

  return (
    <div className="flex flex-col gap-3 py-2">
      {turns.map((turn, idx) => {
        const isAgent = turn.role === "agent";
        return (
          <div
            key={idx}
            className={cn("flex flex-col max-w-[75%]", isAgent ? "self-end items-end" : "self-start items-start")}
          >
            <span className="text-[10px] font-semibold uppercase tracking-wide mb-1 text-gray-400">
              {isAgent ? "Agent" : "User"}
            </span>
            <div
              className={cn(
                "rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
                isAgent
                  ? "bg-indigo-600 text-white rounded-br-sm"
                  : "bg-gray-100 text-gray-800 rounded-bl-sm"
              )}
            >
              {turn.text}
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              {turn.ts && (
                <span className="text-[10px] text-gray-400">
                  {new Date(turn.ts).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </span>
              )}
              {turn.latency_ms != null && (
                <span className="text-[10px] text-gray-400">
                  {fmtMs(turn.latency_ms)}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
