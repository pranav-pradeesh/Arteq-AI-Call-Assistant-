"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { HisConfig } from "@/lib/types";
import { PageHeader, Button, Card, CardHeader, CardBody, Field, Input, Select, Badge, Spinner, EmptyState } from "@/components/ui";
import { RequireHospital } from "@/components/require-hospital";
import { useToast } from "@/components/providers";

const ENDPOINT_KEYS = [
  { key: "search_patient", label: "Search Patient", placeholder: "e.g. /patients?phone={phone}" },
  { key: "get_slots", label: "Get Slots", placeholder: "e.g. /slots?doctor={doctor_id}&date={date}" },
  { key: "create_appointment", label: "Create Appointment", placeholder: "e.g. /appointments (POST)" },
  { key: "cancel_appointment", label: "Cancel Appointment", placeholder: "e.g. /appointments/{appointment_id}/cancel" },
];

const EMPTY_CONFIG: HisConfig = {
  type: "none",
  base_url: "",
  auth: { type: "bearer", header: "", value: "" },
  endpoints: {},
};

function HisInner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [form, setForm] = React.useState<HisConfig>(EMPTY_CONFIG);
  const [testResult, setTestResult] = React.useState<{ ok: boolean; detail?: string } | null>(null);

  const { data: hisData, isLoading, isError } = useQuery({
    queryKey: ["his-config", hospitalId],
    queryFn: () => api.getHisConfig(hospitalId),
  });

  React.useEffect(() => {
    if (hisData) {
      setForm({
        ...EMPTY_CONFIG,
        ...hisData,
        auth: { type: "bearer", header: "", value: "", ...(hisData.auth ?? {}) },
        endpoints: hisData.endpoints ?? {},
      });
    }
  }, [hisData]);

  const saveMutation = useMutation({
    mutationFn: (cfg: HisConfig) => api.saveHisConfig(hospitalId, cfg),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["his-config", hospitalId] });
      toast("HIS configuration saved");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const testMutation = useMutation({
    mutationFn: () => api.testHisConfig(hospitalId),
    onSuccess: (result) => {
      setTestResult(result);
      toast(result.ok ? "Connection test passed" : "Connection test failed", result.ok ? "ok" : "err");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  function setField<K extends keyof HisConfig>(key: K, value: HisConfig[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function setAuth(key: keyof NonNullable<HisConfig["auth"]>, value: string) {
    setForm((f) => ({ ...f, auth: { ...(f.auth ?? { type: "bearer" }), [key]: value } }));
  }

  function setEndpoint(key: string, value: string) {
    setForm((f) => ({ ...f, endpoints: { ...(f.endpoints ?? {}), [key]: value } }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const payload: HisConfig = { ...form };
    // Strip empty auth value (keep existing on backend)
    if (payload.auth?.value === "") {
      payload.auth = { ...payload.auth, value: undefined };
    }
    saveMutation.mutate(payload);
  }

  if (isLoading) {
    return <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>;
  }
  if (isError) {
    return <EmptyState title="Could not load HIS configuration" hint="Check your connection and try again." />;
  }

  const showDetails = form.type === "fhir" || form.type === "generic_rest";

  return (
    <div>
      <PageHeader title="HIS Integration" />
      <form onSubmit={handleSubmit}>
        <Card className="mb-4">
          <CardHeader>
            <span className="text-sm font-semibold text-gray-700">Connection</span>
          </CardHeader>
          <CardBody className="space-y-3">
            <Field label="Integration Type">
              <Select
                value={form.type}
                onChange={(e) => setField("type", e.target.value as HisConfig["type"])}
              >
                <option value="none">None</option>
                <option value="fhir">FHIR</option>
                <option value="generic_rest">Generic REST</option>
              </Select>
            </Field>

            {showDetails && (
              <>
                <Field label="Base URL">
                  <Input
                    type="url"
                    value={form.base_url ?? ""}
                    onChange={(e) => setField("base_url", e.target.value)}
                    placeholder="https://his.hospital.com/api"
                  />
                </Field>

                <Field label="Auth Type">
                  <Select
                    value={form.auth?.type ?? "bearer"}
                    onChange={(e) => setAuth("type", e.target.value)}
                  >
                    <option value="bearer">Bearer Token</option>
                    <option value="basic">Basic Auth</option>
                    <option value="api_key">API Key</option>
                    <option value="none">None</option>
                  </Select>
                </Field>

                {form.auth?.type !== "none" && (
                  <>
                    <Field label="Auth Header (optional)">
                      <Input
                        value={form.auth?.header ?? ""}
                        onChange={(e) => setAuth("header", e.target.value)}
                        placeholder="e.g. X-API-Key (leave blank for default)"
                      />
                    </Field>
                    <Field label="Auth Value">
                      <Input
                        type="password"
                        value={form.auth?.value ?? ""}
                        onChange={(e) => setAuth("value", e.target.value)}
                        placeholder="Leave ••• blank to keep existing"
                      />
                    </Field>
                  </>
                )}
              </>
            )}
          </CardBody>
        </Card>

        {showDetails && (
          <Card className="mb-4">
            <CardHeader>
              <span className="text-sm font-semibold text-gray-700">Endpoints</span>
            </CardHeader>
            <CardBody className="space-y-3">
              <p className="text-xs text-gray-400">
                Use placeholders: <code className="rounded bg-gray-100 px-1">{"{phone}"}</code>{" "}
                <code className="rounded bg-gray-100 px-1">{"{doctor_id}"}</code>{" "}
                <code className="rounded bg-gray-100 px-1">{"{date}"}</code>{" "}
                <code className="rounded bg-gray-100 px-1">{"{appointment_id}"}</code>
              </p>
              {ENDPOINT_KEYS.map(({ key, label, placeholder }) => (
                <Field key={key} label={label}>
                  <Input
                    value={form.endpoints?.[key] ?? ""}
                    onChange={(e) => setEndpoint(key, e.target.value)}
                    placeholder={placeholder}
                  />
                </Field>
              ))}
            </CardBody>
          </Card>
        )}

        <div className="flex items-center gap-3">
          <Button type="submit" disabled={saveMutation.isPending}>
            {saveMutation.isPending && <Spinner />} Save
          </Button>
          {showDetails && (
            <Button
              type="button"
              variant="outline"
              onClick={() => testMutation.mutate()}
              disabled={testMutation.isPending}
            >
              {testMutation.isPending && <Spinner />} Test Connection
            </Button>
          )}
          {testResult && (
            <div className="flex items-center gap-2">
              <Badge tone={testResult.ok ? "green" : "red"}>
                {testResult.ok ? "Connected" : "Failed"}
              </Badge>
              {testResult.detail && (
                <span className="text-xs text-gray-500">{testResult.detail}</span>
              )}
            </div>
          )}
        </div>
      </form>
    </div>
  );
}

export default function HisPage() {
  return (
    <RequireHospital>
      {(hospitalId) => <HisInner hospitalId={hospitalId} />}
    </RequireHospital>
  );
}
