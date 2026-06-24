"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { signIn, getSession } from "next-auth/react";
import { Button, Card, CardBody, Input, Label, Spinner } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [showForgot, setShowForgot] = React.useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const res = await signIn("credentials", { email, password, redirect: false });
    setLoading(false);
    if (res?.error || !res?.ok) {
      setError("Invalid username or password");
      return;
    }
    // Doctors get their own self-service dashboard; everyone else lands on the
    // admin overview. The role rides in the freshly-issued session.
    const session = await getSession();
    const role = (session?.user as { role?: string } | undefined)?.role;
    router.push(role === "doctor" ? "/doctor" : "/overview");
    router.refresh();
  }

  return (
    <div className="grid min-h-screen place-items-center bg-gray-50 p-4">
      <Card className="w-full max-w-sm">
        <CardBody>
          <div className="mb-5 flex items-center gap-2">
            <img src="/logo.svg" alt="Arteq" className="h-10 w-10 object-contain" />
            <div>
              <p className="font-semibold">Arteq Admin</p>
              <p className="text-xs text-gray-500">Hospital Voice Agent dashboard</p>
            </div>
          </div>
          <form onSubmit={onSubmit} className="space-y-3">
            <div>
              <Label>Username</Label>
              <Input
                type="text"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="superadmin"
              />
            </div>
            <div>
              <Label>Password</Label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading && <Spinner />} Sign in
            </Button>
            <button
              type="button"
              onClick={() => setShowForgot((s) => !s)}
              className="w-full text-center text-xs text-gray-500 hover:underline"
            >
              Forgot password?
            </button>
            {showForgot && (
              <p className="rounded-md bg-gray-50 p-2 text-center text-xs text-gray-500">
                Contact your administrator to reset your password. Hospital admins:
                ask your Arteq super-admin to set a new password from the Users page.
              </p>
            )}
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
