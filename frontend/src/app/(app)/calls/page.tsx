"use client";
import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronUp } from "lucide-react";
import { api } from "@/lib/api";
import { fmtDateTime, fmtMs, paiseToRupees, parseMaybeJson } from "@/lib/utils";
import { RequireHospital } from "@/components/require-hospital";
import { RecordingPlayer } from "@/components/audio-player";
import { TranscriptView } from "@/components/transcript-view";
import { PageHeader, EmptyState, Spinner, Badge, Card, CardBody, CardHeader, Input } from "@/components/ui";
import type { CallLog, TranscriptTurn } from "@/lib/types";

function deriveDuration(started_at?: string | null, ended_at?: string | null): string {
  if (!started_at || !ended_at) return "—";
  const s = new Date(started_at).getTime();
  const e = new Date(ended_at).getTime();
  if (isNaN(s) || isNaN(e) || e <= s) return "—";
  const secs = Math.floor((e - s) / 1000);
  const mins = Math.floor(secs / 60);
  return mins > 0 ? `${mins}m ${secs % 60}s` : `${secs}s`;
}

function outcomeTone(v?: string | null): "green" | "blue" | "red" | "gray" {
  if (v === "resolved") return "green";
  if (v === "transferred") return "blue";
  if (v === "dropped") return "red";
  return "gray";
}

function CallRow({ call }: { call: CallLog }) {
  const [open, setOpen] = React.useState(false);
  const turns: TranscriptTurn[] = parseMaybeJson<TranscriptTurn[]>(call.transcript, []);

  return (
    <li className="text-sm">
      {/* Summary row — click anywhere to expand */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-gray-50"
      >
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-gray-900">{call.caller ?? "Unknown caller"}</p>
          <p className="truncate text-xs text-gray-500">
            {fmtDateTime(call.started_at)} · {deriveDuration(call.started_at, call.ended_at)} ·{" "}
            {call.total_turns} turns
          </p>
        </div>
        <div className="hidden shrink-0 text-right text-xs text-gray-500 sm:block">
          <p>{fmtMs(call.latency_avg_ms)}</p>
          <p>{paiseToRupees(call.cost_paise)}</p>
        </div>
        {call.outcome ? (
          <Badge tone={outcomeTone(call.outcome)}>{call.outcome}</Badge>
        ) : (
          <span className="text-gray-300">—</span>
        )}
        {open ? (
          <ChevronUp className="h-4 w-4 shrink-0 text-gray-400" />
        ) : (
          <ChevronDown className="h-4 w-4 shrink-0 text-gray-400" />
        )}
      </button>

      {/* Expanded panel — recording + transcript */}
      {open && (
        <div className="space-y-3 border-t border-gray-100 bg-gray-50/60 px-4 py-3">
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Recording</p>
            <RecordingPlayer src={call.recording_url} />
          </div>
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Transcript</p>
            {turns.length > 0 ? (
              <div className="max-h-72 overflow-y-auto rounded-lg border border-gray-200 bg-white p-3">
                <TranscriptView turns={turns} />
              </div>
            ) : (
              <p className="text-xs text-gray-400">No transcript captured for this call.</p>
            )}
          </div>
          <Link href={`/calls/${call.id}`} className="inline-block text-xs text-brand-700 hover:underline">
            Open full detail →
          </Link>
        </div>
      )}
    </li>
  );
}

function CallsInner({ hospitalId }: { hospitalId: string }) {
  const [q, setQ] = React.useState("");
  const { data = [], isLoading, isError } = useQuery({
    queryKey: ["calls", hospitalId, 100],
    queryFn: () => api.listCalls(hospitalId, 100),
  });

  const filtered = React.useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return data;
    return data.filter(
      (c) =>
        (c.caller ?? "").toLowerCase().includes(needle) ||
        (c.outcome ?? "").toLowerCase().includes(needle)
    );
  }, [data, q]);

  return (
    <div className="space-y-4">
      <PageHeader title="Call Logs" />
      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500"><Spinner /> Loading calls…</div>
      ) : isError ? (
        <EmptyState title="Could not load calls" hint="Check your connection and try again." />
      ) : (
        <Card>
          <CardHeader>
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search by caller or outcome…"
              className="max-w-xs"
            />
          </CardHeader>
          <CardBody className="p-0">
            {filtered.length === 0 ? (
              <EmptyState title="No calls recorded yet" hint="Calls appear here once the agent handles them." />
            ) : (
              <ul className="divide-y divide-gray-100">
                {filtered.map((c) => (
                  <CallRow key={c.id} call={c} />
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  );
}

export default function CallsPage() {
  return <RequireHospital>{(hid) => <CallsInner hospitalId={hid} />}</RequireHospital>;
}
