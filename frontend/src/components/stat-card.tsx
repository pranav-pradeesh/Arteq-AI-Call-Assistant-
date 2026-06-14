"use client";
import * as React from "react";
import { Card, CardBody } from "@/components/ui";
import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: string;
  delta?: number;
  hint?: string;
}

export function StatCard({ label, value, delta, hint }: StatCardProps) {
  const hasDelta = delta != null && !isNaN(delta);
  const positive = hasDelta && delta! >= 0;

  return (
    <Card className="flex-1 min-w-[160px]">
      <CardBody className="flex flex-col gap-1 py-5 px-5">
        <span className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</span>
        <span className="text-3xl font-bold text-gray-900 leading-tight">{value}</span>
        {hasDelta && (
          <span
            className={cn(
              "inline-flex items-center gap-0.5 text-sm font-medium",
              positive ? "text-green-600" : "text-red-600"
            )}
          >
            {positive ? "↑" : "↓"}
            {Math.abs(delta!).toFixed(1)}%
          </span>
        )}
        {hint && <span className="text-xs text-gray-400 mt-0.5">{hint}</span>}
      </CardBody>
    </Card>
  );
}
