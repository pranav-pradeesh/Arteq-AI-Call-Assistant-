"use client";
import * as React from "react";
import { cn } from "@/lib/utils";

type DivProps = React.HTMLAttributes<HTMLDivElement>;

export function Card({ className, ...p }: DivProps) {
  return <div className={cn("card", className)} {...p} />;
}
export function CardBody({ className, ...p }: DivProps) {
  return <div className={cn("p-4", className)} {...p} />;
}
export function CardHeader({ className, ...p }: DivProps) {
  return <div className={cn("border-b border-gray-100 px-4 py-3", className)} {...p} />;
}

export function Button({
  className, variant = "primary", ...p
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger" | "outline";
}) {
  const v = {
    primary: "btn-primary", ghost: "btn-ghost", danger: "btn-danger", outline: "btn-outline",
  }[variant];
  return <button className={cn(v, className)} {...p} />;
}

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...p }, ref) {
    return <input ref={ref} className={cn("input", className)} {...p} />;
  }
);

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...p }, ref) {
    return <textarea ref={ref} className={cn("input", className)} {...p} />;
  }
);

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, ...p }, ref) {
    return <select ref={ref} className={cn("input", className)} {...p} />;
  }
);

export function Label({ className, ...p }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("label", className)} {...p} />;
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Label>{label}</Label>
      {children}
    </div>
  );
}

const BADGE_TONES: Record<string, string> = {
  gray: "bg-gray-100 text-gray-700",
  green: "bg-green-100 text-green-700",
  red: "bg-red-100 text-red-700",
  yellow: "bg-yellow-100 text-yellow-800",
  blue: "bg-brand-100 text-brand-700",
};
export function Badge({ tone = "gray", className, ...p }: DivProps & { tone?: keyof typeof BADGE_TONES }) {
  return <span className={cn("badge", BADGE_TONES[tone], className)} {...(p as React.HTMLAttributes<HTMLSpanElement>)} />;
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cn("h-4 w-4 animate-spin text-current", className)} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="py-12 text-center text-sm text-gray-400">
      <p className="font-medium text-gray-500">{title}</p>
      {hint && <p className="mt-1">{hint}</p>}
    </div>
  );
}

export function PageHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between">
      <h1 className="text-lg font-semibold text-gray-900">{title}</h1>
      {action}
    </div>
  );
}
