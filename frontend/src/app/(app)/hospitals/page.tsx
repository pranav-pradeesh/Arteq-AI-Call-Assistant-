"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Hospital } from "@/lib/types";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { FormModal } from "@/components/modal";
import {
  PageHeader, Button, Field, Input, Select, Badge,
} from "@/components/ui";
import { useToast, useCurrentHospital } from "@/components/providers";

type HospitalForm = {
  name: string;
  name_ml: string;
  phone: string;
  address: string;
  slug: string;
  tier: "hospital" | "clinic";
  agent_name: string;
};

const BLANK: HospitalForm = {
  name: "", name_ml: "", phone: "", address: "", slug: "", tier: "hospital", agent_name: "",
};

export default function HospitalsPage() {
  const toast = useToast();
  const qc = useQueryClient();
  const { setHospitalId, hospitalId } = useCurrentHospital();

  const { data = [], isLoading } = useQuery({
    queryKey: ["hospitals"],
    queryFn: () => api.listHospitals(),
  });

  const [open, setOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<Hospital | null>(null);
  const [form, setForm] = React.useState<HospitalForm>(BLANK);

  function openCreate() {
    setEditing(null);
    setForm(BLANK);
    setOpen(true);
  }

  function openEdit(h: Hospital) {
    setEditing(h);
    setForm({
      name: h.name ?? "",
      name_ml: h.name_ml ?? "",
      phone: h.phone ?? "",
      address: h.address ?? "",
      slug: h.slug ?? "",
      tier: h.tier ?? "hospital",
      agent_name: h.agent_name ?? "",
    });
    setOpen(true);
  }

  const set = (k: keyof HospitalForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const saveMut = useMutation({
    mutationFn: () =>
      editing
        ? api.updateHospital(editing.id, form)
        : api.createHospital(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hospitals"] });
      toast(editing ? "Hospital updated" : "Hospital created", "ok");
      setOpen(false);
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const columns: ColumnDef<Hospital>[] = [
    {
      header: "Name",
      accessorKey: "name",
      cell: ({ row }) => (
        <span className="font-medium text-gray-900">{row.original.name}</span>
      ),
    },
    { header: "Slug", accessorKey: "slug" },
    { header: "Phone", accessorKey: "phone" },
    {
      header: "Tier",
      accessorKey: "tier",
      cell: ({ row }) => (
        <Badge tone={row.original.tier === "clinic" ? "blue" : "green"}>
          {row.original.tier ?? "hospital"}
        </Badge>
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
      cell: ({ row }) => {
        const h = row.original;
        const isActive = hospitalId === h.id;
        return (
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={() => openEdit(h)} className="text-xs">
              Edit
            </Button>
            <Button
              variant={isActive ? "primary" : "outline"}
              onClick={() => setHospitalId(h.id)}
              className="text-xs"
            >
              {isActive ? "Selected" : "Select"}
            </Button>
          </div>
        );
      },
    },
  ];

  if (isLoading) {
    return <div className="p-8 text-center text-sm text-gray-400">Loading hospitals…</div>;
  }

  return (
    <div>
      <PageHeader
        title="Hospitals"
        action={<Button onClick={openCreate}>+ Add Hospital</Button>}
      />
      <DataTable
        columns={columns}
        data={data}
        searchPlaceholder="Search hospitals…"
        emptyTitle="No hospitals yet"
      />
      <FormModal
        open={open}
        onClose={() => setOpen(false)}
        title={editing ? "Edit Hospital" : "New Hospital"}
        onSubmit={(e) => { e.preventDefault(); saveMut.mutate(); }}
        saving={saveMut.isPending}
      >
        <Field label="Name *">
          <Input value={form.name} onChange={set("name")} required />
        </Field>
        <Field label="Name (Malayalam)">
          <Input value={form.name_ml} onChange={set("name_ml")} />
        </Field>
        <Field label="Phone">
          <Input value={form.phone} onChange={set("phone")} type="tel" />
        </Field>
        <Field label="Address">
          <Input value={form.address} onChange={set("address")} />
        </Field>
        <Field label="Slug">
          <Input value={form.slug} onChange={set("slug")} placeholder="e.g. my-hospital" />
        </Field>
        <Field label="Tier">
          <Select value={form.tier} onChange={set("tier")}>
            <option value="hospital">Hospital</option>
            <option value="clinic">Clinic</option>
          </Select>
        </Field>
        <Field label="Agent Name">
          <Input value={form.agent_name} onChange={set("agent_name")} placeholder="e.g. Arteq" />
        </Field>
      </FormModal>
    </div>
  );
}
