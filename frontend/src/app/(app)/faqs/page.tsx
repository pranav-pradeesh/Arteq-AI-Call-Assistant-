"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Faq } from "@/lib/types";
import { RequireHospital } from "@/components/require-hospital";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { FormModal } from "@/components/modal";
import { PageHeader, Button, Field, Input, Textarea, Badge } from "@/components/ui";
import { useToast } from "@/components/providers";

type FaqForm = {
  category: string;
  question: string;
  answer: string;
  answer_ml: string;
  priority: string;
  tags: string; // comma-separated
};

const BLANK: FaqForm = {
  category: "", question: "", answer: "", answer_ml: "", priority: "0", tags: "",
};

function Inner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();

  const { data = [], isLoading } = useQuery({
    queryKey: ["faqs", hospitalId],
    queryFn: () => api.listFaqs(hospitalId),
  });

  const [open, setOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<Faq | null>(null);
  const [form, setForm] = React.useState<FaqForm>(BLANK);

  function openCreate() {
    setEditing(null);
    setForm(BLANK);
    setOpen(true);
  }

  function openEdit(f: Faq) {
    setEditing(f);
    setForm({
      category: f.category ?? "",
      question: f.question ?? "",
      answer: f.answer ?? "",
      answer_ml: f.answer_ml ?? "",
      priority: String(f.priority ?? 0),
      tags: (f.tags ?? []).join(", "),
    });
    setOpen(true);
  }

  const set =
    (k: keyof FaqForm) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((f) => ({ ...f, [k]: e.target.value }));

  function formToPayload() {
    return {
      category: form.category || null,
      question: form.question,
      answer: form.answer,
      answer_ml: form.answer_ml || null,
      priority: parseInt(form.priority, 10) || 0,
      tags: form.tags
        ? form.tags.split(",").map((t) => t.trim()).filter(Boolean)
        : [],
    };
  }

  const saveMut = useMutation({
    mutationFn: () =>
      editing
        ? api.updateFaq(hospitalId, editing.id, formToPayload())
        : api.createFaq(hospitalId, formToPayload()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["faqs", hospitalId] });
      toast(editing ? "FAQ updated" : "FAQ created", "ok");
      setOpen(false);
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteFaq(hospitalId, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["faqs", hospitalId] });
      toast("FAQ deleted", "ok");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const columns: ColumnDef<Faq>[] = [
    {
      header: "Question",
      accessorKey: "question",
      cell: ({ row }) => (
        <span className="font-medium text-gray-900 line-clamp-2 max-w-xs">
          {row.original.question}
        </span>
      ),
    },
    { header: "Category", accessorKey: "category" },
    {
      header: "Priority",
      accessorKey: "priority",
      cell: ({ row }) => (
        <Badge tone="blue">{row.original.priority}</Badge>
      ),
    },
    {
      header: "Tags",
      id: "tags",
      cell: ({ row }) => {
        const tags = row.original.tags ?? [];
        if (tags.length === 0) return <span className="text-gray-400">—</span>;
        return (
          <div className="flex flex-wrap gap-1">
            {tags.map((t) => (
              <Badge key={t} tone="gray">{t}</Badge>
            ))}
          </div>
        );
      },
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
              if (confirm("Delete this FAQ?")) deleteMut.mutate(row.original.id);
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
        title="FAQs"
        action={<Button onClick={openCreate}>+ Add FAQ</Button>}
      />
      <DataTable
        columns={columns}
        data={data}
        searchPlaceholder="Search FAQs…"
        emptyTitle="No FAQs yet"
      />
      <FormModal
        open={open}
        onClose={() => setOpen(false)}
        title={editing ? "Edit FAQ" : "New FAQ"}
        onSubmit={(e) => { e.preventDefault(); saveMut.mutate(); }}
        saving={saveMut.isPending}
        wide
      >
        <Field label="Category">
          <Input value={form.category} onChange={set("category")} placeholder="e.g. General, Appointments" />
        </Field>
        <Field label="Question *">
          <Textarea value={form.question} onChange={set("question")} rows={2} required />
        </Field>
        <Field label="Answer *">
          <Textarea value={form.answer} onChange={set("answer")} rows={4} required />
        </Field>
        <Field label="Answer (Malayalam)">
          <Textarea value={form.answer_ml} onChange={set("answer_ml")} rows={3} />
        </Field>
        <Field label="Priority">
          <Input
            type="number"
            value={form.priority}
            onChange={set("priority")}
            min={0}
          />
        </Field>
        <Field label="Tags (comma-separated)">
          <Input
            value={form.tags}
            onChange={set("tags")}
            placeholder="e.g. timing, location, doctor"
          />
        </Field>
      </FormModal>
    </div>
  );
}

export default function FaqsPage() {
  return (
    <RequireHospital>
      {(hid) => <Inner hospitalId={hid} />}
    </RequireHospital>
  );
}
