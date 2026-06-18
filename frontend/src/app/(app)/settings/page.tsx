"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Hospital } from "@/lib/types";
import { RequireHospital } from "@/components/require-hospital";
import {
  PageHeader, Button, Field, Input, Select, Card, CardHeader, CardBody, Spinner,
} from "@/components/ui";
import { useToast } from "@/components/providers";

const DOW_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
type DowKey = typeof DOW_KEYS[number];
const DOW_LABELS_MAP: Record<DowKey, string> = {
  mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday",
  fri: "Friday", sat: "Saturday", sun: "Sunday",
};

type ProfileForm = {
  name: string;
  name_ml: string;
  phone: string;
  address: string;
  slug: string;
  tier: "hospital" | "clinic";
  agent_name: string;
};

type HoursState = Record<DowKey, { enabled: boolean; open: string; close: string }>;

function buildHoursState(hours?: Record<string, [string, string]> | null): HoursState {
  const defaults: HoursState = {} as HoursState;
  for (const k of DOW_KEYS) {
    const h = hours?.[k];
    defaults[k] = h
      ? { enabled: true, open: h[0], close: h[1] }
      : { enabled: false, open: "08:00", close: "17:00" };
  }
  return defaults;
}

function hoursStateToRecord(h: HoursState): Record<string, [string, string]> {
  const out: Record<string, [string, string]> = {};
  for (const k of DOW_KEYS) {
    if (h[k].enabled) out[k] = [h[k].open, h[k].close];
  }
  return out;
}

function Inner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();

  const { data: hospital, isLoading } = useQuery({
    queryKey: ["hospital", hospitalId],
    queryFn: () => api.getHospital(hospitalId),
  });

  const [profile, setProfile] = React.useState<ProfileForm>({
    name: "", name_ml: "", phone: "", address: "", slug: "", tier: "hospital", agent_name: "",
  });
  const [hours, setHours] = React.useState<HoursState>(buildHoursState(null));
  const [initialized, setInitialized] = React.useState(false);

  React.useEffect(() => {
    if (hospital && !initialized) {
      setProfile({
        name: hospital.name ?? "",
        name_ml: hospital.name_ml ?? "",
        phone: hospital.phone ?? "",
        address: hospital.address ?? "",
        slug: hospital.slug ?? "",
        tier: hospital.tier ?? "hospital",
        agent_name: hospital.agent_name ?? "",
      });
      setHours(buildHoursState(hospital.hours));
      setInitialized(true);
    }
  }, [hospital, initialized]);

  const setP = (k: keyof ProfileForm) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setProfile((f) => ({ ...f, [k]: e.target.value }));

  const saveMut = useMutation({
    mutationFn: () =>
      api.updateHospital(hospitalId, {
        ...profile,
        hours: hoursStateToRecord(hours),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hospital", hospitalId] });
      qc.invalidateQueries({ queryKey: ["hospitals"] });
      toast("Settings saved", "ok");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Spinner className="h-6 w-6 text-gray-400" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" />

      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-gray-700">Hospital Profile</h2>
        </CardHeader>
        <CardBody>
          <form
            id="settings-form"
            onSubmit={(e) => { e.preventDefault(); saveMut.mutate(); }}
            className="grid gap-4 sm:grid-cols-2"
          >
            <Field label="Name *">
              <Input value={profile.name} onChange={setP("name")} required />
            </Field>
            <Field label="Name (Malayalam)">
              <Input value={profile.name_ml} onChange={setP("name_ml")} />
            </Field>
            <Field label="Phone">
              <Input value={profile.phone} onChange={setP("phone")} type="tel" />
            </Field>
            <Field label="Slug">
              <Input value={profile.slug} onChange={setP("slug")} />
            </Field>
            <Field label="Address">
              <Input value={profile.address} onChange={setP("address")} className="col-span-full" />
            </Field>
            <Field label="Tier">
              <Select value={profile.tier} onChange={setP("tier")}>
                <option value="hospital">Hospital</option>
                <option value="clinic">Clinic</option>
              </Select>
            </Field>
            <Field label="Agent Name">
              <Input value={profile.agent_name} onChange={setP("agent_name")} />
            </Field>
          </form>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-gray-700">Weekly Hours</h2>
        </CardHeader>
        <CardBody>
          <div className="space-y-3">
            {DOW_KEYS.map((k) => {
              const day = hours[k];
              return (
                <div key={k} className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    id={`dow-${k}`}
                    checked={day.enabled}
                    onChange={(e) =>
                      setHours((h) => ({ ...h, [k]: { ...h[k], enabled: e.target.checked } }))
                    }
                    className="h-4 w-4 rounded border-gray-300 text-blue-600"
                  />
                  <label htmlFor={`dow-${k}`} className="w-24 text-sm font-medium text-gray-700">
                    {DOW_LABELS_MAP[k]}
                  </label>
                  {day.enabled ? (
                    <div className="flex items-center gap-2">
                      <Input
                        type="time"
                        value={day.open}
                        onChange={(e) =>
                          setHours((h) => ({ ...h, [k]: { ...h[k], open: e.target.value } }))
                        }
                        className="w-32"
                      />
                      <span className="text-sm text-gray-500">to</span>
                      <Input
                        type="time"
                        value={day.close}
                        onChange={(e) =>
                          setHours((h) => ({ ...h, [k]: { ...h[k], close: e.target.value } }))
                        }
                        className="w-32"
                      />
                    </div>
                  ) : (
                    <span className="text-sm text-gray-400">Closed</span>
                  )}
                </div>
              );
            })}
          </div>
        </CardBody>
      </Card>

      <div className="flex justify-end">
        <Button
          form="settings-form"
          type="submit"
          disabled={saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          {saveMut.isPending && <Spinner />} Save Settings
        </Button>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <RequireHospital>
      {(hid) => <Inner hospitalId={hid} />}
    </RequireHospital>
  );
}
