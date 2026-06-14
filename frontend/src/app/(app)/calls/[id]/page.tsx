"use client";
import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { fmtDateTime, fmtMs, paiseToRupees, parseMaybeJson } from "@/lib/utils";
import { RequireHospital } from "@/components/require-hospital";
import { TranscriptView } from "@/components/transcript-view";
import {
  PageHeader, EmptyState, Spinner, Badge, Card, CardBody, CardHeader,
} from "@/components/ui";
import type { TranscriptTurn } from "@/lib/types";

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="w-32 flex-shrink-0 font-medium text-gray-500">{label}</span>
      <span className="text-gray-900">{value}</span>
    </div>
  );
}

function CallDetailInner({ hospitalId }: { hospitalId: string }) {
  const params = useParams();
  const id = Array.isArray(params.id) ? params.id[0] : (params.id as string);

  // Try the single-call endpoint (planned)
  const callQuery = useQuery({
    queryKey: ["call", hospitalId, id],
    queryFn: () => api.getCall(hospitalId, id),
    retry: false,
  });

  // Fallback: find in list
  const listQuery = useQuery({
    queryKey: ["calls", hospitalId, 100],
    queryFn: () => api.listCalls(hospitalId, 100),
    enabled: callQuery.isError,
    retry: false,
  });

  const call = callQuery.data ?? listQuery.data?.find((c) => c.id === id);

  const isLoading = callQuery.isLoading || (callQuery.isError && listQuery.isLoading);
  const notFound = !isLoading && !call;

  const intents: string[] = parseMaybeJson<string[]>(call?.intents, []);
  const turns: TranscriptTurn[] = parseMaybeJson<TranscriptTurn[]>(
    call?.transcript,
    []
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Call Detail"
        action={
          <Link href="/calls" className="text-sm text-indigo-600 hover:underline">
            ← Back to calls
          </Link>
        }
      />

      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Spinner /> Loading call…
        </div>
      ) : notFound ? (
        <EmptyState
          title="Call not found"
          hint="This call may not be available yet, or the ID is invalid."
        />
      ) : call ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          {/* Metadata + intents */}
          <div className="space-y-4 lg:col-span-1">
            <Card>
              <CardHeader>
                <h2 className="text-sm font-semibold text-gray-700">Metadata</h2>
              </CardHeader>
              <CardBody className="space-y-2.5">
                <MetaRow label="Caller" value={<span className="font-mono text-xs">{call.caller ?? "—"}</span>} />
                <MetaRow label="Started" value={fmtDateTime(call.started_at)} />
                <MetaRow label="Ended" value={fmtDateTime(call.ended_at)} />
                <MetaRow label="Turns" value={call.total_turns} />
                <MetaRow label="Avg Latency" value={fmtMs(call.latency_avg_ms)} />
                <MetaRow label="Cost" value={paiseToRupees(call.cost_paise)} />
                <MetaRow
                  label="Outcome"
                  value={
                    call.outcome ? (
                      <Badge tone="blue">{call.outcome}</Badge>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )
                  }
                />
              </CardBody>
            </Card>

            {intents.length > 0 && (
              <Card>
                <CardHeader>
                  <h2 className="text-sm font-semibold text-gray-700">Intents</h2>
                </CardHeader>
                <CardBody>
                  <div className="flex flex-wrap gap-2">
                    {intents.map((intent, i) => (
                      <Badge key={i} tone="blue">{intent}</Badge>
                    ))}
                  </div>
                </CardBody>
              </Card>
            )}
          </div>

          {/* Transcript */}
          <div className="lg:col-span-2">
            <Card>
              <CardHeader>
                <h2 className="text-sm font-semibold text-gray-700">Transcript</h2>
              </CardHeader>
              <CardBody className="max-h-[600px] overflow-y-auto">
                <TranscriptView turns={turns} />
              </CardBody>
            </Card>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function CallDetailPage() {
  return (
    <RequireHospital>
      {(hid) => <CallDetailInner hospitalId={hid} />}
    </RequireHospital>
  );
}
