"use client";
import * as React from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { Button, Spinner } from "./ui";

export function Modal({
  open, onClose, title, children, footer, wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  wide?: boolean;
}) {
  // Portal to <body> so the overlay escapes any transformed ancestor (the GSAP
  // Reveal wrapper). A position:fixed element inside a transformed box is
  // positioned relative to that box, which trapped the modal "inside a frame".
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => { setMounted(true); }, []);
  if (!open || !mounted) return null;
  const node = (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:items-center sm:p-8">
      <div
        className={`flex max-h-[calc(100dvh-2rem)] w-full ${wide ? "max-w-3xl" : "max-w-lg"} flex-col overflow-hidden rounded-xl bg-white shadow-xl sm:max-h-[calc(100dvh-4rem)]`}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-gray-100 px-5 py-3">
          <h2 className="font-semibold text-gray-900">{title}</h2>
          <button onClick={onClose} className="rounded p-1 text-gray-400 hover:bg-gray-100">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex shrink-0 justify-end gap-2 border-t border-gray-100 px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
  return createPortal(node, document.body);
}

/** Modal wired for a form submit, with save/cancel + pending state. */
export function FormModal({
  open, onClose, title, onSubmit, saving, children, wide, submitLabel = "Save",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  onSubmit: (e: React.FormEvent) => void;
  saving?: boolean;
  children: React.ReactNode;
  wide?: boolean;
  submitLabel?: string;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      wide={wide}
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" form="modal-form" disabled={saving}>
            {saving && <Spinner />} {submitLabel}
          </Button>
        </>
      }
    >
      <form id="modal-form" onSubmit={onSubmit} className="space-y-3">
        {children}
      </form>
    </Modal>
  );
}
