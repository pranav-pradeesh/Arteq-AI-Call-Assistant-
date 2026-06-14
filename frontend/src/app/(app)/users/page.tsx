"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { User, Role } from "@/lib/types";
import { PageHeader, Button, Badge, Field, Input, Select, Spinner, EmptyState } from "@/components/ui";
import { DataTable, ColumnDef } from "@/components/data-table";
import { FormModal } from "@/components/modal";
import { useToast } from "@/components/providers";

const ROLES: Role[] = ["super_admin", "tenant_admin", "viewer"];

type UserForm = {
  email: string;
  role: Role;
  password: string;
  active: boolean;
};

const EMPTY_FORM: UserForm = { email: "", role: "viewer", password: "", active: true };

function userToForm(u: User): UserForm {
  return { email: u.email, role: u.role, password: "", active: u.active };
}

function roleTone(r: Role): "red" | "blue" | "gray" {
  if (r === "super_admin") return "red";
  if (r === "tenant_admin") return "blue";
  return "gray";
}

function UserFormModal({
  open,
  onClose,
  editing,
}: {
  open: boolean;
  onClose: () => void;
  editing: User | null;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const [form, setForm] = React.useState<UserForm>(EMPTY_FORM);

  React.useEffect(() => {
    if (editing) setForm(userToForm(editing));
    else setForm(EMPTY_FORM);
  }, [editing, open]);

  function set<K extends keyof UserForm>(key: K, value: UserForm[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      if (editing) {
        const payload: Partial<User> = { email: form.email, role: form.role, active: form.active };
        return api.updateUser(editing.id, payload);
      }
      return api.createUser({ email: form.email, role: form.role, active: form.active, password: form.password });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      toast(editing ? "User updated" : "User created");
      onClose();
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  return (
    <FormModal
      open={open}
      onClose={onClose}
      title={editing ? `Edit — ${editing.email}` : "New User"}
      onSubmit={(e) => { e.preventDefault(); saveMutation.mutate(); }}
      saving={saveMutation.isPending}
    >
      <Field label="Email *">
        <Input
          required
          type="email"
          value={form.email}
          onChange={(e) => set("email", e.target.value)}
        />
      </Field>
      <Field label="Role">
        <Select value={form.role} onChange={(e) => set("role", e.target.value as Role)}>
          {ROLES.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </Select>
      </Field>
      {!editing && (
        <Field label="Password *">
          <Input
            required
            type="password"
            value={form.password}
            onChange={(e) => set("password", e.target.value)}
            autoComplete="new-password"
          />
        </Field>
      )}
      <label className="flex cursor-pointer items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.active}
          onChange={(e) => set("active", e.target.checked)}
          className="h-4 w-4 rounded border-gray-300"
        />
        Active
      </label>
    </FormModal>
  );
}

export default function UsersPage() {
  const toast = useToast();
  const qc = useQueryClient();
  const [editing, setEditing] = React.useState<User | null>(null);
  const [formOpen, setFormOpen] = React.useState(false);

  const { data = [], isLoading, isError } = useQuery({
    queryKey: ["users"],
    queryFn: api.listUsers,
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteUser(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      toast("User deleted");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  function handleDelete(u: User) {
    if (!window.confirm(`Delete user "${u.email}"? This cannot be undone.`)) return;
    deleteMutation.mutate(u.id);
  }

  const columns: ColumnDef<User, unknown>[] = [
    { header: "Email", accessorKey: "email" },
    {
      header: "Role",
      accessorKey: "role",
      cell: ({ row }) => (
        <Badge tone={roleTone(row.original.role)}>{row.original.role}</Badge>
      ),
    },
    {
      header: "Active",
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
        const u = row.original;
        return (
          <div className="flex gap-1.5">
            <Button
              variant="outline"
              className="px-2 py-1 text-xs"
              onClick={() => { setEditing(u); setFormOpen(true); }}
            >
              Edit
            </Button>
            <Button
              variant="danger"
              className="px-2 py-1 text-xs"
              onClick={() => handleDelete(u)}
            >
              Delete
            </Button>
          </div>
        );
      },
    },
  ];

  if (isError) {
    return (
      <div>
        <PageHeader title="Users" />
        <EmptyState
          title="User management requires the RBAC backend (planned)"
          hint="The /users endpoint has not been implemented on the backend yet. This page will become available once the RBAC system is deployed."
        />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Users"
        action={
          <Button onClick={() => { setEditing(null); setFormOpen(true); }}>
            + New User
          </Button>
        }
      />
      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>
      ) : (
        <DataTable
          columns={columns}
          data={data}
          searchPlaceholder="Search users…"
          emptyTitle="No users yet"
        />
      )}

      <UserFormModal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        editing={editing}
      />
    </div>
  );
}
