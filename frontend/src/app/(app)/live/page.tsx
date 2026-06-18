"use client";
import * as React from "react";
import { fmtDateTime } from "@/lib/utils";
import { useLiveCalls } from "@/lib/use-live-calls";
import { RequireHospital } from "@/components/require-hospital";
import { PageHeader, EmptyState, Spinner, Badge, Card, CardBody } from "@/components/ui";
import type { CallLog } from "@/lib/types";

function useElapsed(startedAt?: string | null): string {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!startedAt) return "—";
  const diff = now - new Date(startedAt).getTime();
  if (isNaN(diff) || diff < 0) return "—";
  const totalSecs = Math.floor(diff / 1000);
  const mins = Math.floor(totalSecs / 60);
  const secs = totalSecs % 60;
  return `${mins}m ${String(secs).padStart(2, "0")}s`;
}

function ActiveCallCard({ call }: { call: CallLog }) {
  const elapsed = useElapsed(call.started_at);
  return (
    <Card className="w-full sm:w-72">
      <CardBody className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="font-mono text-sm text-gray-800">{call.caller ?? "Unknown"}</span>
          <span className="inline-flex h-2 w-2 rounded-full bg-green-500 ring-2 ring-green-200 animate-pulse" />
        </div>
        <div className="text-xs text-gray-500 space-y-0.5">
          <div>
            <span className="font-medium">Started:</span> {fmtDateTime(call.started_at)}
          </div>
          <div>
            <span className="font-medium">Elapsed:</span> {elapsed}
          </div>
          {call.outcome && (
            <div className="flex items-center gap-1">
              <span className="font-medium">Status:</span>
              <Badge tone="green">{call.outcome}</Badge>
            </div>
          )}
          {call.intents && (
            <div className="flex items-center gap-1 flex-wrap">
              <span className="font-medium">Intent:</span>
              {(typeof call.intents === "string"
                ? (() => { try { return JSON.parse(call.intents); } catch { return [call.intents]; } })()
                : call.intents
              ).slice(0, 2).map((intent: string, i: number) => (
                <Badge key={i} tone="blue" className="text-[10px]">{intent}</Badge>
              ))}
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

function StatusPill({ status }: { status: ReturnType<typeof useLiveCalls>["status"] }) {
  if (status === "connecting") {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-gray-500">
        <Spinner className="h-3 w-3" /> Connecting…
      </span>
    );
  }
  if (status === "live") {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-green-600">
        <span className="inline-flex h-2 w-2 rounded-full bg-green-500 ring-2 ring-green-200 animate-pulse" />
        Live
      </span>
    );
  }
  if (status === "polling") {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-amber-600">
        <span className="inline-flex h-2 w-2 rounded-full bg-amber-500" />
        Polling (5s) — real-time feed unavailable
      </span>
    );
  }
  return null;
}

function LiveInner({ hospitalId }: { hospitalId: string }) {
  const { calls, status } = useLiveCalls(hospitalId);

  return (
    <div className="space-y-6">
      <PageHeader title="Live Calls" action={<StatusPill status={status} />} />

      {status === "error" ? (
        <EmptyState
          title="Live monitoring not available yet"
          hint="The active-calls endpoint ships with the backend additions (monitoring_api + live_ws)."
        />
      ) : status === "connecting" && calls.length === 0 ? (
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Spinner /> Connecting to live feed…
        </div>
      ) : calls.length === 0 ? (
        <EmptyState
          title="No active calls right now"
          hint={
            status === "live"
              ? "New calls will appear here instantly."
              : "This page refreshes every 5 seconds while the live feed is unavailable."
          }
        />
      ) : (
        <>
          <div className="text-sm text-gray-600">
            {calls.length} active {calls.length === 1 ? "call" : "calls"}
          </div>
          <div className="flex flex-wrap gap-4">
            {calls.map((call) => (
              <ActiveCallCard key={call.call_id ?? call.id} call={call} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default function LivePage() {
  return (
    <RequireHospital>
      {(hid) => <LiveInner hospitalId={hid} />}
    </RequireHospital>
  );
}
