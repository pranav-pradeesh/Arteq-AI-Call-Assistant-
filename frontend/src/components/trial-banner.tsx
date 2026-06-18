"use client";
import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useSession } from "next-auth/react";
import { AlertTriangle, Clock, BadgeCheck } from "lucide-react";
import { api } from "@/lib/api";
import { cn, fmtDateTime } from "@/lib/utils";
import { useCurrentHospital, useTrialStatus, useToast } from "./providers";
import { Button, Spinner } from "./ui";

/**
 * App-wide trial/subscription banner. Reads the selected hospital's trial status
 * (provided by ReadOnlyProvider) and renders:
 *   - trial  → days-remaining countdown (amber when ≤3 days left)
 *   - expired → red notice + Activate (super_admin only)
 *   - active → nothing
 */
export function TrialBanner() {
  const trial = useTrialStatus();
  const { hospitalId } = useCurrentHospital();
  const { data: session } = useSession();
  const role = (session?.user as { role?: string } | undefined)?.role;
  const qc = useQueryClient();
  const toast = useToast();

  const activate = useMutation({
    mutationFn: () => api.activateSubscription(hospitalId as string),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trial-status", hospitalId] });
      toast("Subscription activated");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  if (!trial || trial.subscription_status === "active") return null;

  const isExpired = trial.subscription_status === "expired";
  const days = trial.days_remaining ?? null;
  const lowDays = days != null && days <= 3;

  const tone = isExpired
    ? "border-red-200 bg-red-50 text-red-800"
    : lowDays
    ? "border-amber-200 bg-amber-50 text-amber-800"
    : "border-brand-200 bg-brand-50 text-brand-800";

  return (
    <div className={cn("mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border px-3 py-2 text-sm", tone)}>
      {isExpired ? <AlertTriangle className="h-4 w-4 shrink-0" /> : <Clock className="h-4 w-4 shrink-0" />}
      <span className="min-w-0 flex-1">
        {isExpired ? (
          <>
            <strong>Trial expired.</strong> The dashboard is read-only until the subscription is activated.
            {trial.trial_expires_at && <> Expired {fmtDateTime(trial.trial_expires_at)}.</>}
          </>
        ) : (
          <>
            <strong>Free trial.</strong>{" "}
            {days != null ? (
              <>{days} {days === 1 ? "day" : "days"} remaining</>
            ) : (
              "Trial active"
            )}
            {trial.trial_expires_at && <> · expires {fmtDateTime(trial.trial_expires_at)}</>}
          </>
        )}
      </span>
      {role === "super_admin" && hospitalId && (
        <Button
          variant="primary"
          allowInReadOnly
          className="px-2.5 py-1 text-xs"
          onClick={() => activate.mutate()}
          disabled={activate.isPending}
        >
          {activate.isPending ? <Spinner /> : <BadgeCheck className="h-3.5 w-3.5" />} Activate subscription
        </Button>
      )}
    </div>
  );
}
