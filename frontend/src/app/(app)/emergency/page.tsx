"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { EmergencyContact } from "@/lib/types";
import { RequireHospital } from "@/components/require-hospital";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { FormModal } from "@/components/modal";
import { PageHeader, Button, Field, Input, Badge } from "@/components/ui";
import { useToast } from "@/components/providers";

type EmergencyForm = {
  label: string;
  label_ml: string;
  phone: string;
  priority: string;
};

const BLANK: EmergencyForm = { label: "", label_ml: "", phone: "", priority: "0" };

function Inner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();

  const { data = [], isLoading } = useQuery({
    queryKey: ["emergency", hospitalId],
    queryFn: () => api.listEmergency(hospitalId),
  });

  // Sort by priority descending
  const sorted = React.useMemo(
    () => [...data].sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0)),
    [data],
  );

  const [open, setOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<EmergencyContact | null>(null);
  const [form, setForm] = React.useState<EmergencyForm>(BLANK);

  function openCreate() {
    setEditing(null);
    setForm(BLANK);
    setOpen(true);
  }

  function openEdit(c: EmergencyContact) {
    setEditing(c);
    setForm({
      label: c.label ?? "",
      label_ml: c.label_ml ?? "",
      phone: c.phone ?? "",
      priority: String(c.priority ?? 0),
    });
    setOpen(true);
  }

  const set = (k: keyof EmergencyForm) =>
    (e: React.ChangeEvent<HTMLInputElement>) =>
      setForm((f) => ({ ...f, [k]: e.target.value }));

  function formToPayload() {
    return {
      label: form.label,
      label_ml: form.label_ml || null,
      phone: form.phone,
      priority: parseInt(form.priority, 10) || 0,
    };
  }

  const saveMut = useMutation({
    mutationFn: () =>
      editing
        ? api.updateEmergency(hospitalId, editing.id, formToPayload())
        : api.createEmergency(hospitalId, formToPayload()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["emergency", hospitalId] });
      toast(editing ? "Contact updated" : "Contact created", "ok");
      setOpen(false);
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteEmergency(hospitalId, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["emergency", hospitalId] });
      toast("Contact deleted", "ok");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const columns: ColumnDef<EmergencyContact>[] = [
    {
      header: "Priority",
      accessorKey: "priority",
      cell: ({ row }) => (
        <Badge tone="red">{row.original.priority}</Badge>
      ),
    },
    {
      header: "Label",
      accessorKey: "label",
      cell: ({ row }) => (
        <span className="font-medium text-gray-900">{row.original.label}</span>
      ),
    },
    { header: "Label (ML)", accessorKey: "label_ml" },
    {
      header: "Phone",
      accessorKey: "phone",
      cell: ({ row }) => (
        <a
          href={`tel:${row.original.phone}`}
          className="text-blue-600 hover:underline"
        >
          {row.original.phone}
        </a>
      ),
    },
    {
      header: "Status",
      accessorKey: "active",
      cell: ({ row }) => (
        <Badge tone={row.original.active ? "green" : "gray"}>
          {row.original.active ? "Active" : "Inactive"}
        </Badge>
      ),
    },
    {
      header: "Actions",
      id: "actions",
      cell: ({ row }) => (
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={() => openEdit(row.original)} className="text-xs">
            Edit
          </Button>
          <Button
            variant="danger"
            className="text-xs"
            onClick={() => {
              if (confirm(`Delete "${row.original.label}"?`)) deleteMut.mutate(row.original.id);
            }}
          >
            Delete
          </Button>
        </div>
      ),
    },
  ];

  if (isLoading) {
    return <div className="py-8 text-center text-sm text-gray-400">Loading…</div>;
  }

  return (
    <div>
      <PageHeader
        title="Emergency Contacts"
        action={<Button onClick={openCreate}>+ Add Contact</Button>}
      />
      <DataTable
        columns={columns}
        data={sorted}
        searchPlaceholder="Search contacts…"
        emptyTitle="No emergency contacts yet"
      />
      <FormModal
        open={open}
        onClose={() => setOpen(false)}
        title={editing ? "Edit Emergency Contact" : "New Emergency Contact"}
        onSubmit={(e) => { e.preventDefault(); saveMut.mutate(); }}
        saving={saveMut.isPending}
      >
        <Field label="Label *">
          <Input value={form.label} onChange={set("label")} required placeholder="e.g. Ambulance" />
        </Field>
        <Field label="Label (Malayalam)">
          <Input value={form.label_ml} onChange={set("label_ml")} />
        </Field>
        <Field label="Phone *">
          <Input value={form.phone} onChange={set("phone")} required type="tel" placeholder="e.g. 108" />
        </Field>
        <Field label="Priority">
          <Input
            type="number"
            value={form.priority}
            onChange={set("priority")}
            min={0}
            placeholder="Higher = shown first"
          />
        </Field>
      </FormModal>
    </div>
  );
}

export default function EmergencyPage() {
  return (
    <RequireHospital>
      {(hid) => <Inner hospitalId={hid} />}
    </RequireHospital>
  );
}
