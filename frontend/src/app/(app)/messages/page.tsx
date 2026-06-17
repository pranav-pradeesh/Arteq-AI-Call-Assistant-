"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { MessageCircle } from "lucide-react";
import { api } from "@/lib/api";
import { RequireHospital } from "@/components/require-hospital";
import { WhatsAppFeed } from "@/components/whatsapp-feed";
import { PageHeader, Card, CardBody, CardHeader, Badge, EmptyState, Spinner } from "@/components/ui";
import type { WhatsAppMessage } from "@/lib/types";

function MessagesInner({ hospitalId }: { hospitalId: string }) {
  const { data = [], isLoading, isError } = useQuery({
    queryKey: ["whatsapp", hospitalId],
    queryFn: () => api.listWhatsApp(hospitalId),
    retry: false,
  });

  return (
    <div className="space-y-4">
      <PageHeader title="WhatsApp Messages" />
      <Card>
        <CardHeader className="flex items-center justify-between">
          <span className="flex items-center gap-2 font-medium text-gray-900">
            <MessageCircle className="h-4 w-4 text-gray-500" /> Outbound messages
          </span>
          <Badge>{data.length}</Badge>
        </CardHeader>
        <CardBody className="p-0">
          {isLoading ? (
            <div className="flex items-center gap-2 px-4 py-6 text-sm text-gray-500"><Spinner /> Loading…</div>
          ) : isError ? (
            <EmptyState
              title="WhatsApp endpoint not available yet"
              hint="This ships with the patient-intake backend (see backend spec). The UI is ready."
            />
          ) : data.length === 0 ? (
            <EmptyState title="No messages yet" hint="Welcome messages, tokens & confirmations appear here." />
          ) : (
            <WhatsAppFeed
              items={data.map((m: WhatsAppMessage) => ({
                id: m.id,
                name: m.patient_name ?? m.phone,
                phone: m.phone,
                body: m.body,
                at: m.at,
              }))}
            />
          )}
        </CardBody>
      </Card>
    </div>
  );
}

export default function MessagesPage() {
  return <RequireHospital>{(hid) => <MessagesInner hospitalId={hid} />}</RequireHospital>;
}
