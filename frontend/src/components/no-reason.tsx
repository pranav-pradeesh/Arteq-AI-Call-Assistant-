"use client";
import * as React from "react";

// Random playful rejection, vendored from no-as-a-service (MIT, hotheadhacker).
// Self-hosted at /no-reasons.json — no external API call at runtime.
export function NoReason({ className }: { className?: string }) {
  const [reason, setReason] = React.useState<string>("");
  React.useEffect(() => {
    let alive = true;
    fetch("/no-reasons.json")
      .then((r) => r.json())
      .then((list: string[]) => {
        if (alive && Array.isArray(list) && list.length) {
          setReason(list[Math.floor(Math.random() * list.length)]);
        }
      })
      .catch(() => { /* silent — the page still works without a quip */ });
    return () => { alive = false; };
  }, []);
  if (!reason) return null;
  return <p className={className}>&ldquo;{reason}&rdquo;</p>;
}
