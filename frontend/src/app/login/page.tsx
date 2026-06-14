"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";
import { Button, Card, CardBody, Input, Label, Spinner } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const res = await signIn("credentials", { email, password, redirect: false });
    setLoading(false);
    if (res?.error || !res?.ok) {
      setError("Invalid email or password");
      return;
    }
    router.push("/overview");
    router.refresh();
  }

  return (
    <div className="grid min-h-screen place-items-center bg-gray-50 p-4">
      <Card className="w-full max-w-sm">
        <CardBody>
          <div className="mb-5 flex items-center gap-2">
            <span className="grid h-8 w-8 place-items-center rounded-md bg-brand-600 font-semibold text-white">A</span>
            <div>
              <p className="font-semibold">Arteq Admin</p>
              <p className="text-xs text-gray-500">Hospital Voice Agent dashboard</p>
            </div>
          </div>
          <form onSubmit={onSubmit} className="space-y-3">
            <div>
              <Label>Email</Label>
              <Input
                type="email"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@hospital.com"
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
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
