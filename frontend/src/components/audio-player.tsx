"use client";
import * as React from "react";
import { AudioLines } from "lucide-react";
import { getSession, useSession } from "next-auth/react";

/**
 * Call-recording player. `src` is the dashboard API path for the recording; we
 * fetch it WITH the auth token (so a plain <audio src> can't leak it) and play
 * the blob. Admin-only and tenant-scoped server-side.
 */
export function RecordingPlayer({ src }: { src?: string | null }) {
  const { data: session } = useSession();
  const isSuper = (session?.user as { role?: string } | undefined)?.role === "super_admin";
  const [url, setUrl] = React.useState<string | null>(null);
  const [err, setErr] = React.useState(false);

  React.useEffect(() => {
    if (!src || isSuper) return;
    let alive = true;
    let objectUrl: string | null = null;
    (async () => {
      try {
        const s = await getSession();
        const token = (s as { accessToken?: string } | null)?.accessToken;
        const res = await fetch(src, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) throw new Error(String(res.status));
        const blob = await res.blob();
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      } catch {
        if (alive) setErr(true);
      }
    })();
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [src, isSuper]);

  if (isSuper) {
    return (
      <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-500">
        Call recordings are available to the hospital admin only.
      </div>
    );
  }
  if (!src || err) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-dashed border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-500">
        <AudioLines className="h-4 w-4 shrink-0 text-gray-400" />
        Recording not available for this call yet.
      </div>
    );
  }
  if (!url) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-dashed border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-500">
        <AudioLines className="h-4 w-4 shrink-0 text-gray-400 animate-pulse" />
        Loading recording…
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <AudioLines className="h-4 w-4 shrink-0 text-brand-600" />
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio controls preload="none" src={url} className="h-9 w-full max-w-md">
        Your browser does not support audio playback.
      </audio>
    </div>
  );
}
