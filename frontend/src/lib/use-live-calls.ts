"use client";
import * as React from "react";
import { getSession } from "next-auth/react";
import { api } from "@/lib/api";
import type { CallLog } from "@/lib/types";

export type LiveStatus = "connecting" | "live" | "polling" | "error";

type ServerEvent =
  | { type: "snapshot"; calls: CallLog[] }
  | { type: "call_started"; call: CallLog }
  | { type: "call_updated"; call: CallLog }
  | { type: "call_ended"; call_id: string }
  | { type: "ping" };

const keyOf = (c: CallLog) => c.call_id ?? c.id;

/**
 * Real-time active-call feed. Connects to the backend WebSocket
 * (/admin/ws/live) and applies lifecycle events; if the socket can't be
 * established or drops, falls back to polling /admin/hospitals/{id}/active-calls
 * every 5s. Returns the current active calls and a connection status.
 */
export function useLiveCalls(hospitalId: string): { calls: CallLog[]; status: LiveStatus } {
  const [calls, setCalls] = React.useState<CallLog[]>([]);
  const [status, setStatus] = React.useState<LiveStatus>("connecting");

  React.useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let openTimeout: ReturnType<typeof setTimeout> | null = null;

    const apiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
    const wsBase = apiBase.replace(/^http/, "ws");

    function startPolling() {
      if (cancelled || pollTimer) return;
      setStatus((s) => (s === "live" ? "polling" : "polling"));
      const tick = async () => {
        try {
          const d = await api.activeCalls(hospitalId);
          if (!cancelled) {
            setCalls(d);
            setStatus("polling");
          }
        } catch {
          if (!cancelled) setStatus("error");
        }
      };
      void tick();
      pollTimer = setInterval(tick, 5_000);
    }

    function applyEvent(msg: ServerEvent) {
      if (cancelled) return;
      switch (msg.type) {
        case "snapshot":
          setCalls(msg.calls ?? []);
          break;
        case "call_started":
        case "call_updated":
          setCalls((prev) => {
            const i = prev.findIndex((x) => keyOf(x) === keyOf(msg.call));
            if (i === -1) return [msg.call, ...prev];
            const next = prev.slice();
            next[i] = { ...next[i], ...msg.call };
            return next;
          });
          break;
        case "call_ended":
          setCalls((prev) => prev.filter((x) => keyOf(x) !== msg.call_id));
          break;
        case "ping":
        default:
          break;
      }
    }

    async function connect() {
      let token: string | undefined;
      try {
        token = (await getSession())?.accessToken;
      } catch {
        /* ignore — connect anyway, server will reject if needed */
      }
      if (cancelled) return;

      try {
        ws = new WebSocket(
          `${wsBase}/admin/ws/live?hospital_id=${encodeURIComponent(hospitalId)}&token=${encodeURIComponent(token ?? "")}`
        );
      } catch {
        startPolling();
        return;
      }

      // If the socket doesn't open promptly, fall back to polling.
      openTimeout = setTimeout(() => {
        if (ws && ws.readyState !== WebSocket.OPEN) ws.close();
      }, 4_000);

      ws.onopen = () => {
        if (openTimeout) clearTimeout(openTimeout);
        if (!cancelled) setStatus("live");
      };
      ws.onmessage = (ev) => {
        let msg: ServerEvent;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        applyEvent(msg);
      };
      ws.onerror = () => {
        /* close handler will trigger fallback */
      };
      ws.onclose = () => {
        if (openTimeout) clearTimeout(openTimeout);
        if (!cancelled) startPolling();
      };
    }

    void connect();

    return () => {
      cancelled = true;
      if (openTimeout) clearTimeout(openTimeout);
      if (pollTimer) clearInterval(pollTimer);
      if (ws) {
        ws.onclose = null; // prevent fallback-after-unmount
        ws.onmessage = null;
        ws.onerror = null;
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    };
  }, [hospitalId]);

  return { calls, status };
}
