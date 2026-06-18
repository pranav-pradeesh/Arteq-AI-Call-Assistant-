"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Appointment, AppointmentStatus, AppointmentWorkflowStatus } from "@/lib/types";
import { fmtDateTime } from "@/lib/utils";
import {
  PageHeader, Badge, Button, Select, Label, Spinner, EmptyState,
} from "@/components/ui";
import { Modal } from "@/components/modal";
import { DataTable, ColumnDef } from "@/components/data-table";
import { RequireHospital } from "@/components/require-hospital";
import { useToast } from "@/components/providers";

const EM_DASH = String.fromCharCode(8212);
const ELLIPSIS = String.fromCharCode(8230);

const ALL_STATUSES: AppointmentStatus[] = [
  "pending", "booked", "confirmed", "cancelled", "rescheduled", "requested",
];

function statusTone(s: AppointmentStatus): "green" | "red" | "yellow" | "gray" {
  if (s === "confirmed") return "green";
  if (s === "cancelled") return "red";
  if (s === "pending" || s === "requested") return "yellow";
  return "gray";
}

function workflowTone(s: AppointmentWorkflowStatus): "green" | "red" | "yellow" | "gray" {
  if (s === "confirmed" || s === "doctor_available") return "green";
  if (s === "missed" || s === "doctor_unavailable" || s === "cancelled") return "red";
  if (s === "reminder_sent" || s === "doctor_delayed" || s === "pending") return "yellow";
  return "gray";
}

function humanize(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function EventsModal({
  hospitalId, appointment, onClose,
}: {
  hospitalId: string;
  appointment: Appointment | null;
  onClose: () => void;
}) {
  const open = appointment !== null;
  const { data = [], isLoading, isError } = useQuery({
    queryKey: ["appointment-events", hospitalId, appointment?.id],
    queryFn: () => api.listAppointmentEvents(hospitalId, appointment!.id),
    enabled: open,
  });

  const sorted = React.useMemo(
    () =>
      [...data].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    [data],
  );

  return (
    <Modal open={open} onClose={onClose} title="Appointment timeline">
      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>
      ) : isError ? (
        <EmptyState title={`Events not available yet ${EM_DASH} ships with the workflow backend`} />
      ) : sorted.length === 0 ? (
        <EmptyState title="No events yet" />
      ) : (
        <ol className="space-y-4">
          {sorted.map((ev) => (
            <li key={ev.id} className="relative border-l-2 border-gray-200 pl-4">
              <span className="absolute -left-[5px] top-1.5 h-2 w-2 rounded-full bg-brand-500" />
              <div className="text-sm font-medium text-gray-900">{humanize(ev.event_type)}</div>
              {ev.detail && <div className="mt-0.5 text-sm text-gray-600">{ev.detail}</div>}
              <div className="mt-0.5 text-xs text-gray-400">{fmtDateTime(ev.created_at)}</div>
            </li>
          ))}
        </ol>
      )}
    </Modal>
  );
}

function AppointmentsInner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = React.useState<AppointmentStatus | "">("");
  const [eventsFor, setEventsFor] = React.useState<Appointment | null>(null);

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
      cell: ({ row }) => row.original.patient_name ?? <span className="text-gray-400">{EM_DASH}</span>,
    },
    {
      header: "Phone",
      accessorKey: "patient_phone",
      cell: ({ row }) => row.original.patient_phone ?? <span className="text-gray-400">{EM_DASH}</span>,
    },
    {
      header: "Slot",
      accessorKey: "slot_time",
      cell: ({ row }) =>
        row.original.slot_time ? fmtDateTime(row.original.slot_time) : <span className="text-gray-400">{EM_DASH}</span>,
    },
    {
      header: "Status",
      accessorKey: "status",
      cell: ({ row }) => {
        const appt = row.original;
        return (
          <div className="flex flex-wrap items-center gap-1">
            <Badge tone={statusTone(appt.status)}>{appt.status}</Badge>
            {appt.workflow_status && (
              <Badge tone={workflowTone(appt.workflow_status)} className="text-xs">
                {humanize(appt.workflow_status)}
              </Badge>
            )}
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
            <Button
              variant="outline"
              className="px-2 py-1 text-xs"
              onClick={() => setEventsFor(appt)}
            >
              View events
            </Button>
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
          searchPlaceholder={`Search patient${ELLIPSIS}`}
          emptyTitle="No appointments found"
        />
      )}
      <EventsModal
        hospitalId={hospitalId}
        appointment={eventsFor}
        onClose={() => setEventsFor(null)}
      />
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
