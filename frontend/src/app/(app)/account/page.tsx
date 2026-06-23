"use client";
import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { useSession } from "next-auth/react";
import { api } from "@/lib/api";
import {
  PageHeader, Card, CardHeader, CardBody, Button, Field, Input, Spinner,
} from "@/components/ui";
import { useToast } from "@/components/providers";

export default function AccountPage() {
  const { data: session } = useSession();
  const toast = useToast();
  const role = (session?.user as { role?: string } | undefined)?.role;
  const [oldP, setOldP] = React.useState("");
  const [newP, setNewP] = React.useState("");
  const [confirm, setConfirm] = React.useState("");

  const mut = useMutation({
    mutationFn: () => api.changePassword(oldP, newP),
    onSuccess: () => {
      toast("Password updated", "success");
      setOldP(""); setNewP(""); setConfirm("");
    },
    onError: (e: unknown) =>
      toast((e as { message?: string })?.message || "Could not update password", "error"),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (newP.length < 8) { toast("New password must be at least 8 characters", "error"); return; }
    if (newP !== confirm) { toast("Passwords do not match", "error"); return; }
    mut.mutate();
  }

  return (
    <div className="max-w-lg space-y-6">
      <PageHeader title="My Account" />
      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-gray-700">Change password</h2>
        </CardHeader>
        <CardBody>
          <p className="mb-4 text-xs text-gray-500">
            Signed in as {session?.user?.email}
            {role ? ` \u00b7 ${role.replace("_", " ")}` : ""}
          </p>
          <form onSubmit={submit} className="space-y-3">
            <Field label="Current password">
              <Input type="password" value={oldP} onChange={(e) => setOldP(e.target.value)} />
            </Field>
            <Field label="New password">
              <Input type="password" value={newP} onChange={(e) => setNewP(e.target.value)} />
            </Field>
            <Field label="Confirm new password">
              <Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </Field>
            <Button type="submit" disabled={mut.isPending}>
              {mut.isPending && <Spinner />} Update password
            </Button>
          </form>
          {role === "super_admin" && (
            <p className="mt-3 text-xs text-amber-600">
              Note: the super-admin password is configured on the server
              (DASHBOARD_ADMIN_PASSWORD) and is restored on restart. Change it there
              for a permanent update.
            </p>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
