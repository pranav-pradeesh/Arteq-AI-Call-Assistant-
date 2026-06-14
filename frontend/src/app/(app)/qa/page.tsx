"use client";
import * as React from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/utils";
import { RequireHospital } from "@/components/require-hospital";
import { DataTable, ColumnDef } from "@/components/data-table";
import { FormModal } from "@/components/modal";
import {
  PageHeader, EmptyState, Spinner, Badge, Button, Card, CardBody, CardHeader,
  Label, Input,
} from "@/components/ui";
import { useToast } from "@/components/providers";
import type { CallFeedback, MissedQuestion } from "@/lib/types";

// ── Promote to FAQ modal ─────────────────────────────────────────────────────

function PromoteModal({
  open,
  onClose,
  hospitalId,
  question,
}: {
  open: boolean;
  onClose: () => void;
  hospitalId: string;
  question: string;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [q, setQ] = React.useState(question);
  const [answer, setAnswer] = React.useState("");

  React.useEffect(() => {
    if (open) {
      setQ(question);
      setAnswer("");
    }
  }, [open, question]);

  const mutation = useMutation({
    mutationFn: () =>
      api.createFaq(hospitalId, { question: q, answer, category: "from_missed" }),
    onSuccess: () => {
      toast("FAQ created successfully.");
      queryClient.invalidateQueries({ queryKey: ["faqs", hospitalId] });
      onClose();
    },
    onError: () => {
      toast("Failed to create FAQ.", "err");
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim() || !answer.trim()) return;
    mutation.mutate();
  }

  return (
    <FormModal
      open={open}
      onClose={onClose}
      title="Promote to FAQ"
      onSubmit={handleSubmit}
      saving={mutation.isPending}
      submitLabel="Create FAQ"
    >
      <div>
        <Label htmlFor="pq-question">Question</Label>
        <Input
          id="pq-question"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          required
        />
      </div>
      <div>
        <Label htmlFor="pq-answer">Answer</Label>
        <textarea
          id="pq-answer"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          required
          rows={4}
          className="input w-full"
        />
      </div>
    </FormModal>
  );
}

// ── Main QA page ─────────────────────────────────────────────────────────────

function QAInner({ hospitalId }: { hospitalId: string }) {
  const [promoteTarget, setPromoteTarget] = React.useState<MissedQuestion | null>(null);

  const feedbackQuery = useQuery({
    queryKey: ["feedback", hospitalId, 1, 3],
    queryFn: () => api.listFeedback(hospitalId, 1, 3),
    retry: false,
  });

  const missedQuery = useQuery({
    queryKey: ["missedQuestions", hospitalId],
    queryFn: () => api.listMissedQuestions(hospitalId),
    retry: false,
  });

  const feedbackColumns: ColumnDef<CallFeedback, any>[] = [
    {
      header: "Call",
      accessorKey: "call_id",
      cell: ({ getValue }) => {
        const cid: string = getValue();
        return (
          <Link href={`/calls/${cid}`} className="font-mono text-xs text-indigo-600 hover:underline">
            {cid.slice(0, 8)}…
          </Link>
        );
      },
    },
    {
      header: "Rating",
      accessorKey: "rating",
      cell: ({ getValue }) => {
        const r: number = getValue();
        const tone = r <= 2 ? "red" : r === 3 ? "yellow" : "green";
        return <Badge tone={tone}>{r}/5</Badge>;
      },
    },
    {
      header: "Comments",
      accessorKey: "comments",
      cell: ({ getValue }) => (
        <span className="text-gray-700">{getValue() ?? <span className="text-gray-400">—</span>}</span>
      ),
    },
    {
      header: "Date",
      accessorKey: "created_at",
      cell: ({ getValue }) => fmtDateTime(getValue()),
    },
  ];

  const missedColumns: ColumnDef<MissedQuestion, any>[] = [
    {
      header: "Question",
      accessorKey: "question",
      cell: ({ getValue }) => (
        <span className="text-gray-800">{getValue() ?? <span className="text-gray-400">—</span>}</span>
      ),
    },
    {
      header: "Language",
      accessorKey: "language",
      cell: ({ getValue }) => (
        <span>{getValue() ?? <span className="text-gray-400">—</span>}</span>
      ),
    },
    {
      header: "Date",
      accessorKey: "created_at",
      cell: ({ getValue }) => fmtDateTime(getValue()),
    },
    {
      header: "",
      id: "promote",
      cell: ({ row }) => (
        <Button
          variant="outline"
          className="text-xs"
          onClick={() => setPromoteTarget(row.original)}
        >
          Promote to FAQ
        </Button>
      ),
    },
  ];

  return (
    <div className="space-y-8">
      <PageHeader title="Quality Assurance" />

      {/* Low-rated calls */}
      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-gray-700">Low-Rated Calls (1–3 stars)</h2>
        </CardHeader>
        <CardBody>
          {feedbackQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <Spinner /> Loading feedback…
            </div>
          ) : feedbackQuery.isError ? (
            <EmptyState
              title="Feedback endpoint not available yet"
              hint="Call feedback tracking ships with the backend additions."
            />
          ) : (
            <DataTable
              columns={feedbackColumns}
              data={feedbackQuery.data ?? []}
              pageSize={15}
              emptyTitle="No low-rated calls"
              searchPlaceholder="Search feedback…"
            />
          )}
        </CardBody>
      </Card>

      {/* Missed questions */}
      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-gray-700">Missed Questions</h2>
        </CardHeader>
        <CardBody>
          {missedQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <Spinner /> Loading missed questions…
            </div>
          ) : missedQuery.isError ? (
            <EmptyState
              title="Missed questions endpoint not available yet"
              hint="Missed question tracking ships with the backend additions."
            />
          ) : (
            <DataTable
              columns={missedColumns}
              data={missedQuery.data ?? []}
              pageSize={15}
              emptyTitle="No missed questions recorded"
              searchPlaceholder="Search questions…"
            />
          )}
        </CardBody>
      </Card>

      {/* Promote modal */}
      <PromoteModal
        open={promoteTarget != null}
        onClose={() => setPromoteTarget(null)}
        hospitalId={hospitalId}
        question={promoteTarget?.question ?? ""}
      />
    </div>
  );
}

export default function QAPage() {
  return (
    <RequireHospital>
      {(hid) => <QAInner hospitalId={hid} />}
    </RequireHospital>
  );
}
