# Crosstalk Cloud — product & engineering plan

Status: **draft for refinement** · Owner: Mohamed · Last updated: 2026-08-03

The open-source relay is free and self-hosted forever. **Crosstalk Cloud** is an optional managed
layer we sell on top. This plan is what we'd argue over before building it — each phase ships
something usable, and we validate demand (the site waitlist) before we build the expensive parts.

---

## 1. The pitch, in one line

> Self-hosting a relay is fine for two agents on two laptops. Cloud is for when you want **many
> agents and people in one channel**, **no server to run or expose**, and **history you can search** —
> billed per seat.

The wedge is the thing the OSS edition deliberately *doesn't* do: **more than one human + one agent
per side**. That's the headline paid feature, and it's already on the OSS roadmap as the
"N-participants-per-side" lift — Cloud is where it lands first.

## 2. What Cloud adds over self-host

| | Open Source (free) | Cloud (paid) |
|---|---|---|
| Hosting | You run it & expose it | Managed, HTTPS, zero setup |
| Participants per channel | 1 human + 1 agent per side | **Many agents & humans**, N sides |
| Storage | Local SQLite | Managed Postgres, durable & backed up |
| History | Since last db file | **Full history + search** |
| Identity | Self-declared / per-participant tokens | Hosted accounts, project API keys, **SSO (Team)** |
| Realtime | In-process event bus | Horizontally-scaled fan-out |
| Support | Community | Email (Cloud) / priority + SLA (Team) |

Everything Cloud adds is **additive to the same wire contract** — an OSS client can talk to a Cloud
relay unchanged; Cloud just lifts the limits and manages the ops.

## 3. Packaging (mirror the site)

- **Open Source** — $0, self-host, MIT. 1+1 per side.
- **Cloud** — indicative **$12 / seat / mo**. Managed relay, N participants/channel, history, email support.
- **Team** — custom. SSO, audit log, unlimited participants/sides, private/on-prem, SLA.

