"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CalendarPlus, Phone, Printer, CheckCircle2, Ticket, RefreshCw, XCircle, QrCode,
} from "lucide-react";
import { api } from "@/lib/api";
import { paiseToRupees, fmtDateTime } from "@/lib/utils";
import { RequireHospital } from "@/components/require-hospital";
import { Modal } from "@/components/modal";
import { CalendarPicker, FakeQR } from "@/components/scheduling";
import {
  PageHeader, Button, Input, Field, Badge, Card, CardBody, CardHeader,
  EmptyState, Spinner,
} from "@/components/ui";
import { useToast } from "@/components/providers";
import type { Booking, BookingStatus, PaymentMode } from "@/lib/types";

function statusBadge(s: BookingStatus) {
  const map: Record<BookingStatus, { tone: "green" | "red" | "yellow" | "gray"; label: string }> = {
    confirmed: { tone: "green", label: "confirmed" },
    pending_payment: { tone: "yellow", label: "awaiting payment" },
    awaiting_confirmation: { tone: "yellow", label: "awaiting AI confirm" },
    cancelled: { tone: "red", label: "cancelled" },
  };
  const v = map[s];
  return <Badge tone={v.tone}>{v.label}</Badge>;
}

function PayOption({
  active, onClick, icon, title, desc,
}: {
  active: boolean; onClick: () => void; icon: React.ReactNode; title: string; desc: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex flex-col gap-1 rounded-lg border p-3 text-left transition ${
        active ? "border-brand-600 bg-brand-50 ring-1 ring-brand-200" : "border-gray-200 hover:bg-gray-50"
      }`}
    >
      <span className="flex items-center gap-2 font-medium text-gray-900">{icon} {title}</span>
      <span className="text-xs text-gray-600">{desc}</span>
    </button>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-gray-500">{k}</dt>
      <dd className="text-right font-medium text-gray-900">{v}</dd>
    </div>
  );
}

function ResultStep({
  hospitalId, booking, onClose,
}: { hospitalId: string; booking: Booking; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [live, setLive] = React.useState<Booking>(booking);

  const payMut = useMutation({
    mutationFn: () => api.updateBookingStatus(hospitalId, live.id, "confirmed"),
    onSuccess: (b) => {
      setLive(b);
      qc.invalidateQueries({ queryKey: ["bookings", hospitalId] });
      qc.invalidateQueries({ queryKey: ["whatsapp", hospitalId] });
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  if (live.payment_mode === "pay_now") {
    return (
      <div className="space-y-4">
        <p className="text-sm text-gray-600">
          Payment slip prepared for the receptionist. Print it, the patient scans the QR and pays,
          then the booking confirms instantly.
        </p>
        <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed border-gray-300 p-5 sm:flex-row sm:items-start">
          <FakeQR seed={live.id} />
          <div className="flex-1 text-sm">
            <p className="font-semibold text-gray-900">Arteq Care · Payment slip</p>
            <dl className="mt-2 space-y-1">
              <Row k="Patient" v={`${live.patient_name} (${live.patient_id})`} />
              <Row k="Appointment" v={fmtDateTime(live.slot)} />
              <Row k="Amount" v={paiseToRupees(live.amount_paise)} />
              <Row k="Reference" v={live.id} />
            </dl>
            <Badge tone={live.status === "confirmed" ? "green" : "yellow"} className="mt-3">
              {live.status === "confirmed" ? "Paid · confirmed" : "Awaiting scan & pay"}
            </Badge>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" onClick={() => window.print()}>
            <Printer className="h-4 w-4" /> Print slip
          </Button>
          {live.status !== "confirmed" ? (
            <Button onClick={() => payMut.mutate()} disabled={payMut.isPending}>
              {payMut.isPending ? <Spinner /> : <CheckCircle2 className="h-4 w-4" />} Patient scanned &amp; paid
            </Button>
          ) : (
            <Button onClick={onClose}>Done</Button>
          )}
        </div>
      </div>
    );
  }

  // Pay later
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 rounded-lg bg-brand-50 p-3 text-sm text-brand-800">
        <Ticket className="h-5 w-5 shrink-0" /> Temporary token issued and sent on WhatsApp.
      </div>
      <div className="rounded-xl border border-gray-200 p-4 text-sm">
        <dl className="space-y-1">
          <Row k="Patient" v={`${live.patient_name} (${live.patient_id})`} />
          <Row k="Appointment" v={fmtDateTime(live.slot)} />
          <Row k="Token" v={live.token?.code ?? "—"} />
          <Row k="Token status" v={live.token?.active ? "active" : "inactive (awaiting AI confirmation)"} />
        </dl>
      </div>
      <ul className="space-y-1.5 text-xs text-gray-500">
        <li>• The token stays <strong>inactive</strong> until the patient confirms.</li>
        <li>• ~1 week before, the AI agent calls, references the token, and asks to confirm.</li>
        <li>• On confirmation the token becomes <strong>active</strong>.</li>
      </ul>
      <div className="flex justify-end">
        <Button onClick={onClose}>Done</Button>
      </div>
    </div>
  );
}

function ScheduleModal({
  hospitalId, open, onClose,
}: { hospitalId: string; open: boolean; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const { data: patients = [] } = useQuery({
    queryKey: ["patients", hospitalId],
    queryFn: () => api.listPatients(hospitalId),
    enabled: open,
    retry: false,
  });

  const [patientId, setPatientId] = React.useState("");
  const [slot, setSlot] = React.useState<string | null>(null);
  const [rupees, setRupees] = React.useState("500");
  const [mode, setMode] = React.useState<PaymentMode>("pay_now");
  const [created, setCreated] = React.useState<Booking | null>(null);

  const close = () => {
    setPatientId(""); setSlot(null); setRupees("500"); setMode("pay_now"); setCreated(null);
    onClose();
  };

  const mut = useMutation({
    mutationFn: () =>
      api.createBooking(hospitalId, {
        patient_id: patientId,
        slot: slot as string,
        payment_mode: mode,
        amount_paise: Math.round((Number(rupees) || 0) * 100),
      }),
    onSuccess: (b) => {
      setCreated(b);
      qc.invalidateQueries({ queryKey: ["bookings", hospitalId] });
      qc.invalidateQueries({ queryKey: ["whatsapp", hospitalId] });
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  return (
    <Modal
      open={open}
      onClose={close}
      title="Schedule appointment"
      wide
      footer={
        created ? undefined : (
          <>
            <Button type="button" variant="ghost" onClick={close}>Cancel</Button>
            <Button type="submit" form="real-schedule-form" disabled={!patientId || !slot || mut.isPending}>
              {mut.isPending && <Spinner />} Create booking
            </Button>
          </>
        )
      }
    >
      {created ? (
        <ResultStep hospitalId={hospitalId} booking={created} onClose={close} />
      ) : (
        <form
          id="real-schedule-form"
          onSubmit={(e) => { e.preventDefault(); if (patientId && slot) mut.mutate(); }}
          className="space-y-4"
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Patient">
              <select className="input" value={patientId} onChange={(e) => setPatientId(e.target.value)}>
                <option value="" disabled>Select patient…</option>
                {patients.map((p) => (
                  <option key={p.id} value={p.id}>{p.name} · {p.id}</option>
                ))}
              </select>
              {patients.length === 0 && (
                <p className="mt-1 text-xs text-amber-600">Add a patient first (Patients page).</p>
              )}
            </Field>
            <Field label="Consultation fee (₹)">
              <Input type="number" min={0} value={rupees} onChange={(e) => setRupees(e.target.value)} />
            </Field>
          </div>

          <div>
            <p className="label">Appointment date &amp; time</p>
            <CalendarPicker value={slot} onChange={setSlot} />
            {slot && (
              <p className="mt-2 text-xs text-gray-600">
                Selected: <strong>{fmtDateTime(slot)}</strong>
              </p>
            )}
          </div>

          <div>
            <p className="label">Payment</p>
            <div className="grid gap-2 sm:grid-cols-2">
              <PayOption
                active={mode === "pay_now"} onClick={() => setMode("pay_now")}
                icon={<QrCode className="h-5 w-5" />} title="Pay now"
                desc="Generate a QR slip for the receptionist. Patient scans & pays — booking confirmed instantly."
              />
              <PayOption
                active={mode === "pay_later"} onClick={() => setMode("pay_later")}
                icon={<Ticket className="h-5 w-5" />} title="Pay at appointment"
                desc="Issue a temporary token over WhatsApp. AI confirms ~1 week before; token activates on confirmation."
              />
            </div>
          </div>
        </form>
      )}
    </Modal>
  );
}

function BookingsInner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [scheduleOpen, setScheduleOpen] = React.useState(false);

  const { data = [], isLoading, isError } = useQuery({
    queryKey: ["bookings", hospitalId],
    queryFn: () => api.listBookings(hospitalId),
    retry: false,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["bookings", hospitalId] });
    qc.invalidateQueries({ queryKey: ["whatsapp", hospitalId] });
  };

  const confirmCall = useMutation({
    mutationFn: (id: string) => api.runConfirmationCall(hospitalId, id),
    onSuccess: () => { invalidate(); toast("AI confirmation call completed · token activated"); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const changeTok = useMutation({
    mutationFn: (id: string) => api.changeBookingToken(hospitalId, id),
    onSuccess: () => { invalidate(); toast("Token re-issued"); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api.updateBookingStatus(hospitalId, id, "cancelled"),
    onSuccess: () => { invalidate(); toast("Booking cancelled"); },
    onError: (e: Error) => toast(e.message, "err"),
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Bookings & Tokens"
        action={
          <Button onClick={() => setScheduleOpen(true)}>
            <CalendarPlus className="h-4 w-4" /> Schedule
          </Button>
        }
      />
      <Card>
        <CardHeader className="flex items-center justify-between">
          <span className="font-medium text-gray-900">All bookings</span>
          <Badge>{data.length}</Badge>
        </CardHeader>
        <CardBody className="p-0">
          {isLoading ? (
            <div className="flex items-center gap-2 px-4 py-6 text-sm text-gray-500"><Spinner /> Loading…</div>
          ) : isError ? (
            <EmptyState
              title="Bookings endpoint not available yet"
              hint="This ships with the patient-intake backend (see backend spec). The UI is ready."
            />
          ) : data.length === 0 ? (
            <EmptyState title="No bookings yet" hint="Use “Schedule” to create one." />
          ) : (
            <ul className="divide-y divide-gray-100">
              {data.map((b: Booking) => (
                <li key={b.id} className="px-4 py-3 text-sm">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate font-medium text-gray-900">{b.patient_name}</p>
                      <p className="text-xs text-gray-500">{fmtDateTime(b.slot)} · {paiseToRupees(b.amount_paise)}</p>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      {statusBadge(b.status)}
                      {b.token && (
                        <span className="font-mono text-[11px] text-gray-500">
                          {b.token.code} {b.token.active ? "·active" : "·inactive"}
                        </span>
                      )}
                    </div>
                  </div>
                  {b.status !== "cancelled" && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {b.payment_mode === "pay_later" && b.status === "awaiting_confirmation" && (
                        <Button variant="outline" className="px-2 py-1 text-xs"
                          onClick={() => confirmCall.mutate(b.id)} disabled={confirmCall.isPending}>
                          <Phone className="h-3.5 w-3.5" /> Run AI confirmation call
                        </Button>
                      )}
                      {b.token && (
                        <Button variant="outline" className="px-2 py-1 text-xs"
                          onClick={() => changeTok.mutate(b.id)} disabled={changeTok.isPending}>
                          <RefreshCw className="h-3.5 w-3.5" /> Change token
                        </Button>
                      )}
                      <Button variant="danger" className="px-2 py-1 text-xs"
                        onClick={() => { if (confirm("Cancel this booking?")) cancel.mutate(b.id); }}
                        disabled={cancel.isPending}>
                        <XCircle className="h-3.5 w-3.5" /> Cancel
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <ScheduleModal hospitalId={hospitalId} open={scheduleOpen} onClose={() => setScheduleOpen(false)} />
    </div>
  );
}

export default function BookingsPage() {
  return <RequireHospital>{(hid) => <BookingsInner hospitalId={hid} />}</RequireHospital>;
}
