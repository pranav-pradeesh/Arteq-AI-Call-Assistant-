"use client";
import * as React from "react";
import { AlertTriangle } from "lucide-react";

interface TrialBannerProps {
  daysRemaining: number;
}

export function TrialBanner({ daysRemaining }: TrialBannerProps) {
  const isExpired = daysRemaining <= 0;
  return (
    <div className={`flex items-center gap-2 px-4 py-2 text-sm font-medium ${
      isExpired ? "bg-red-600 text-white" : "bg-amber-500 text-white"
    }`}>
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <span>
        {isExpired
          ? "Your free trial has expired. Contact support to activate your subscription."
          : `Free trial — ${daysRemaining} day${daysRemaining !== 1 ? "s" : ""} remaining. Contact support to upgrade.`}
      </span>
    </div>
  );
}
