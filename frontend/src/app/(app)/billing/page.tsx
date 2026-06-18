"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { BillingItem } from "@/lib/types";
import { RequireHospital } from "@/components/require-hospital";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { FormModal } from "@/components/modal";
import { PageHeader, Button, Field, Input, Textarea, Badge } from "@/components/ui";
import { useToast } from "@/components/providers";

type BillingForm = {
  item: string;
  item_ml: string;
  price_min: string;
  price_max: string;
  notes: string;
};

const BLANK: BillingForm = { item: "", item_ml: "", price_min: "", price_max: "", notes: "" };

function Inner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();

  const { data = [], isLoading } = useQuery({
    queryKey: ["billing", hospitalId],
    queryFn: () => api.listBilling(hospitalId),
  });

  const [open, setOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<BillingItem | null>(null);
  const [form, setForm] = React.useState<BillingForm>(BLANK);

  function openCreate() {
    setEditing(null);
    setForm(BLANK);
    setOpen(true);
  }

  function openEdit(b: BillingItem) {
    setEditing(b);
    setForm({
      item: b.item ?? "",
      item_ml: b.item_ml ?? "",
      price_min: b.price_min != null ? String(b.price_min) : "",
      price_max: b.price_max != null ? String(b.price_max) : "",
      notes: b.notes ?? "",
    });
    setOpen(true);
  }

  const set = (k: keyof BillingForm) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((f) => ({ ...f, [k]: e.target.value }));

  function formToPayload() {
    return {
      item: form.item,
      item_ml: form.item_ml || null,
      price_min: form.price_min !== "" ? parseFloat(form.price_min) : null,
      price_max: form.price_max !== "" ? parseFloat(form.price_max) : null,
      notes: form.notes || null,
    };
  }

  const saveMut = useMutation({
    mutationFn: () =>
      editing
        ? api.updateBilling(hospitalId, editing.id, formToPayload())
        : api.createBilling(hospitalId, formToPayload()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["billing", hospitalId] });
      toast(editing ? "Billing item updated" : "Billing item created", "ok");
      setOpen(false);
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteBilling(hospitalId, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["billing", hospitalId] });
      toast("Billing item deleted", "ok");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  function fmtPrice(min?: number | null, max?: number | null): string {
    if (min == null && max == null) return "—";
    if (min == null) return `up to ₹${max}`;
    if (max == null) return `from ₹${min}`;
    return `₹${min} – ₹${max}`;
  }

  const columns: ColumnDef<BillingItem>[] = [
    {
      header: "Item",
      accessorKey: "item",
      cell: ({ row }) => (
        <span className="font-medium text-gray-900">{row.original.item}</span>
      ),
    },
    { header: "Item (ML)", accessorKey: "item_ml" },
    {
      header: "Price Range",
      id: "price",
      cell: ({ row }) => fmtPrice(row.original.price_min, row.original.price_max),
    },
    {
      header: "Notes",
      accessorKey: "notes",
      cell: ({ row }) => (
        <span className="text-gray-500 line-clamp-1 max-w-xs">{row.original.notes ?? "—"}</span>
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
              if (confirm(`Delete "${row.original.item}"?`)) deleteMut.mutate(row.original.id);
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
        title="Billing"
        action={<Button onClick={openCreate}>+ Add Item</Button>}
      />
      <DataTable
        columns={columns}
        data={data}
        searchPlaceholder="Search billing items…"
        emptyTitle="No billing items yet"
      />
      <FormModal
        open={open}
        onClose={() => setOpen(false)}
        title={editing ? "Edit Billing Item" : "New Billing Item"}
        onSubmit={(e) => { e.preventDefault(); saveMut.mutate(); }}
        saving={saveMut.isPending}
      >
        <Field label="Item *">
          <Input value={form.item} onChange={set("item")} required placeholder="e.g. Consultation" />
        </Field>
        <Field label="Item (Malayalam)">
          <Input value={form.item_ml} onChange={set("item_ml")} />
        </Field>
        <Field label="Min Price (₹)">
          <Input type="number" value={form.price_min} onChange={set("price_min")} min={0} step="0.01" />
        </Field>
        <Field label="Max Price (₹)">
          <Input type="number" value={form.price_max} onChange={set("price_max")} min={0} step="0.01" />
        </Field>
        <Field label="Notes">
          <Textarea value={form.notes} onChange={set("notes")} rows={3} />
        </Field>
      </FormModal>
    </div>
  );
}

export default function BillingPage() {
  return (
    <RequireHospital>
      {(hid) => <Inner hospitalId={hid} />}
    </RequireHospital>
  );
}
