"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { PageHeader, Button, Card, CardHeader, CardBody, Field, Textarea, Spinner, EmptyState } from "@/components/ui";
import { RequireHospital } from "@/components/require-hospital";
import { useToast } from "@/components/providers";

const PLACEHOLDER = `Enter knowledge base content for your hospital's AI agent. Examples:

Parking:
- Free parking available in Basement B1 and B2 (first 2 hours)
- Paid parking in surface lot at Rs. 20/hour

Health Camps:
- Free diabetes screening every Saturday 9am–12pm, OPD Block A
- Monthly blood donation camp on the first Sunday

Policies:
- Visiting hours: 10am–12pm and 5pm–7pm daily
- Attendant pass required for ICU visits (collect from reception)
- Online reports available via the patient portal within 24 hours

Emergency:
- 24/7 emergency: +91 98765 43210
- Ambulance: dial 1066 or our direct line

Departments on which floor, special services, etc.`;

function KnowledgeInner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [text, setText] = React.useState("");
  const [loaded, setLoaded] = React.useState(false);

  const { data: hospitalData, isLoading, isError } = useQuery({
    queryKey: ["hospital", hospitalId],
    queryFn: () => api.getHospital(hospitalId),
  });

  React.useEffect(() => {
    if (hospitalData && !loaded) {
      setText(hospitalData.knowledge_base ?? "");
      setLoaded(true);
    }
  }, [hospitalData, loaded]);

  const saveMutation = useMutation({
    mutationFn: (kb: string) => api.saveKnowledgeBase(hospitalId, kb),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hospital", hospitalId] });
      toast("Knowledge base saved");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  if (isLoading) {
    return <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>;
  }
  if (isError) {
    return <EmptyState title="Could not load hospital data" hint="Check your connection and try again." />;
  }

  return (
    <div>
      <PageHeader title="Knowledge Base" />
      <Card>
        <CardHeader>
          <span className="text-sm font-semibold text-gray-700">Hospital Knowledge</span>
        </CardHeader>
        <CardBody>
          <p className="mb-3 text-xs text-gray-400">
            This text is used by the AI agent to answer patient questions. Include parking info,
            visiting hours, policies, health camps, department locations, and any FAQs.
          </p>
          <Field label="Knowledge Base Content">
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={20}
              placeholder={PLACEHOLDER}
              className="w-full font-mono text-xs leading-relaxed"
            />
          </Field>
          <div className="mt-4 flex items-center gap-3">
            <Button
              onClick={() => saveMutation.mutate(text)}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending && <Spinner />} Save Knowledge Base
            </Button>
            <span className="text-xs text-gray-400">{text.length.toLocaleString()} characters</span>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}

export default function KnowledgePage() {
  return (
    <RequireHospital>
      {(hospitalId) => <KnowledgeInner hospitalId={hospitalId} />}
    </RequireHospital>
  );
}
