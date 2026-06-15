"use client";
import * as React from "react";

// ──────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────
export type Patient = {
  id: string;
  name: string;
  phone: string;
  createdAt: string;
};

export type PaymentMode = "pay_now" | "pay_later";

export type BookingStatus =
  | "pending_payment" // Pay Now: QR generated, awaiting scan + pay
  | "awaiting_confirmation" // Pay Later: token issued but inactive
  | "confirmed" // paid (Pay Now) or token activated (Pay Later)
  | "cancelled";

export type Token = {
  code: string;
  active: boolean;
};

export type Booking = {
  id: string;
  patientId: string;
  patientName: string;
  patientPhone: string;
  slot: string; // ISO datetime
  paymentMode: PaymentMode;
  status: BookingStatus;
  amountPaise: number;
  token: Token | null;
  createdAt: string;
};

export type WhatsAppMessage = {
  id: string;
  phone: string;
  patientName: string;
  body: string;
  at: string;
};

export type CallLog = {
  id: string;
  phone: string;
  patientName: string;
  purpose: string;
  outcome: string;
  at: string;
};

type State = {
  patients: Patient[];
  bookings: Booking[];
  whatsapp: WhatsAppMessage[];
  calls: CallLog[];
};

type Store = State & {
  addPatient: (name: string, phone: string) => Patient;
  previewPatientId: () => string;
  addBooking: (input: {
    patientId: string;
    slot: string;
    paymentMode: PaymentMode;
    amountPaise: number;
  }) => Booking;
  markPaid: (bookingId: string) => void; // Pay Now → confirm instantly
  runConfirmationCall: (bookingId: string) => void; // AI call → activate token
  changeToken: (bookingId: string) => void; // re-issue token + notify
  cancelBooking: (bookingId: string) => void;
  reset: () => void;
};

// ──────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────
const STORAGE_KEY = "arteq_mock_v1";

function yymmdd(d = new Date()): string {
  return [
    String(d.getFullYear()).slice(2),
    String(d.getMonth() + 1).padStart(2, "0"),
    String(d.getDate()).padStart(2, "0"),
  ].join("");
}

function rid(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
}

function genToken(): string {
  const n = Math.floor(1000 + Math.random() * 9000);
  return `TKN-${n}`;
}

function now(): string {
  return new Date().toISOString();
}

const SEED: State = {
  patients: [],
  bookings: [],
  whatsapp: [],
  calls: [],
};

// ──────────────────────────────────────────────────────────────────────────
// Context
// ──────────────────────────────────────────────────────────────────────────
const Ctx = React.createContext<Store | null>(null);

export function useMockStore(): Store {
  const ctx = React.useContext(Ctx);
  if (!ctx) throw new Error("useMockStore must be used inside <MockStoreProvider>");
  return ctx;
}

