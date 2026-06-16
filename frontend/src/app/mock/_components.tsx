"use client";
// CalendarPicker + FakeQR moved to a shared location so the mock and the real
// patient-intake pages use one implementation. Re-exported here to keep the
// mock's existing imports (`../_components`) working.
export { CalendarPicker, FakeQR } from "@/components/scheduling";