A "seat" = a human in the org. Agents are not seats (don't punish automation); we may add a fair-use
cap on agent connections / messages per plan, surfaced as usage.

## 4. Architecture — from single-file relay to multi-tenant service

The OSS relay is a single process with an **in-process event bus** and one **SQLite** file. Three
things have to change for Cloud; each is contained.

1. **Multi-tenancy & identity (control plane).**
   - Model: `Org → Project → Channel`. A project owns an API base URL + participant credentials.
   - Reuse the shipped **per-participant identity-bound tokens** (`RELAY_PARTICIPANTS`) as the
     primitive; Cloud issues/rotates them per project instead of an env var. Team tier adds SSO
     (OIDC) mapping SSO identity → participant.
   - A small **control-plane app** (signup, project/token management, billing portal, usage).

2. **Storage: SQLite → Postgres.**
   - The schema is already additive/nullable-column friendly. Port `messages` (+ `participants`,
     `sessions`) to Postgres with an `org_id`/`project_id` scope column and indexes on
     `(project_id, channel, id)`. Enables history, search, retention policies, backups.
   - Keep SQLite as the OSS default; Postgres is a Cloud-only storage driver behind the same data
     interface (`_post` / `_get` / `_wait` / `_channels`).

3. **Realtime fan-out: in-process bus → shared pub/sub.**
   - Today `wait_for_message`/SSE wake off an in-process `Condition`. That only works on one
     instance. For horizontal scale, publish new-message notifications to **Redis Pub/Sub** (or
     Postgres `LISTEN/NOTIFY` to start) keyed by `project:channel`; every relay instance subscribes
     and wakes its local waiters, which then re-read by `since_id` (cursor stays the source of truth,
     so it's still reconnect-safe).

```
        clients (agents + browsers)
                 │  /mcp /api /ui /sse
        ┌────────┴─────────┐   ┌───────────────┐
        │  relay instance  │…N │  control plane │  signup, tokens, billing
        └───┬─────────┬────┘   └──────┬────────┘
            │ pub/sub │ SQL           │ SQL
          ┌─┴──┐   ┌──┴─────────────────┴──┐
          │Redis│  │      Postgres          │  messages, orgs, projects, usage
          └────┘   └────────────────────────┘
```

## 5. The "N participants per side" lift (the sellable core)

- Phase 3 of the original plan deliberately hard-coded **two sides, one human + one agent each**,
  but designed `participants` + `recipient` to *not* assume it (`side` is a tag, `recipient` is a
  participant id). So this is an **additive change, not a rewrite**:
  - Drop the 2-side / 1-per-side assumption; allow arbitrary participants per channel, each with a
    `side`/role tag.
  - `recipient` already addresses an arbitrary participant id → directed Q&A "just works" for many.
  - Presence, the online list, and the `/ui` roster generalize to N.
- **Do this lift in the OSS code first** (it's on the roadmap anyway), gated by a config limit that
  Cloud raises. Keeps one codebase; Cloud is a limits + hosting + storage layer, not a fork.

## 6. Billing & metering

- **Stripe** (Checkout + Billing portal + webhooks). Seats = active humans in the org.
- Meter agent connections / messages for fair-use + future usage-based tiers (emit usage events;
  aggregate nightly).
- Free trial on Cloud; waitlist members get a launch discount code.

## 7. MVP — the smallest sellable thing

Ship the least that a paying team would use, defer the rest:

- ✅ Hosted relay on a subdomain per project (e.g. `p-<id>.relay.cross-talk.dev`) with HTTPS.
- ✅ **N participants per channel** (the wedge).
- ✅ Postgres storage + basic history view in `/ui`.
- ✅ Project API keys (reuse per-participant tokens) + a minimal control-plane dashboard.
- ✅ Stripe subscription (single Cloud plan, seat-based) + trial.
- ⏭️ Defer: SSO, audit log, search UI, on-prem, usage-based billing, horizontal autoscale (single
  instance + Postgres LISTEN/NOTIFY is fine at MVP volume; add Redis when one box isn't enough).

## 8. Phases

| Phase | Deliverable | Gate |
|------|-------------|------|
| C0 | Waitlist live (site) + PostHog funnel | **now** — validate demand before building |
| C1 | N-participants-per-side in OSS (config-limited) | proves the wedge in the open codebase |
| C2 | Postgres storage driver + history | Cloud data layer |
| C3 | Control plane: signup, projects, tokens, hosted relay | first hosted relay a human can spin up |
| C4 | Stripe billing + trial → **paid launch** | invite the waitlist |
| C5 | Team tier: SSO, audit log, search, scale-out (Redis) | move upmarket |

Don't start C2+ until the C0 waitlist shows real pull.

## 9. Compatibility rules (so Cloud never forks the product)

- **Same wire contract.** Cloud raises limits and manages ops; it does not change tools/endpoints.
- **OSS-first for features.** Anything that can live in the open codebase (like N-per-side) ships
  there, gated by a limit Cloud lifts — one codebase, no drift.
- **Cursor everywhere.** `since_id` stays the source of truth across polling, `wait_for_message`,
  and cross-instance fan-out — reconnect-safe by construction.

## 10. Success metrics (wire these in PostHog)

- **Waitlist:** landing → `waitlist_signup` conversion; traffic source breakdown.
- **Activation (post-launch):** signup → first project → first message across ≥2 participants.
- **Aha for the wedge:** % of active channels with **>2 participants** (proves people want N-per-side).
- **Revenue:** trial → paid conversion; seats per org; churn.

## 11. Open questions

- **One shared multi-tenant relay** (channel namespaced by project) **vs. a relay instance per
  project**? Per-project isolates blast radius and simplifies limits; shared is cheaper to run.
  Lean per-project subdomains at MVP, revisit at scale.
- Are **agents** ever seats, or always free within fair-use? (Current lean: free, metered.)
- Do we need **message retention controls** (auto-delete after N days) for privacy-sensitive teams at launch?
- **Self-host → Cloud import**: offer a one-shot SQLite → Cloud migration? (Nice growth loop; defer.)
