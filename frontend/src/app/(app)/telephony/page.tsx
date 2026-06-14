"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { PageHeader, Badge, Button, Card, CardHeader, CardBody, Spinner, EmptyState } from "@/components/ui";
import { RequireHospital } from "@/components/require-hospital";
import { useToast } from "@/components/providers";

function BoolMap({ title, map }: { title: string; map?: Record<string, boolean> }) {
  if (!map) return null;
  const entries = Object.entries(map);
  return (
    <Card>
      <CardHeader>
        <span className="text-sm font-semibold text-gray-700">{title}</span>
      </CardHeader>
      <CardBody>
        {entries.length === 0 ? (
          <p className="text-sm text-gray-400">No items</p>
        ) : (
          <ul className="space-y-1.5">
            {entries.map(([key, val]) => (
              <li key={key} className="flex items-center gap-2 text-sm">
                <span>{val ? "✅" : "❌"}</span>
                <span className="font-mono text-xs text-gray-700">{key}</span>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function TelephonyInner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["telephony", hospitalId],
    queryFn: () => api.telephonyStatus(hospitalId),
  });

  const sipMutation = useMutation({
    mutationFn: () => {
      if (!window.confirm("Run SIP setup? This may affect live calls.")) {
        return Promise.reject(new Error("Cancelled"));
      }
      return api.runSipSetup();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["telephony", hospitalId] });
      toast("SIP setup completed successfully");
    },
    onError: (e: Error) => {
      if (e.message !== "Cancelled") toast(e.message, "err");
    },
  });

  if (isLoading) {
    return <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>;
  }
  if (isError || !data) {
    return <EmptyState title="Could not load telephony status" hint="Check your connection and try again." />;
  }

  return (
    <div>
      <PageHeader
        title="Telephony Status"
        action={
          <Button
            variant="outline"
            onClick={() => sipMutation.mutate()}
            disabled={sipMutation.isPending}
          >
            {sipMutation.isPending && <Spinner />} Run SIP Setup
          </Button>
        }
      />

      <div className="mb-6 flex items-center gap-3">
        <span className="text-sm font-medium text-gray-600">Overall</span>
        <Badge tone={data.overall.sip_calls_ready ? "green" : "red"}>
          {data.overall.sip_calls_ready ? "Ready" : "Not Ready"}
        </Badge>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <BoolMap title="Plivo" map={data.plivo} />
        <BoolMap title="LiveKit" map={data.livekit} />
      </div>

      {data.missing.length > 0 && (
        <Card className="mt-4">
          <CardHeader>
            <span className="text-sm font-semibold text-red-600">Missing Configuration</span>
          </CardHeader>
          <CardBody>
            <ul className="space-y-1">
              {data.missing.map((item) => (
                <li key={item} className="flex items-center gap-2 text-sm">
                  <span>❌</span>
                  <span className="font-mono text-xs text-gray-700">{item}</span>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      {data.bsnl_forward_code && (
        <Card className="mt-4">
          <CardHeader>
            <span className="text-sm font-semibold text-gray-700">BSNL Call Forward Code</span>
          </CardHeader>
          <CardBody>
            <p className="font-mono text-sm text-gray-800">{data.bsnl_forward_code}</p>
            <p className="mt-1 text-xs text-gray-400">Dial this code on a BSNL line to activate call forwarding.</p>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

export default function TelephonyPage() {
  return (
    <RequireHospital>
      {(hospitalId) => <TelephonyInner hospitalId={hospitalId} />}
    </RequireHospital>
  );
}
