"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Appointment, AppointmentStatus } from "@/lib/types";
import { fmtDateTime } from "@/lib/utils";
import {
  PageHeader, Badge, Button, Select, Label, Spinner, EmptyState,
} from "@/components/ui";
import { DataTable, ColumnDef } from "@/components/data-table";
import { RequireHospital } from "@/components/require-hospital";
import { useToast } from "@/components/providers";

const ALL_STATUSES: AppointmentStatus[] = [
  "pending", "booked", "confirmed", "cancelled", "rescheduled", "requested",
];

function statusTone(s: AppointmentStatus): "green" | "red" | "yellow" | "gray" {
  if (s === "confirmed") return "green";
  if (s === "cancelled") return "red";
  if (s === "pending" || s === "requested") return "yellow";
  return "gray";
}

function AppointmentsInner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = React.useState<AppointmentStatus | "">("");

  const { data = [], isLoading } = useQuery({
    queryKey: ["appointments", hospitalId, statusFilter],
    queryFn: () => api.listAppointments(hospitalId, statusFilter || undefined, 200),
  });

  const updateMutation = useMutation({
    mutationFn: ({ apptId, status }: { apptId: string; status: AppointmentStatus }) =>
      api.updateAppointmentStatus(hospitalId, apptId, status),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["appointments", hospitalId] });
      toast("Appointment status updated");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const columns: ColumnDef<Appointment, unknown>[] = [
    {
      header: "Patient",
      accessorKey: "patient_name",
      cell: ({ row }) => row.original.patient_name ?? <span className="text-gray-400">—</span>,
    },
    {
      header: "Phone",
      accessorKey: "patient_phone",
      cell: ({ row }) => row.original.patient_phone ?? <span className="text-gray-400">—</span>,
    },
    {
      header: "Slot",
      accessorKey: "slot_time",
      cell: ({ row }) =>
        row.original.slot_time ? fmtDateTime(row.original.slot_time) : <span className="text-gray-400">—</span>,
    },
    {
      header: "Status",
      accessorKey: "status",
      cell: ({ row }) => {
        const appt = row.original;
        return (
          <div className="flex flex-wrap items-center gap-1">
            <Badge tone={statusTone(appt.status)}>{appt.status}</Badge>
            {appt.reminder_sent && (
              <Badge tone="blue" className="text-xs">reminder</Badge>
            )}
            {appt.confirmation_sent && (
              <Badge tone="green" className="text-xs">confirmed</Badge>
            )}
          </div>
        );
      },
    },
    {
      header: "Actions",
      id: "actions",
      cell: ({ row }) => {
        const appt = row.original;
        const busy = updateMutation.isPending;
        return (
          <div className="flex gap-1.5">
            {appt.status !== "confirmed" && (
              <Button
                variant="outline"
                className="px-2 py-1 text-xs"
                disabled={busy}
                onClick={() => updateMutation.mutate({ apptId: appt.id, status: "confirmed" })}
              >
                Confirm
              </Button>
            )}
            {appt.status !== "cancelled" && (
              <Button
                variant="danger"
                className="px-2 py-1 text-xs"
                disabled={busy}
                onClick={() => updateMutation.mutate({ apptId: appt.id, status: "cancelled" })}
              >
                Cancel
              </Button>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <div>
      <PageHeader title="Appointments" />
      <div className="mb-4 flex items-center gap-2">
        <Label className="text-sm">Status</Label>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as AppointmentStatus | "")}
          className="w-44"
        >
          <option value="">All</option>
          {ALL_STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </Select>
      </div>
      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>
      ) : (
        <DataTable
          columns={columns}
          data={data}
          searchPlaceholder="Search patient…"
          emptyTitle="No appointments found"
        />
      )}
    </div>
  );
}

export default function AppointmentsPage() {
  return (
    <RequireHospital>
      {(hospitalId) => <AppointmentsInner hospitalId={hospitalId} />}
    </RequireHospital>
  );
}
