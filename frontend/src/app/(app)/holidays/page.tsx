"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Holiday } from "@/lib/types";
import { PageHeader, Button, Field, Input, Badge, Spinner } from "@/components/ui";
import { RequireHospital } from "@/components/require-hospital";
import { useToast } from "@/components/providers";

function Inner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const { data = [], isLoading } = useQuery({
    queryKey: ["holidays", hospitalId],
    queryFn: () => api.listHolidays(hospitalId),
  });

  const [date, setDate] = React.useState("");
  const [reason, setReason] = React.useState("");
  const [closed, setClosed] = React.useState(true);
  const [openTime, setOpenTime] = React.useState("");
  const [closeTime, setCloseTime] = React.useState("");

  const saveMut = useMutation({
    mutationFn: () =>
      api.createHoliday(hospitalId, {
        holiday_date: date,
        reason,
        closed,
        open_time: closed ? null : openTime || null,
        close_time: closed ? null : closeTime || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["holidays", hospitalId] });
      toast("Holiday saved", "ok");
      setDate(""); setReason(""); setClosed(true); setOpenTime(""); setCloseTime("");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const delMut = useMutation({
    mutationFn: (id: string) => api.deleteHoliday(hospitalId, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["holidays", hospitalId] });
      toast("Holiday removed", "ok");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  return (
    <div>
      <PageHeader title="Holidays & Closures" />
      <p className="mb-4 text-sm text-gray-500">
        Mark dates the hospital is closed (or has special hours). On these dates the
        AI receptionist tells callers it is closed instead of offering appointments.
      </p>

      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Date *">
            <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label="Reason">
            <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Onam, Public Holiday" />
          </Field>
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" checked={closed} onChange={(e) => setClosed(e.target.checked)}
                 className="h-4 w-4 rounded border-gray-300 text-blue-600" />
          Closed all day
        </label>
        {!closed && (
          <div className="mt-3 grid grid-cols-2 gap-3">
            <Field label="Special open time">
              <Input value={openTime} onChange={(e) => setOpenTime(e.target.value)} placeholder="09:00" />
            </Field>
            <Field label="Special close time">
              <Input value={closeTime} onChange={(e) => setCloseTime(e.target.value)} placeholder="13:00" />
            </Field>
          </div>
        )}
        <div className="mt-4">
          <Button onClick={() => saveMut.mutate()} disabled={!date || saveMut.isPending}>
            {saveMut.isPending ? "Saving…" : "Add holiday"}
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>
      ) : data.length === 0 ? (
        <p className="text-sm text-gray-400">No holidays set.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-500">
              <tr>
                <th className="px-4 py-2">Date</th>
                <th className="px-4 py-2">Reason</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {[...data].sort((a, b) => a.holiday_date.localeCompare(b.holiday_date)).map((h: Holiday) => (
                <tr key={h.id} className="border-t border-gray-100">
                  <td className="px-4 py-2 font-medium">{h.holiday_date}</td>
                  <td className="px-4 py-2">{h.reason || <span className="text-gray-400">—</span>}</td>
                  <td className="px-4 py-2">
                    {h.closed
                      ? <Badge tone="red">Closed</Badge>
                      : <Badge tone="yellow">{`${h.open_time || "?"}–${h.close_time || "?"}`}</Badge>}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <Button variant="danger" className="px-2 py-1 text-xs"
                            disabled={delMut.isPending} onClick={() => delMut.mutate(h.id)}>
                      Remove
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function HolidaysPage() {
  return <RequireHospital>{(hid) => <Inner hospitalId={hid} />}</RequireHospital>;
}
