"use client";
import * as React from "react";
import { AudioLines } from "lucide-react";

/**
 * Call-recording player. Renders a native audio player when a recording URL is
 * present; otherwise shows a clear "not available yet" note. Built ready for the
 * backend to populate `call_logs.recording_url` (see backend spec) — no change
 * needed here once recordings are stored.
 */
export function RecordingPlayer({ src }: { src?: string | null }) {
  if (!src) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-dashed border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-500">
        <AudioLines className="h-4 w-4 shrink-0 text-gray-400" />
        Recording not available for this call yet.
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <AudioLines className="h-4 w-4 shrink-0 text-brand-600" />
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio controls preload="none" src={src} className="h-9 w-full max-w-md">
        Your browser does not support audio playback.
      </audio>
    </div>
  );
}
