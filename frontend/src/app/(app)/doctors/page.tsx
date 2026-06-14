"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Doctor, Department, Schedule } from "@/lib/types";
import { RequireHospital } from "@/components/require-hospital";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { FormModal } from "@/components/modal";
import {
  PageHeader, Button, Field, Input, Select, Badge, Card, CardHeader, CardBody,
} from "@/components/ui";
import { useToast } from "@/components/providers";
import { DOW_LABELS, dowLabel } from "@/lib/utils";

// ── Doctor form ──────────────────────────────────────────────────────────────

type DoctorForm = {
  name: string;
  name_ml: string;
  specialty: string;
  qualifications: string;
  dept_id: string;
};

const BLANK_DOC: DoctorForm = {
  name: "", name_ml: "", specialty: "", qualifications: "", dept_id: "",
};

// ── Schedule form ────────────────────────────────────────────────────────────

type SchedForm = {
  day_of_week: number;
  start_time: string;
  end_time: string;
  room: string;
};

const BLANK_SCHED: SchedForm = { day_of_week: 1, start_time: "09:00", end_time: "17:00", room: "" };

// ── Schedule Panel ───────────────────────────────────────────────────────────

function SchedulePanel({
  doctor,
  onClose,
}: {
  doctor: Doctor;
  onClose: () => void;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const [addOpen, setAddOpen] = React.useState(false);
  const [form, setForm] = React.useState<SchedForm>(BLANK_SCHED);

  const schedules: Schedule[] = doctor.schedules ?? [];

  // Group by day_of_week
  const grouped: Record<number, Schedule[]> = {};
  for (const s of schedules) {
    if (!grouped[s.day_of_week]) grouped[s.day_of_week] = [];
    grouped[s.day_of_week].push(s);
  }
  const sortedDays = Object.keys(grouped)
    .map(Number)
    .sort((a, b) => a - b);

  const addMut = useMutation({
    mutationFn: () =>
      api.addSchedule(doctor.id, {
        day_of_week: form.day_of_week,
        start_time: form.start_time,
        end_time: form.end_time,
        room: form.room || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["doctors"] });
      toast("Schedule added", "ok");
      setAddOpen(false);
      setForm(BLANK_SCHED);
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteSchedule(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["doctors"] });
      toast("Schedule removed", "ok");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:p-8">
      <div className="w-full max-w-xl rounded-xl bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <h2 className="font-semibold text-gray-900">
            Schedules — {doctor.name}
          </h2>
          <button onClick={onClose} className="rounded p-1 text-gray-400 hover:bg-gray-100">
            ✕
          </button>
        </div>
        <div className="px-5 py-4 space-y-4">
          {sortedDays.length === 0 ? (
            <p className="text-sm text-gray-400">No schedules yet.</p>
          ) : (
            sortedDays.map((d) => (
              <div key={d}>
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
                  {dowLabel(d)}
                </p>
                <div className="space-y-1">
                  {grouped[d].map((s) => (
                    <div
                      key={s.id}
                      className="flex items-center justify-between rounded border border-gray-100 bg-gray-50 px-3 py-1.5 text-sm"
                    >
                      <span>
                        {s.start_time} – {s.end_time}
                        {s.room ? <span className="ml-2 text-gray-400">Room {s.room}</span> : null}
                      </span>
                      <Button
                        variant="danger"
                        className="text-xs"
                        onClick={() => {
                          if (confirm("Remove this schedule?")) deleteMut.mutate(s.id);
                        }}
                      >
                        Remove
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
          <Button variant="outline" onClick={() => setAddOpen(true)} className="w-full">
            + Add Slot
          </Button>
        </div>
        <FormModal
          open={addOpen}
          onClose={() => setAddOpen(false)}
          title="Add Schedule Slot"
          onSubmit={(e) => { e.preventDefault(); addMut.mutate(); }}
          saving={addMut.isPending}
          submitLabel="Add Slot"
        >
          <Field label="Day of Week">
            <Select
              value={String(form.day_of_week)}
              onChange={(e) => setForm((f) => ({ ...f, day_of_week: Number(e.target.value) }))}
            >
              {DOW_LABELS.map((label, idx) => (
                <option key={idx} value={idx}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Start Time">
            <Input
              type="time"
              value={form.start_time}
              onChange={(e) => setForm((f) => ({ ...f, start_time: e.target.value }))}
              required
            />
          </Field>
          <Field label="End Time">
            <Input
              type="time"
              value={form.end_time}
              onChange={(e) => setForm((f) => ({ ...f, end_time: e.target.value }))}
              required
            />
          </Field>
          <Field label="Room">
            <Input
              value={form.room}
              onChange={(e) => setForm((f) => ({ ...f, room: e.target.value }))}
              placeholder="e.g. 201"
            />
          </Field>
        </FormModal>
      </div>
    </div>
  );
}

// ── Main Inner component ──────────────────────────────────────────────────────

function Inner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();

  const { data: doctors = [], isLoading } = useQuery({
    queryKey: ["doctors", hospitalId],
    queryFn: () => api.listDoctors(hospitalId),
  });

  const { data: departments = [] } = useQuery({
    queryKey: ["depts", hospitalId],
    queryFn: () => api.listDepartments(hospitalId),
  });

  const deptMap = React.useMemo(() => {
    const m: Record<string, Department> = {};
    for (const d of departments) m[d.id] = d;
    return m;
  }, [departments]);

  const [open, setOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<Doctor | null>(null);
  const [form, setForm] = React.useState<DoctorForm>(BLANK_DOC);
  const [schedDoctor, setSchedDoctor] = React.useState<Doctor | null>(null);

  function openCreate() {
    setEditing(null);
    setForm(BLANK_DOC);
    setOpen(true);
  }

  function openEdit(d: Doctor) {
    setEditing(d);
    setForm({
      name: d.name ?? "",
      name_ml: d.name_ml ?? "",
      specialty: d.specialty ?? "",
      qualifications: d.qualifications ?? "",
      dept_id: d.dept_id ?? "",
    });
    setOpen(true);
  }

  const set = (k: keyof DoctorForm) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setForm((f) => ({ ...f, [k]: e.target.value }));

  const saveMut = useMutation({
    mutationFn: () =>
      editing
        ? api.updateDoctor(hospitalId, editing.id, {
            ...form,
            dept_id: form.dept_id || null,
          })
        : api.createDoctor(hospitalId, {
            ...form,
            dept_id: form.dept_id || null,
          }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["doctors", hospitalId] });
      toast(editing ? "Doctor updated" : "Doctor created", "ok");
      setOpen(false);
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteDoctor(hospitalId, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["doctors", hospitalId] });
      toast("Doctor deleted", "ok");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const columns: ColumnDef<Doctor>[] = [
    {
      header: "Name",
      accessorKey: "name",
      cell: ({ row }) => (
        <span className="font-medium text-gray-900">{row.original.name}</span>
      ),
    },
    { header: "Specialty", accessorKey: "specialty" },
    {
      header: "Department",
      accessorKey: "dept_id",
      cell: ({ row }) =>
        row.original.dept_id ? deptMap[row.original.dept_id]?.name ?? row.original.dept_id : "—",
    },
    {
      header: "Schedules",
      id: "schedules",
      cell: ({ row }) => {
        const count = row.original.schedules?.length ?? 0;
        return (
          <Badge tone={count > 0 ? "blue" : "gray"}>
            {count} slot{count !== 1 ? "s" : ""}
          </Badge>
        );
      },
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
          <Button variant="outline" onClick={() => setSchedDoctor(row.original)} className="text-xs">
            Schedules
          </Button>
          <Button
            variant="danger"
            className="text-xs"
            onClick={() => {
              if (confirm(`Delete Dr. ${row.original.name}?`)) {
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
        title="Doctors"
        action={<Button onClick={openCreate}>+ Add Doctor</Button>}
      />
      <DataTable
        columns={columns}
        data={doctors}
        searchPlaceholder="Search doctors…"
        emptyTitle="No doctors yet"
      />

      {/* Doctor create/edit modal */}
      <FormModal
        open={open}
        onClose={() => setOpen(false)}
        title={editing ? "Edit Doctor" : "New Doctor"}
        onSubmit={(e) => { e.preventDefault(); saveMut.mutate(); }}
        saving={saveMut.isPending}
      >
        <Field label="Name *">
          <Input value={form.name} onChange={set("name")} required />
        </Field>
        <Field label="Name (Malayalam)">
          <Input value={form.name_ml} onChange={set("name_ml")} />
        </Field>
        <Field label="Specialty">
          <Input value={form.specialty} onChange={set("specialty")} placeholder="e.g. Cardiology" />
        </Field>
        <Field label="Qualifications">
          <Input value={form.qualifications} onChange={set("qualifications")} placeholder="e.g. MBBS, MD" />
        </Field>
        <Field label="Department">
          <Select value={form.dept_id} onChange={set("dept_id")}>
            <option value="">— None —</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </Select>
        </Field>
      </FormModal>

      {/* Schedule panel */}
      {schedDoctor && (
        <SchedulePanel
          doctor={schedDoctor}
          onClose={() => setSchedDoctor(null)}
        />
      )}
    </div>
  );
}

export default function DoctorsPage() {
  return (
    <RequireHospital>
      {(hid) => <Inner hospitalId={hid} />}
    </RequireHospital>
  );
}
