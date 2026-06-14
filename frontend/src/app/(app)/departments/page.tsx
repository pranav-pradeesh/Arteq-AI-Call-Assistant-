"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Department } from "@/lib/types";
import { RequireHospital } from "@/components/require-hospital";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { FormModal } from "@/components/modal";
import { PageHeader, Button, Field, Input, Badge } from "@/components/ui";
import { useToast } from "@/components/providers";

type DeptForm = {
  name: string;
  name_ml: string;
  floor: string;
  location_hint: string;
  phone_ext: string;
};

const BLANK: DeptForm = { name: "", name_ml: "", floor: "", location_hint: "", phone_ext: "" };

function Inner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();

  const { data = [], isLoading } = useQuery({
    queryKey: ["depts", hospitalId],
    queryFn: () => api.listDepartments(hospitalId),
  });

  const [open, setOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<Department | null>(null);
  const [form, setForm] = React.useState<DeptForm>(BLANK);

  function openCreate() {
    setEditing(null);
    setForm(BLANK);
    setOpen(true);
  }

  function openEdit(d: Department) {
    setEditing(d);
    setForm({
      name: d.name ?? "",
      name_ml: d.name_ml ?? "",
      floor: d.floor ?? "",
      location_hint: d.location_hint ?? "",
      phone_ext: d.phone_ext ?? "",
    });
    setOpen(true);
  }

  const set = (k: keyof DeptForm) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const saveMut = useMutation({
    mutationFn: () =>
      editing
        ? api.updateDepartment(hospitalId, editing.id, form)
        : api.createDepartment(hospitalId, form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["depts", hospitalId] });
      toast(editing ? "Department updated" : "Department created", "ok");
      setOpen(false);
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteDepartment(hospitalId, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["depts", hospitalId] });
      toast("Department deleted", "ok");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const columns: ColumnDef<Department>[] = [
    {
      header: "Name",
      accessorKey: "name",
      cell: ({ row }) => (
        <span className="font-medium text-gray-900">{row.original.name}</span>
      ),
    },
    { header: "Name (ML)", accessorKey: "name_ml" },
    { header: "Floor", accessorKey: "floor" },
    { header: "Location Hint", accessorKey: "location_hint" },
    { header: "Phone Ext", accessorKey: "phone_ext" },
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
              if (confirm(`Delete "${row.original.name}"?`)) {
                deleteMut.mutate(row.original.id);
              }
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
        title="Departments"
        action={<Button onClick={openCreate}>+ Add Department</Button>}
      />
      <DataTable
        columns={columns}
        data={data}
        searchPlaceholder="Search departments…"
        emptyTitle="No departments yet"
      />
      <FormModal
        open={open}
        onClose={() => setOpen(false)}
        title={editing ? "Edit Department" : "New Department"}
        onSubmit={(e) => { e.preventDefault(); saveMut.mutate(); }}
        saving={saveMut.isPending}
      >
        <Field label="Name *">
          <Input value={form.name} onChange={set("name")} required />
        </Field>
        <Field label="Name (Malayalam)">
          <Input value={form.name_ml} onChange={set("name_ml")} />
        </Field>
        <Field label="Floor">
          <Input value={form.floor} onChange={set("floor")} placeholder="e.g. 2nd Floor" />
        </Field>
        <Field label="Location Hint">
          <Input value={form.location_hint} onChange={set("location_hint")} placeholder="e.g. Near main entrance" />
        </Field>
        <Field label="Phone Extension">
          <Input value={form.phone_ext} onChange={set("phone_ext")} placeholder="e.g. 201" />
        </Field>
      </FormModal>
    </div>
  );
}

export default function DepartmentsPage() {
  return (
    <RequireHospital>
      {(hid) => <Inner hospitalId={hid} />}
    </RequireHospital>
  );
}
