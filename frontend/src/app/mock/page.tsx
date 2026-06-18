"use client";
import * as React from "react";
import Link from "next/link";
import {
  Stethoscope, Building2, ArrowRight, UserPlus, CalendarCheck,
  Phone, MessageCircle, QrCode, Ticket,
} from "lucide-react";
import { Card, CardBody } from "@/components/ui";

const FLOW = [
  { icon: UserPlus, label: "Add patient", note: "Auto patient ID" },
  { icon: Phone, label: "AI outbound call", note: "Welcome / confirm" },
  { icon: MessageCircle, label: "WhatsApp", note: "Tokens & updates" },
  { icon: CalendarCheck, label: "Schedule", note: "Calendar picker" },
  { icon: QrCode, label: "Pay now", note: "QR for reception" },
  { icon: Ticket, label: "Pay later", note: "Token + AI confirm" },
];

export default function MockHome() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Hospital workflow — mock preview</h1>
        <p className="mt-1 max-w-2xl text-sm text-gray-600">
          A clickable prototype of the patient intake and appointment flow. Every button works on
          in-browser demo data (nothing is sent anywhere). Use the Doctor dashboard to drive the
          flow, and the Hospital Admin dashboard to watch AI calls, WhatsApp messages and tokens
          update live.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Link href="/mock/doctor" className="group">
          <Card className="h-full transition group-hover:border-brand-400 group-hover:shadow-md">
            <CardBody className="flex h-full flex-col gap-3 p-6">
              <div className="inline-flex h-11 w-11 items-center justify-center rounded-lg bg-brand-50 text-brand-700">
                <Stethoscope className="h-6 w-6" />
              </div>
              <div>
                <h2 className="flex items-center gap-1 text-lg font-semibold text-gray-900">
                  Doctor Dashboard
                  <ArrowRight className="h-4 w-4 opacity-0 transition group-hover:translate-x-0.5 group-hover:opacity-100" />
                </h2>
                <p className="mt-1 text-sm text-gray-600">
                  Add patients, schedule appointments on the calendar, pick Pay Now / Pay Later,
                  and manage tokens.
                </p>
              </div>
            </CardBody>
          </Card>
        </Link>

        <Link href="/mock/admin" className="group">
          <Card className="h-full transition group-hover:border-brand-400 group-hover:shadow-md">
            <CardBody className="flex h-full flex-col gap-3 p-6">
              <div className="inline-flex h-11 w-11 items-center justify-center rounded-lg bg-brand-50 text-brand-700">
                <Building2 className="h-6 w-6" />
              </div>
              <div>
                <h2 className="flex items-center gap-1 text-lg font-semibold text-gray-900">
                  Hospital Admin Dashboard
                  <ArrowRight className="h-4 w-4 opacity-0 transition group-hover:translate-x-0.5 group-hover:opacity-100" />
                </h2>
                <p className="mt-1 text-sm text-gray-600">
                  Overview of patients, bookings and tokens, plus live AI call log and WhatsApp
                  message feed.
                </p>
              </div>
            </CardBody>
          </Card>
        </Link>
      </div>

      <Card>
        <CardBody className="p-6">
          <p className="mb-4 text-sm font-medium text-gray-700">The flow at a glance</p>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
            {FLOW.map((f) => {
              const Icon = f.icon;
              return (
                <div key={f.label} className="flex flex-col items-center text-center">
                  <div className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-gray-100 text-gray-700">
                    <Icon className="h-5 w-5" />
                  </div>
                  <p className="mt-2 text-xs font-medium text-gray-900">{f.label}</p>
                  <p className="text-[11px] text-gray-500">{f.note}</p>
                </div>
              );
            })}
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
