"use client";
import * as React from "react";

/**
 * Deprecated. Lenis smooth-scroll was removed because it hijacked the window
 * scroll and conflicted with the dashboard's fixed shell, nested scroll areas
 * (sidebar, tables) and modals — causing scroll glitches on desktop and mobile.
 * The app now uses native scrolling. This passthrough is kept only so any stray
 * import keeps compiling; it can be deleted.
 */
export function SmoothScroll({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
