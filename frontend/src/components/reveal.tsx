"use client";
import * as React from "react";
import { usePathname } from "next/navigation";
import { gsap, useIsomorphicLayoutEffect } from "@/lib/gsap";

/**
 * Fades + lifts its children into view with GSAP. Re-runs on route change so
 * each page gets a subtle entrance. Respects prefers-reduced-motion.
 */
export function Reveal({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  const pathname = usePathname();

  useIsomorphicLayoutEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const ctx = gsap.context(() => {
      gsap.from(ref.current, {
        opacity: 0,
        y: 14,
        duration: 0.45,
        ease: "power2.out",
      });
    }, ref);
    return () => ctx.revert();
  }, [pathname]);

  return (
    <div ref={ref} className={className}>
      {children}
    </div>
  );
}
