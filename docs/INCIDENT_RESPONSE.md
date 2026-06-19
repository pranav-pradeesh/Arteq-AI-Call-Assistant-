# Incident Response & Status Runbook

> Operational procedure for detecting, responding to, and communicating service
> incidents and personal-data breaches. Supports the SLA (`SLA.md`) and the DPDP 72-hour
> breach-notification obligation (`compliance/DATA_PROCESSING_AGREEMENT.md` §10).

---

## 1. Severity definitions

| Sev | Trigger | Examples |
|---|---|---|
| **S1** | Patient-impacting outage / safety | Calls not answered; emergency routing failing; data breach |
| **S2** | Major degradation | High latency; one language/feature down; booking failures |
| **S3** | Minor | Dashboard glitch; single non-critical feature |

## 2. Detection

- **Automated:** alert on `/api/v1/health` component failures, error-rate spikes and
  latency regressions in `/metrics`, and call-volume drop-to-zero (a strong outage
  signal). **[Configure]** alerting (e.g. Prometheus Alertmanager / hosted monitor).
- **Manual:** hospital report via the S1 hotline / support channel.

## 3. Response flow

```
Detect ──> Triage (assign severity + owner)
       ──> Mitigate (failover / rollback / restart)
       ──> Communicate (status page + hospital contact)
       ──> Resolve & verify (health + test call)
       ──> Post-incident review (S1/S2 within 5 business days)
```

### First-response actions by symptom
| Symptom | First checks | Mitigation |
|---|---|---|
| Agent not answering | LiveKit creds on agent; `worker_registered` in logs; `NODE_IP` (self-host) | Restart agent worker; verify SIP dispatch |
| High latency | `latency_avg_ms`; provider status (Sarvam/Gemini) | Confirm LLM fallback engaged; scale workers |
| Empty transcripts | `SARVAM_API_KEY`; VAD events | Pin `SARVAM_STT_LANGUAGE`; check provider |
| Bookings failing | DB connectivity; migration state | Restore DB connectivity; check `db_migration_failed` |
| WhatsApp failing | `whatsapp_failed` events; template approval | Re-check token/template; degrade gracefully |

## 4. Personal-data breach procedure (DPDP)

1. **Contain** — revoke compromised credentials/tokens, isolate affected component.
2. **Assess** — what data, how many principals, likely consequences.
3. **Notify the hospital (Data Fiduciary) within 72 hours** of awareness, with nature,
   scope, consequences and remediation. The hospital handles principal/Board
   notifications as required.
4. **Remediate** — patch root cause; rotate secrets (`DASHBOARD_JWT_SECRET`,
   `INTERNAL_API_KEY`, provider keys).
5. **Document** — full timeline and corrective actions; feed into post-incident review.

## 5. Communication

- **Status page** updated at detection, on mitigation, and at resolution. **[Configure]**
- **S1:** direct contact to the hospital's designated operations contact within the SLA
  first-response window.
- **Monthly report** summarises incidents and SLA performance.

## 6. Roles

| Role | Responsibility |
|---|---|
| **Incident Commander** | Owns the incident end-to-end; declares severity |
| **Comms lead** | Status page + hospital updates |
| **Engineer on call** | Diagnosis & mitigation |
| **DPO** | Breach assessment & notification (data incidents) |

## 7. Post-incident review (blameless)
For every S1/S2: timeline, root cause, what went well/poorly, and **action items with
owners and dates**. Tracked to completion.

## 8. Readiness checklist
- [ ] On-call rotation defined with contact methods
- [ ] Alerting wired to health + metrics
- [ ] Status page live
- [ ] Backup restore tested in the last quarter
- [ ] Secret-rotation procedure documented and rehearsed
- [ ] Hospital operations contact recorded per tenant
