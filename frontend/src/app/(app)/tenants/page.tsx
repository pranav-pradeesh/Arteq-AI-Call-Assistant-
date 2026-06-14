"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Tenant } from "@/lib/types";
import { PageHeader, Button, Badge, Field, Input, Select, Spinner, EmptyState } from "@/components/ui";
import { DataTable, ColumnDef } from "@/components/data-table";
import { FormModal, Modal } from "@/components/modal";
import { useToast } from "@/components/providers";

// ── Tenant form ────────────────────────────────────────────────────────────

type TenantForm = {
  name: string;
  name_ml: string;
  slug: string;
  db_url: string;
  agent_name: string;
  tier: string;
  phone: string;
  plivo_number: string;
  address: string;
  contact_person: string;
  contact_phone: string;
  notes: string;
};

const EMPTY_FORM: TenantForm = {
  name: "", name_ml: "", slug: "", db_url: "", agent_name: "",
  tier: "hospital", phone: "", plivo_number: "", address: "",
  contact_person: "", contact_phone: "", notes: "",
};

function tenantToForm(t: Tenant): TenantForm {
  return {
    name: t.name ?? "",
    name_ml: t.name_ml ?? "",
    slug: t.slug ?? "",
    db_url: t.db_url ?? "",
    agent_name: t.agent_name ?? "",
    tier: t.tier ?? "hospital",
    phone: t.phone ?? "",
    plivo_number: t.plivo_number ?? "",
    address: t.address ?? "",
    contact_person: t.contact_person ?? "",
    contact_phone: t.contact_phone ?? "",
    notes: t.notes ?? "",
  };
}

function formToPayload(f: TenantForm): Partial<Tenant> {
  return {
    name: f.name,
    name_ml: f.name_ml || undefined,
    slug: f.slug,
    db_url: f.db_url || undefined,
    agent_name: f.agent_name || undefined,
    tier: (f.tier as Tenant["tier"]) || undefined,
    phone: f.phone || undefined,
    plivo_number: f.plivo_number || undefined,
    address: f.address || undefined,
    contact_person: f.contact_person || undefined,
    contact_phone: f.contact_phone || undefined,
    notes: f.notes || undefined,
  };
}

// ── Features modal ─────────────────────────────────────────────────────────

function FeaturesModal({
  open,
  onClose,
  tenant,
}: {
  open: boolean;
  onClose: () => void;
  tenant: Tenant | null;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const [features, setFeatures] = React.useState<Record<string, boolean>>({});

  const { data: catalog, isLoading: catalogLoading } = useQuery({
    queryKey: ["features-catalog"],
    queryFn: api.featuresCatalog,
    enabled: open,
  });

  React.useEffect(() => {
    if (tenant?.features) setFeatures({ ...tenant.features });
    else setFeatures({});
  }, [tenant]);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!tenant) throw new Error("No tenant");
      return api.updateTenantFeatures(tenant.slug, features);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tenants"] });
      toast("Features saved");
      onClose();
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const allKeys = React.useMemo(() => {
    const catalogKeys = catalog ? Object.keys(catalog) : [];
    const tenantKeys = tenant?.features ? Object.keys(tenant.features) : [];
    return Array.from(new Set([...catalogKeys, ...tenantKeys])).sort();
  }, [catalog, tenant]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Features — ${tenant?.name ?? ""}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
            {saveMutation.isPending && <Spinner />} Save
          </Button>
        </>
      }
    >
      {catalogLoading ? (
        <div className="flex justify-center py-8"><Spinner className="h-5 w-5" /></div>
      ) : allKeys.length === 0 ? (
        <EmptyState title="No features available" />
      ) : (
        <div className="space-y-2">
          {allKeys.map((key) => (
            <label key={key} className="flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 hover:bg-gray-50">
              <input
                type="checkbox"
                checked={features[key] ?? false}
                onChange={(e) => setFeatures((f) => ({ ...f, [key]: e.target.checked }))}
                className="h-4 w-4 rounded border-gray-300"
              />
              <span className="text-sm text-gray-700">{key}</span>
              {catalog && key in catalog && (
                <span className="ml-auto text-xs text-gray-400">
                  {String((catalog as Record<string, unknown>)[key])}
                </span>
              )}
            </label>
          ))}
        </div>
      )}
    </Modal>
  );
}

// ── Tenant form modal ──────────────────────────────────────────────────────

