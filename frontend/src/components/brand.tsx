"use client";
import * as React from "react";
import { gsap, useIsomorphicLayoutEffect } from "@/lib/gsap";
import { cn } from "@/lib/utils";

/** The Arteq brand mark (shared SVG used for logo + favicon). */
export const BrandLogo = React.forwardRef<HTMLImageElement, { className?: string }>(
  function BrandLogo({ className }, ref) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img ref={ref} src="/logo.svg" alt="Arteq" className={cn("object-contain", className)} />;
  }
);

/**
 * Full-screen branded loading animation. The brand mark scales/pulses while an
 * SVG ring spins+breathes and a heartbeat line draws underneath — all driven by
 * a single looping GSAP timeline.
 */
export function AppLoader({
  label = "Loading",
  className,
}: {
  label?: string;
  className?: string;
}) {
  const root = React.useRef<HTMLDivElement>(null);
  const logo = React.useRef<HTMLImageElement>(null);
  const ring = React.useRef<SVGCircleElement>(null);
  const beat = React.useRef<SVGPathElement>(null);

  useIsomorphicLayoutEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from(root.current, { opacity: 0, duration: 0.35, ease: "power1.out" });

      // Brand mark: pop in, then breathe.
      gsap.fromTo(
        logo.current,
        { scale: 0.8, opacity: 0 },
        { scale: 1, opacity: 1, duration: 0.6, ease: "back.out(1.7)" }
      );
      gsap.to(logo.current, {
        scale: 1.07,
        duration: 1,
        ease: "sine.inOut",
        yoyo: true,
        repeat: -1,
      });

      // Ring: continuous spin + breathing arc length.
      gsap.to(ring.current, {
        rotation: 360,
        svgOrigin: "60 60",
        duration: 1.4,
        ease: "none",
        repeat: -1,
      });
      gsap.fromTo(
        ring.current,
        { strokeDashoffset: 300 },
        { strokeDashoffset: 90, duration: 1.1, ease: "sine.inOut", yoyo: true, repeat: -1 }
      );

      // Heartbeat line: draw across, repeatedly.
      const len = beat.current?.getTotalLength() ?? 200;
      gsap.set(beat.current, { strokeDasharray: len, strokeDashoffset: len });
      gsap.to(beat.current, {
        strokeDashoffset: -len,
        duration: 1.8,
        ease: "power1.inOut",
        repeat: -1,
      });
    }, root);
    return () => ctx.revert();
  }, []);

  return (
    <div
      ref={root}
      className={cn(
        "grid min-h-screen place-items-center bg-gray-50",
        className
      )}
    >
      <div className="flex flex-col items-center gap-5">
        <div className="relative grid h-[120px] w-[120px] place-items-center">
          <svg viewBox="0 0 120 120" className="absolute inset-0 h-full w-full">
            <circle cx="60" cy="60" r="54" fill="none" stroke="#e5e7eb" strokeWidth="3" />
            <circle
              ref={ring}
              cx="60"
              cy="60"
              r="54"
              fill="none"
              stroke="#403db2"
              strokeWidth="3"
              strokeLinecap="round"
              strokeDasharray="120 300"
            />
          </svg>
          <BrandLogo ref={logo} className="h-14 w-14" />
        </div>

        <svg viewBox="0 0 200 40" className="h-8 w-40">
          <path
            ref={beat}
            d="M0 20 H60 L72 6 L88 34 L104 20 H200"
            fill="none"
            stroke="#403db2"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>

        <p className="text-sm font-medium text-gray-500">{label}…</p>
      </div>
    </div>
  );
}
