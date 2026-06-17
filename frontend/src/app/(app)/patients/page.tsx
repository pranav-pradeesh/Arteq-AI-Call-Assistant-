"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { UserPlus, Phone, MessageCircle, CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/utils";
import { RequireHospital } from "@/components/require-hospital";
import { Modal } from "@/components/modal";
import {
  PageHeader, Button, Input, Field, Card, CardBody, CardHeader, Badge,
  EmptyState, Spinner,
} from "@/components/ui";
import { useToast } from "@/components/providers";
import type { Patient } from "@/lib/types";

function AddPatientModal({
  hospitalId, open, onClose,
}: { hospitalId: string; open: boolean; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [name, setName] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [done, setDone] = React.useState(false);

  const close = () => { setName(""); setPhone(""); setDone(false); onClose(); };

  const mut = useMutation({
    mutationFn: () => api.createPatient(hospitalId, { name: name.trim(), phone: phone.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["patients", hospitalId] });
      qc.invalidateQueries({ queryKey: ["whatsapp", hospitalId] });
      setDone(true);
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  return (
    <Modal open={open} onClose={close} title="Add new patient">
      {done ? (
        <div className="space-y-4">
          <div className="flex items-center gap-2 rounded-lg bg-green-50 p-3 text-sm text-green-800">
            <CheckCircle2 className="h-5 w-5 shrink-0" /> Patient added. Intake automations triggered:
          </div>
          <ul className="space-y-2 text-sm">
            <li className="flex items-start gap-2">
              <Phone className="mt-0.5 h-4 w-4 text-brand-600" />
              <span>AI agent queued an <strong>outbound welcome call</strong> to {phone}.</span>
            </li>
            <li className="flex items-start gap-2">
              <MessageCircle className="mt-0.5 h-4 w-4 text-brand-600" />
              <span>A <strong>WhatsApp</strong> welcome message with the patient ID was sent.</span>
            </li>
          </ul>
          <div className="flex justify-end">
            <Button onClick={close}>Done</Button>
          </div>
        </div>
      ) : (
        <form
          onSubmit={(e) => { e.preventDefault(); if (name.trim() && phone.trim()) mut.mutate(); }}
          className="space-y-3"
        >
          <Field label="Patient name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Anita Sharma" autoFocus />
          </Field>
          <Field label="Phone number (WhatsApp)">
            <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98765 43210" />
          </Field>
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" onClick={close}>Cancel</Button>
            <Button type="submit" disabled={!name.trim() || !phone.trim() || mut.isPending}>
              {mut.isPending && <Spinner />} Add patient
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

function PatientsInner({ hospitalId }: { hospitalId: string }) {
  const [open, setOpen] = React.useState(false);
  const { data = [], isLoading, isError } = useQuery({
    queryKey: ["patients", hospitalId],
    queryFn: () => api.listPatients(hospitalId),
    retry: false,
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Patients"
        action={
          <Button onClick={() => setOpen(true)}>
            <UserPlus className="h-4 w-4" /> Add patient
          </Button>
        }
      />
      <Card>
        <CardHeader className="flex items-center justify-between">
          <span className="font-medium text-gray-900">Registered patients</span>
          <Badge>{data.length}</Badge>
        </CardHeader>
        <CardBody className="p-0">
          {isLoading ? (
            <div className="flex items-center gap-2 px-4 py-6 text-sm text-gray-500"><Spinner /> Loading…</div>
          ) : isError ? (
            <EmptyState
              title="Patients endpoint not available yet"
              hint="This ships with the patient-intake backend (see backend spec). The UI is ready."
            />
          ) : data.length === 0 ? (
            <EmptyState title="No patients yet" hint="Click “Add patient” to begin." />
          ) : (
            <ul className="divide-y divide-gray-100">
              {data.map((p: Patient) => (
                <li key={p.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-gray-900">{p.name}</p>
                    <p className="truncate text-xs text-gray-500">{p.phone}</p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end">
                    <span className="font-mono text-xs text-gray-500">{p.id}</span>
                    {p.created_at && <span className="text-[11px] text-gray-400">{fmtDateTime(p.created_at)}</span>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <AddPatientModal hospitalId={hospitalId} open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

export default function PatientsPage() {
  return <RequireHospital>{(hid) => <PatientsInner hospitalId={hid} />}</RequireHospital>;
}