function TenantFormModal({
  open,
  onClose,
  editing,
}: {
  open: boolean;
  onClose: () => void;
  editing: Tenant | null;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const [form, setForm] = React.useState<TenantForm>(EMPTY_FORM);

  React.useEffect(() => {
    if (editing) setForm(tenantToForm(editing));
    else setForm(EMPTY_FORM);
  }, [editing, open]);

  function set(key: keyof TenantForm, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = formToPayload(form);
      if (editing) return api.updateTenant(editing.slug, payload);
      return api.createTenant(payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tenants"] });
      toast(editing ? "Tenant updated" : "Tenant created");
      onClose();
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  return (
    <FormModal
      open={open}
      onClose={onClose}
      title={editing ? `Edit — ${editing.name}` : "New Tenant"}
      onSubmit={(e) => { e.preventDefault(); saveMutation.mutate(); }}
      saving={saveMutation.isPending}
      wide
    >
      <div className="grid grid-cols-2 gap-3">
        <Field label="Name *">
          <Input required value={form.name} onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label="Name (Malayalam)">
          <Input value={form.name_ml} onChange={(e) => set("name_ml", e.target.value)} />
        </Field>
        <Field label="Slug *">
          <Input
            required
            value={form.slug}
            onChange={(e) => set("slug", e.target.value)}
            disabled={!!editing}
            placeholder="my-hospital"
          />
        </Field>
        <Field label="Tier">
          <Select value={form.tier} onChange={(e) => set("tier", e.target.value)}>
            <option value="hospital">Hospital</option>
            <option value="clinic">Clinic</option>
          </Select>
        </Field>
        <Field label="Phone">
          <Input value={form.phone} onChange={(e) => set("phone", e.target.value)} />
        </Field>
        <Field label="Plivo Number">
          <Input value={form.plivo_number} onChange={(e) => set("plivo_number", e.target.value)} />
        </Field>
        <Field label="Agent Name">
          <Input value={form.agent_name} onChange={(e) => set("agent_name", e.target.value)} />
        </Field>
        <Field label="Contact Person">
          <Input value={form.contact_person} onChange={(e) => set("contact_person", e.target.value)} />
        </Field>
        <Field label="Contact Phone">
          <Input value={form.contact_phone} onChange={(e) => set("contact_phone", e.target.value)} />
        </Field>
        <div className="col-span-2">
          <Field label="DB URL">
            <Input value={form.db_url} onChange={(e) => set("db_url", e.target.value)} placeholder="postgresql://..." />
          </Field>
        </div>
        <div className="col-span-2">
          <Field label="Address">
            <Input value={form.address} onChange={(e) => set("address", e.target.value)} />
          </Field>
        </div>
        <div className="col-span-2">
          <Field label="Notes">
            <Input value={form.notes} onChange={(e) => set("notes", e.target.value)} />
          </Field>
        </div>
      </div>
    </FormModal>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function TenantsPage() {
  const toast = useToast();
  const qc = useQueryClient();
  const [editing, setEditing] = React.useState<Tenant | null>(null);
  const [formOpen, setFormOpen] = React.useState(false);
  const [featTenant, setFeatTenant] = React.useState<Tenant | null>(null);

  const { data = [], isLoading } = useQuery({
    queryKey: ["tenants"],
    queryFn: api.listTenants,
  });

  const deleteMutation = useMutation({
    mutationFn: (slug: string) => api.deleteTenant(slug),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tenants"] });
      toast("Tenant deleted");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  function handleDelete(t: Tenant) {
    if (!window.confirm(`Delete tenant "${t.name}"? This cannot be undone.`)) return;
    deleteMutation.mutate(t.slug);
  }

  const columns: ColumnDef<Tenant, unknown>[] = [
    { header: "Name", accessorKey: "name" },
    {
      header: "Slug",
      accessorKey: "slug",
      cell: ({ row }) => (
        <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs">{row.original.slug}</code>
      ),
    },
    {
      header: "Tier",
      accessorKey: "tier",
      cell: ({ row }) => (
        <Badge tone={row.original.tier === "hospital" ? "blue" : "gray"}>{row.original.tier ?? "—"}</Badge>
      ),
    },
    {
      header: "Phone",
      accessorKey: "phone",
      cell: ({ row }) => row.original.phone ?? <span className="text-gray-400">—</span>,
    },
    {
      header: "Plivo Number",
      accessorKey: "plivo_number",
      cell: ({ row }) =>
        row.original.plivo_number ? (
          <code className="rounded bg-gray-100 px-1 text-xs">{row.original.plivo_number}</code>
        ) : (
          <span className="text-gray-400">—</span>
        ),
    },
    {
      header: "Actions",
      id: "actions",
      cell: ({ row }) => {
        const t = row.original;
        return (
          <div className="flex gap-1.5">
            <Button
              variant="outline"
              className="px-2 py-1 text-xs"
              onClick={() => { setEditing(t); setFormOpen(true); }}
            >
              Edit
            </Button>
            <Button
              variant="outline"
              className="px-2 py-1 text-xs"
              onClick={() => setFeatTenant(t)}
            >
              Features
            </Button>
            <Button
              variant="danger"
              className="px-2 py-1 text-xs"
              onClick={() => handleDelete(t)}
            >
              Delete
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <div>
      <PageHeader
        title="Tenants"
        action={
          <Button onClick={() => { setEditing(null); setFormOpen(true); }}>
            + New Tenant
          </Button>
        }
      />
      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>
      ) : (
        <DataTable
          columns={columns}
          data={data}
          searchPlaceholder="Search tenants…"
          emptyTitle="No tenants yet"
        />
      )}

      <TenantFormModal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        editing={editing}
      />

      <FeaturesModal
        open={!!featTenant}
        onClose={() => setFeatTenant(null)}
        tenant={featTenant}
      />
    </div>
  );
}
