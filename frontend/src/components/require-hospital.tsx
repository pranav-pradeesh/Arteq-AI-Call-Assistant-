"use client";
import * as React from "react";
import { useCurrentHospital } from "./providers";
import { EmptyState } from "./ui";

/**
 * Wrap per-hospital pages. Renders children with the selected hospital id,
 * or a prompt to pick one from the top-bar switcher.
 */
export function RequireHospital({ children }: { children: (hospitalId: string) => React.ReactNode }) {
  const { hospitalId } = useCurrentHospital();
  if (!hospitalId) {
    return <EmptyState title="No hospital selected" hint="Pick a hospital from the switcher above to continue." />;
  }
  return <>{children(hospitalId)}</>;
}