export function MockStoreProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState<State>(SEED);

  // Hydrate from localStorage so Doctor + Admin dashboards share data and
  // a reload keeps the demo state.
  React.useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) setState(JSON.parse(raw));
    } catch {
      /* ignore corrupt storage */
    }
  }, []);

  // Persist + keep tabs in sync.
  const persist = React.useCallback((next: State) => {
    setState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* ignore quota errors */
    }
  }, []);

  React.useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY && e.newValue) {
        try {
          setState(JSON.parse(e.newValue));
        } catch {
          /* ignore */
        }
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const sendWhatsApp = (s: State, phone: string, patientName: string, body: string): State => ({
    ...s,
    whatsapp: [
      { id: rid("wa"), phone, patientName, body, at: now() },
      ...s.whatsapp,
    ],
  });

  const logCall = (
    s: State,
    phone: string,
    patientName: string,
    purpose: string,
    outcome: string
  ): State => ({
    ...s,
    calls: [{ id: rid("call"), phone, patientName, purpose, outcome, at: now() }, ...s.calls],
  });

  const previewPatientId = React.useCallback((): string => {
    const todays = state.patients.filter((p) => p.id.includes(yymmdd())).length;
    return `P-${yymmdd()}-${String(todays + 1).padStart(3, "0")}`;
  }, [state.patients]);

  const addPatient = React.useCallback(
    (name: string, phone: string): Patient => {
      const id = previewPatientId();
      const patient: Patient = { id, name, phone, createdAt: now() };
      let next: State = { ...state, patients: [patient, ...state.patients] };
      // Patient addition connects to AI agent (outbound welcome call) + WhatsApp.
      next = logCall(
        next,
        phone,
        name,
        "Welcome / onboarding",
        "AI agent placed outbound welcome call · introduced clinic"
      );
      next = sendWhatsApp(
        next,
        phone,
        name,
        `Hi ${name}, welcome to Arteq Care 🏥. Your patient ID is ${id}. Save this message — we'll use it for your appointments.`
      );
      persist(next);
      return patient;
    },
    [state, previewPatientId, persist]
  );

  const addBooking = React.useCallback(
    (input: {
      patientId: string;
      slot: string;
      paymentMode: PaymentMode;
      amountPaise: number;
    }): Booking => {
      const patient = state.patients.find((p) => p.id === input.patientId);
      if (!patient) throw new Error("Patient not found");

      const base: Booking = {
        id: rid("appt"),
        patientId: patient.id,
        patientName: patient.name,
        patientPhone: patient.phone,
        slot: input.slot,
        paymentMode: input.paymentMode,
        amountPaise: input.amountPaise,
        status: input.paymentMode === "pay_now" ? "pending_payment" : "awaiting_confirmation",
        token: input.paymentMode === "pay_later" ? { code: genToken(), active: false } : null,
        createdAt: now(),
      };

      let next: State = { ...state, bookings: [base, ...state.bookings] };

      if (input.paymentMode === "pay_later" && base.token) {
        // Path B: issue an inactive token + send it on WhatsApp.
        next = sendWhatsApp(
          next,
          patient.phone,
          patient.name,
          `Your appointment is reserved for ${new Date(input.slot).toLocaleString("en-IN", {
            dateStyle: "medium",
            timeStyle: "short",
          })}. Provisional token: ${base.token.code} (inactive). Our AI assistant will call you ~1 week before to confirm.`
        );
      }
      persist(next);
      return base;
    },
    [state, persist]
  );

  const markPaid = React.useCallback(
    (bookingId: string) => {
      const b = state.bookings.find((x) => x.id === bookingId);
      if (!b) return;
      let next: State = {
        ...state,
        bookings: state.bookings.map((x) =>
          x.id === bookingId ? { ...x, status: "confirmed" as BookingStatus } : x
        ),
      };
      next = sendWhatsApp(
        next,
        b.patientPhone,
        b.patientName,
        `Payment received ✅. Your appointment on ${new Date(b.slot).toLocaleString("en-IN", {
          dateStyle: "medium",
          timeStyle: "short",
        })} is confirmed. See you soon!`
      );
      persist(next);
    },
    [state, persist]
  );

  const runConfirmationCall = React.useCallback(
    (bookingId: string) => {
      const b = state.bookings.find((x) => x.id === bookingId);
      if (!b || !b.token) return;
      let next: State = {
        ...state,
        bookings: state.bookings.map((x) =>
          x.id === bookingId
            ? { ...x, status: "confirmed" as BookingStatus, token: { ...x.token!, active: true } }
            : x
        ),
      };
      next = logCall(
        next,
        b.patientPhone,
        b.patientName,
        "Appointment confirmation (1 week prior)",
        `AI referenced token ${b.token.code} · patient confirmed · token activated`
      );
      next = sendWhatsApp(
        next,
        b.patientPhone,
        b.patientName,
        `Thanks for confirming! Token ${b.token.code} is now ACTIVE ✅. Pay at the clinic on the day of your appointment.`
      );
      persist(next);
    },
    [state, persist]
  );

  const changeToken = React.useCallback(
    (bookingId: string) => {
      const b = state.bookings.find((x) => x.id === bookingId);
      if (!b || !b.token) return;
      const newCode = genToken();
      let next: State = {
        ...state,
        bookings: state.bookings.map((x) =>
          x.id === bookingId
            ? { ...x, token: { code: newCode, active: x.token!.active } }
            : x
        ),
      };
      next = sendWhatsApp(
        next,
        b.patientPhone,
        b.patientName,
        `Your token has been changed. Old: ${b.token.code} → New: ${newCode}. Please use the new token for your visit.`
      );
      persist(next);
    },
    [state, persist]
  );

  const cancelBooking = React.useCallback(
    (bookingId: string) => {
      const b = state.bookings.find((x) => x.id === bookingId);
      if (!b) return;
      let next: State = {
        ...state,
        bookings: state.bookings.map((x) =>
          x.id === bookingId ? { ...x, status: "cancelled" as BookingStatus } : x
        ),
      };
      next = sendWhatsApp(
        next,
        b.patientPhone,
        b.patientName,
        `Your appointment on ${new Date(b.slot).toLocaleString("en-IN", {
          dateStyle: "medium",
          timeStyle: "short",
        })} has been cancelled. Reply to reschedule.`
      );
      persist(next);
    },
    [state, persist]
  );

  const reset = React.useCallback(() => persist(SEED), [persist]);

  const value: Store = {
    ...state,
    addPatient,
    previewPatientId,
    addBooking,
    markPaid,
    runConfirmationCall,
    changeToken,
    cancelBooking,
    reset,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
