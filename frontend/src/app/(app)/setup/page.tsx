"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { PageHeader, Button, Card, CardHeader, CardBody, Badge, Spinner, EmptyState } from "@/components/ui";
import { RequireHospital } from "@/components/require-hospital";
import { useToast } from "@/components/providers";

function prettifyKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = React.useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <Button variant="outline" className="px-2 py-1 text-xs" onClick={copy}>
      {copied ? "Copied!" : "Copy"}
    </Button>
  );
}

function SetupInner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [provisionResult, setProvisionResult] = React.useState<{
    plivo_number: string;
    bsnl_forward_code?: string;
  } | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["setup", hospitalId],
    queryFn: () => api.setupStatus(hospitalId),
  });

  const provisionMutation = useMutation({
    mutationFn: () => api.provisionNumber(hospitalId),
    onSuccess: (result) => {
      setProvisionResult(result);
      qc.invalidateQueries({ queryKey: ["setup", hospitalId] });
      toast("Plivo number provisioned successfully");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  if (isLoading) {
    return <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>;
  }
  if (isError || !data) {
    return <EmptyState title="Could not load setup status" hint="Check your connection and try again." />;
  }

  const checks = Object.entries(data.checks);
  const allPassed = checks.every(([, v]) => v);

  return (
    <div>
      <PageHeader title="Setup Checklist" />

      <Card className="mb-6">
        <CardHeader>
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold text-gray-700">Configuration Checks</span>
            <Badge tone={allPassed ? "green" : "yellow"}>{allPassed ? "All Passed" : "Incomplete"}</Badge>
          </div>
        </CardHeader>
        <CardBody>
          {checks.length === 0 ? (
            <p className="text-sm text-gray-400">No checks available.</p>
          ) : (
            <ul className="space-y-2">
              {checks.map(([key, val]) => (
                <li key={key} className="flex items-center gap-3 text-sm">
                  <span className="text-base">{val ? "✅" : "❌"}</span>
                  <span className={val ? "text-gray-700" : "text-red-600"}>{prettifyKey(key)}</span>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      {data.bsnl_forward_code && (
        <Card className="mb-6">
          <CardHeader>
            <span className="text-sm font-semibold text-gray-700">BSNL Call Forward Code</span>
          </CardHeader>
          <CardBody>
            <div className="flex items-center gap-3">
              <code className="font-mono text-sm text-gray-800">{data.bsnl_forward_code}</code>
              <CopyButton text={data.bsnl_forward_code} />
            </div>
          </CardBody>
        </Card>
      )}

      <div className="mb-4">
        <Button
          onClick={() => provisionMutation.mutate()}
          disabled={provisionMutation.isPending}
        >
          {provisionMutation.isPending && <Spinner />} Provision Plivo Number
        </Button>
      </div>

      {provisionResult && (
        <Card>
          <CardHeader>
            <span className="text-sm font-semibold text-green-600">Number Provisioned</span>
          </CardHeader>
          <CardBody className="space-y-3">
            <div>
              <p className="mb-1 text-xs font-medium text-gray-500">Plivo Number</p>
              <div className="flex items-center gap-3">
                <code className="font-mono text-sm text-gray-800">{provisionResult.plivo_number}</code>
                <CopyButton text={provisionResult.plivo_number} />
              </div>
            </div>
            {provisionResult.bsnl_forward_code && (
              <div>
                <p className="mb-1 text-xs font-medium text-gray-500">BSNL Forward Code</p>
                <div className="flex items-center gap-3">
                  <code className="font-mono text-sm text-gray-800">
                    **21*{provisionResult.plivo_number}#
                  </code>
                  <CopyButton text={`**21*${provisionResult.plivo_number}#`} />
                </div>
                <p className="mt-1 text-xs text-gray-400">
                  Dial this code on a BSNL line to forward calls to your Plivo number.
                </p>
              </div>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  );
}

export default function SetupPage() {
  return (
    <RequireHospital>
      {(hospitalId) => <SetupInner hospitalId={hospitalId} />}
    </RequireHospital>
  );
}
