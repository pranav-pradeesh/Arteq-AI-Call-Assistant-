"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useToast, useCurrentHospital } from "@/components/providers";
import {
  Button, Card, CardBody, CardHeader, Field, Input, Select, Spinner, PageHeader, Badge,
} from "@/components/ui";
import type { Tier } from "@/lib/types";

// Minimal shapes for the one-shot wizard payload (POST /admin/hospitals/wizard).
type WizDoctor = { name: string; specialty: string };
type WizDept = { name: string; name_ml: string; floor: string; doctors: WizDoctor[] };
type WizFaq = { category: string; question: string; answer: string };

export default function OnboardingPage() {
  const router = useRouter();
  const toast = useToast();
  const { setHospitalId } = useCurrentHospital();

  const [form, setForm] = React.useState({
    name: "", name_ml: "", tier: "hospital" as Tier, address: "", phone: "", slug: "",
    provision_plivo_number: false,
  });
  const [departments, setDepartments] = React.useState<WizDept[]>([]);
  const [faqs, setFaqs] = React.useState<WizFaq[]>([]);
  const [result, setResult] = React.useState<{ hospital_id: string; slug: string; plivo_number?: string; bsnl_forward_code?: string } | null>(null);

  const set = (k: keyof typeof form, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  const mut = useMutation({
    mutationFn: () =>
      api.wizard({
        ...form,
        departments: departments.map((d) => ({ ...d, doctors: d.doctors.filter((x) => x.name.trim()) })),
        faqs: faqs.filter((f) => f.question.trim()),
      }),
    onSuccess: (res) => {
      setResult(res);
      setHospitalId(res.hospital_id);
      toast("Hospital created");
    },
    onError: (e: Error) => toast(e.message || "Failed to create hospital", "err"),
  });

  // ── department / doctor / faq editors ──────────────────────────
  const addDept = () => setDepartments((d) => [...d, { name: "", name_ml: "", floor: "", doctors: [] }]);
  const updDept = (i: number, k: keyof WizDept, v: unknown) =>
    setDepartments((ds) => ds.map((d, idx) => (idx === i ? { ...d, [k]: v } : d)));
  const rmDept = (i: number) => setDepartments((ds) => ds.filter((_, idx) => idx !== i));
  const addDoctor = (i: number) =>
    setDepartments((ds) => ds.map((d, idx) => (idx === i ? { ...d, doctors: [...d.doctors, { name: "", specialty: "" }] } : d)));
  const updDoctor = (i: number, j: number, k: keyof WizDoctor, v: string) =>
    setDepartments((ds) => ds.map((d, idx) => (idx === i ? { ...d, doctors: d.doctors.map((dc, jdx) => (jdx === j ? { ...dc, [k]: v } : dc)) } : d)));

  const addFaq = () => setFaqs((f) => [...f, { category: "general", question: "", answer: "" }]);
  const updFaq = (i: number, k: keyof WizFaq, v: string) =>
    setFaqs((fs) => fs.map((f, idx) => (idx === i ? { ...f, [k]: v } : f)));
  const rmFaq = (i: number) => setFaqs((fs) => fs.filter((_, idx) => idx !== i));

  if (result) {
    return (
      <div className="mx-auto max-w-xl">
        <PageHeader title="Hospital created" />
        <Card>
          <CardBody className="space-y-3 text-sm">
            <div className="flex items-center gap-2"><Badge tone="green">Success</Badge><span className="font-medium">{form.name}</span></div>
            <div><span className="text-gray-500">Slug:</span> <code>{result.slug}</code></div>
            {result.plivo_number && <div><span className="text-gray-500">Plivo number:</span> <code>{result.plivo_number}</code></div>}
            {result.bsnl_forward_code && (
              <div><span className="text-gray-500">BSNL forward code:</span> <code>{result.bsnl_forward_code}</code></div>
            )}
            <div className="flex gap-2 pt-2">
              <Button onClick={() => router.push("/overview")}>Go to overview</Button>
              <Button variant="outline" onClick={() => { setResult(null); setDepartments([]); setFaqs([]); setForm({ name: "", name_ml: "", tier: "hospital", address: "", phone: "", slug: "", provision_plivo_number: false }); }}>
                Add another
              </Button>
            </div>
          </CardBody>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <PageHeader title="Onboard a hospital" />

      <Card>
        <CardHeader><h2 className="font-medium">Basics</h2></CardHeader>
        <CardBody className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Name *"><Input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Malabar Hospital" /></Field>
          <Field label="Name (Malayalam)"><Input value={form.name_ml} onChange={(e) => set("name_ml", e.target.value)} /></Field>
          <Field label="Slug *"><Input className="font-mono" value={form.slug} onChange={(e) => set("slug", e.target.value)} placeholder="malabar-hospital" /></Field>
          <Field label="Tier">
            <Select value={form.tier} onChange={(e) => set("tier", e.target.value as Tier)}>
              <option value="hospital">hospital</option>
              <option value="clinic">clinic</option>
            </Select>
          </Field>
          <Field label="Phone"><Input value={form.phone} onChange={(e) => set("phone", e.target.value)} placeholder="+91 …" /></Field>
          <Field label="Address"><Input value={form.address} onChange={(e) => set("address", e.target.value)} /></Field>
          <label className="col-span-full mt-1 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.provision_plivo_number} onChange={(e) => set("provision_plivo_number", e.target.checked)} />
            Provision a Plivo number now
          </label>
        </CardBody>
      </Card>

      <Card>
        <CardHeader className="flex items-center justify-between">
          <h2 className="font-medium">Departments & doctors</h2>
          <Button variant="outline" onClick={addDept}>+ Department</Button>
        </CardHeader>
        <CardBody className="space-y-4">
          {departments.length === 0 && <p className="text-sm text-gray-400">Optional — add departments and their doctors.</p>}
          {departments.map((d, i) => (
            <div key={i} className="rounded-lg border border-gray-200 p-3">
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                <Input value={d.name} onChange={(e) => updDept(i, "name", e.target.value)} placeholder="Cardiology" />
                <Input value={d.name_ml} onChange={(e) => updDept(i, "name_ml", e.target.value)} placeholder="Name (ML)" />
                <Input value={d.floor} onChange={(e) => updDept(i, "floor", e.target.value)} placeholder="3rd Floor" />
              </div>
              <div className="mt-2 space-y-2 pl-3">
                {d.doctors.map((dc, j) => (
                  <div key={j} className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <Input value={dc.name} onChange={(e) => updDoctor(i, j, "name", e.target.value)} placeholder="Dr. Name" />
                    <Input value={dc.specialty} onChange={(e) => updDoctor(i, j, "specialty", e.target.value)} placeholder="Specialty" />
                  </div>
                ))}
                <div className="flex gap-2">
                  <Button variant="ghost" onClick={() => addDoctor(i)}>+ Doctor</Button>
                  <Button variant="ghost" className="text-red-600" onClick={() => rmDept(i)}>Remove department</Button>
                </div>
              </div>
            </div>
          ))}
        </CardBody>
      </Card>

      <Card>
        <CardHeader className="flex items-center justify-between">
          <h2 className="font-medium">FAQs</h2>
          <Button variant="outline" onClick={addFaq}>+ FAQ</Button>
        </CardHeader>
        <CardBody className="space-y-3">
          {faqs.length === 0 && <p className="text-sm text-gray-400">Optional — seed a few common questions.</p>}
          {faqs.map((f, i) => (
            <div key={i} className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_2fr_auto]">
              <Input value={f.question} onChange={(e) => updFaq(i, "question", e.target.value)} placeholder="Question" />
              <Input value={f.answer} onChange={(e) => updFaq(i, "answer", e.target.value)} placeholder="Answer" />
              <Button variant="ghost" className="text-red-600" onClick={() => rmFaq(i)}>Remove</Button>
            </div>
          ))}
        </CardBody>
      </Card>

      <div className="flex justify-end">
        <Button onClick={() => mut.mutate()} disabled={!form.name || !form.slug || mut.isPending}>
          {mut.isPending && <Spinner />} Create hospital
        </Button>
      </div>
    </div>
  );
}
