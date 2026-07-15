# crosstalk-mcp — Feature Plan: Continuous Talk, Live UI, Human-in-the-Loop

Status: **draft for refinement** · Owner: Mohamed · Last updated: 2026-07-12

This is a working plan, not a spec. It's meant to be argued with. Each phase ships something
usable on its own, so we can stop, look, and adjust before committing to the next one.

---

## 1. Where we are today

crosstalk-mcp is a **dumb shared mailbox**. It does not orchestrate anything — the
"conversation" is just two agents politely taking turns posting and polling.

- **Tools (MCP):** `post_message(channel, sender, type, body)`, `get_messages(channel, since_id)`, `list_channels()`.
- **Data:** one SQLite table `messages(id, channel, sender, type, body, created_at)`.
- **Transports:** streamable-HTTP MCP at `/mcp`; a REST mirror at `/api`.
- **Auth:** one optional shared bearer token (`RELAY_TOKEN`).
- **Editions:** Python (FastMCP) and Java (Spring Boot), identical contract.

What it's missing for what you want: **no push** (agents busy-poll), **no UI** (humans can't
see the chatter), and **no concept of people** — no identities, no targeting, no "ask humanY
this question."

### Decisions locked for this plan
- Build on **Python first**; keep **Java in parity as a follow-up**; add an **`npx` launcher** later.
- Continuous talk = **server push (SSE), agents still drive.** The relay stays a transport, not a brain.
- The UI is **bundled into the relay** and served at `/ui` from the same process.

### The topology this assumes
There is **one relay** (hosted on PC1, PC2, or a third box). Both agents connect their MCP
client to it. Both humans open the **same** relay's `/ui` in a browser and pick who they are
(humanX / humanY). Because everything flows through one central relay, routing a question to a
specific human is just an addressing problem — which is why the "bundled UI + one relay" choice
matters.

```
        PC1                         PC2
  ┌───────────────┐           ┌───────────────┐
  │ agentX (MCP)  │           │ agentY (MCP)  │
  │ humanX (/ui)  │           │ humanY (/ui)  │
  └──────┬────────┘           └──────┬────────┘
         │        both point at      │
         └───────────┬───────────────┘
                     ▼
           ┌──────────────────────┐
           │   crosstalk relay     │  SQLite + event bus
           │  /mcp  /api  /ui  /sse │
           └──────────────────────┘
```

---

## 2. The three features, in build order

Roughly increasing effort and risk. We do the plumbing that later phases need, early.

| Phase | Feature | Why it's here | Ships |
|------|---------|---------------|-------|
| 0 | Groundwork refactor | Everything later needs an in-process event bus + tests | No user-visible change |
| 1 | Continuous talk (push) | Your first ask; unblocks live UI | `wait_for_message` tool + SSE stream |
| 2 | Discord-style UI at `/ui` | Humans can watch the agents talk | Live read-only chat + human composer |
| 3 | Identities & presence | Prerequisite for routing to a specific human | "Who's online" + named participants |
| 4 | Human-in-the-loop routing | Your "later" ask: ask/answer/interrupt directed at humanX or humanY | Targeted questions, answers, interrupts |
| 5 | Parity & packaging | Promise of "two editions, one contract" + easy launch | Java parity + `npx crosstalk-mcp` |

---

## Phase 0 — Groundwork (small, invisible, load-bearing)

Nothing user-facing, but it makes the rest clean instead of hacky.

- Extract an **in-process event bus** (async pub/sub keyed by channel). Every write path —
  the MCP `post_message` and the REST `POST` — publishes the new message to subscribers.
  This one seam is what SSE (Phase 1) and the live UI (Phase 2) both subscribe to.
- Add a **schema-version / migration helper** so later phases can add columns without breaking
  existing `relay.db` files. SQLite: check `PRAGMA table_info(messages)` and `ALTER TABLE ADD
  COLUMN` only when missing. All new columns nullable → old rows and the current contract stay valid.
- Add a **test harness** (pytest) covering the three existing tools + REST, so we notice if we
  break the contract. This is our verification backbone for every later phase.
  **Decided:** tests live **outside the shipped artifact** (dev-only — a top-level `tests/` dir
  excluded from the package/Docker build), so the published relay stays lean.

---

## Phase 1 — Continuous talk (server push, agents still drive)

Goal: agents stop busy-polling and instead **block until the peer replies**, so a back-and-forth
runs continuously and cheaply. The relay never decides *what* to say or *when it's someone's
turn* — it only delivers.

Two additions, same event bus:

1. **New MCP tool `wait_for_message(channel, since_id, timeout_s=30)`** — long-poll. Returns the
   moment a message with `id > since_id` arrives, or an empty result on timeout (so the agent
   can loop or bail). This is the agent-side "continuous" primitive: *post → wait → post → wait*.
2. **New SSE endpoint `GET /api/channels/{channel}/stream?since_id=`** — a never-ending event
   stream of new messages. This is what the UI (Phase 2) consumes. Humans and dashboards use SSE;
   agents use `wait_for_message`.

Design notes / things to decide:
- **Turn-taking stays cooperative.** We rely on the agents' own instructions ("post, then wait
  for the reply, then respond") rather than the server enforcing turns. Matches your "agents
  still drive" choice.
- **Optional `session` (decided: include it, opt-in).** A session is a lightweight wrapper over a
  channel: `start_session(channel)` / `end_session`, a turn counter, an optional `max_turns` cap,
  and **auto-stop when both sides post `DONE`**. It's strictly opt-in — if you don't start a
  session, the channel behaves exactly as pure transport. This gives us guardrails against runaway
  loops without making the relay own the conversation. New nullable `session_id` column (via the
  Phase-0 migration) ties messages to a session when one is active.
- **Cost/loop safety:** `timeout_s` cap + a suggested max-idle so a stuck agent doesn't hang
  forever, reinforced by the session's `max_turns`.
- **Backpressure / missed messages:** the `since_id` cursor makes both mechanisms
  self-healing — reconnect with your last id and you never miss anything.

---

## Phase 2 — Discord-style UI at `/ui`

Goal: a human opens `http://<relay-host>:8765/ui` and **watches the two agents talk in real
time**, with the vibe of a Discord channel.

Served by the relay as static assets (single self-contained page to start — no build step,
easy to ship inside the Python package). Layout:

- **Left rail:** channel list (from `list_channels`), with unread/last-activity, Discord-style.
- **Main pane:** message stream — sender-colored avatars, name, timestamp, and a **type badge**
  (`QUESTION` / `ANSWER` / `NOTE` / `DONE`) so the human can skim the shape of the conversation.
  Live-updates via the Phase-1 SSE stream; back-fills history from `/api`.
- **Composer at the bottom:** a human can post into the channel too (posts via `POST /api`,
  `sender = "human"`). This is the seed of Phase 4 — the human is now a participant, not just a viewer.

Design notes:
- Start **vanilla HTML/CSS/JS in one file** for zero build friction; if it grows, graduate to a
  small Vite/React app served from the same route (still bundled). We keep the "one thing to run" promise.
- Markdown rendering for message bodies (agents post markdown).
- The look ("Discord vibes") is a styling pass — dark theme, rounded message groups, hover
  actions — cheap to iterate once the data is flowing.
- **Decided: single-channel view first.** Build the one-channel live chat end to end; the
  multi-channel sidebar comes later as a layout addition once the core is solid. (`list_channels`
  still powers a minimal rail, but we don't invest in server/DM navigation yet.)

---

## Phase 3 — Identities & presence (prerequisite for routing)

Before we can send a question "to humanY," the relay needs to know humanY exists and is online.

- **Participants:** a participant is `{ id, display_name, kind: human|agent, side: X|Y }`.
  Introduced by the UI (human picks "I am humanX on side X") and by agents (they already send a
  `sender`; we map sender → participant). Start cooperative (self-declared), harden with real
  per-user auth later.
- **Presence:** track who's connected via their SSE connection + heartbeat; show an online list
  in the sidebar (humanX, humanY, agentX, agentY) — again, very Discord.
- **Schema:** add nullable columns via the Phase-0 migration helper — `side` on messages, plus a
  small `participants` table (or a presence map kept in memory + last-seen persisted).
- **Decided: strictly two sides first** (side X = 1 human + 1 agent, side Y = 1 human + 1 agent).
  We hard-code that shape to keep routing and the UI simple. But we **design the `participants`
  model and `recipient` addressing to not assume it** — `side` is just a tag and `recipient` is a
  participant id, so lifting to **N participants per side** later is an additive change, not a
  rewrite. Called out explicitly in the backlog.

---

## Phase 4 — Human-in-the-loop: directed questions, answers, interrupts

The headline "later" feature. Now that messages can be **addressed** and people have
**identities + presence**, this becomes mostly UX.

New message semantics (all built on nullable columns, contract stays additive):
- `recipient` — who a message is *for* (`humanX`, `humanY`, `agentY`, or `any-human`).
- `reply_to` — the message id this answers (threads a QUESTION → ANSWER).
- `status` — for questions: `open` / `answered`.

Behaviors:
- **Ask a specific human.** `agentX` posts `type=QUESTION, recipient=humanY`. The relay marks it
  open-and-addressed-to-humanY. **humanY's UI** highlights it, pops a notification, and shows an
  inline **Answer** box; the answer posts back as `type=ANSWER, reply_to=<qid>`, flipping status
  to `answered`. Everyone sees the thread; only the addressee is prompted.
- **Both agents can ask, to either human.** Routing is just the `recipient` field + presence, so
  `agentX→humanY`, `agentY→humanX`, or `→any-human` all work the same way.
- **Human interrupt / directive.** A human posts `type=INTERRUPT` (or `DIRECTIVE`) targeted at an
  agent (or the channel). **Decided: interrupts are honored at the next turn boundary, not a hard
  mid-task stop.** Agents check for open interrupts when they come up for air (each turn, via
  `wait_for_message` or a small `get_directives` tool) and act on them then — pause, change course,
  answer. Simpler and safe; no need to kill work in flight. (If a true hard-stop is ever needed,
  it's a later addition.)
- **Questions don't block by default.** **Decided: an open QUESTION lets the asking agent keep
  working.** It posts `type=QUESTION, recipient=humanY` and carries on; the answer arrives as a
  threaded `ANSWER` it picks up at its next turn boundary (same cadence as interrupts). An agent
  that genuinely needs to wait can still opt into blocking via `wait_for_message` on the thread —
  but that's a choice, not the default.

Design notes:
- **Notifications:** in-UI toast + unread badge first; browser notifications optional.
- **Routing is central, not peer-to-peer** — everything goes through the one relay, so directing
  to humanY on PC2 is the same code path as anything else. This is the payoff of the single-relay topology.
- **Cadence:** both answers and interrupts land at the **next turn boundary** — one consistent
  rule for the agent to follow ("when you come up for air, check the thread and check for
  directives"), which keeps agent behavior predictable and easy to instruct.

---

## Phase 5 — Parity & packaging

- **Java edition parity:** re-implement Phases 1–4 additively (event bus → SSE, `wait_for_message`
  tool, same nullable columns, same `/ui`). The "two editions, one contract" promise holds because
  every change here was additive and cursor-based.
- **`npx crosstalk-mcp`:** a Node launcher that boots the relay (either shells to the Python/Java
  artifact or a thin Node port) so people can start it with one command. Decide at that point
  whether `npx` wraps the Python build or becomes a third first-class edition.
- **Per-participant auth (hardening).** Replace self-declared identities with real per-participant
  credentials so humanX can't impersonate humanY — the security item deferred here from Phase 3.
- **N-participants-per-side (planned lift).** With the two-side model proven, generalize
  `participants`/`recipient` to N per side; additive since Phase 3 was designed for it.
- **Docs + Docker image updates** for the new endpoints, UI, and env vars.

---

## 3. Contract & compatibility rules (so we don't paint ourselves in a corner)

- **Additive only.** Every new field is a nullable column; the three original tools keep working
  unchanged. An old client talking to a new relay sees no difference.
- **Cursor everywhere.** `since_id` is the single source of truth for "what's new" across polling,
  `wait_for_message`, and SSE — reconnect-safe by construction.
- **Relay stays a transport.** No conversation logic on the server unless we explicitly opt into a
  `session` concept later. Keeps both editions cheap to keep in parity.
- **Security caveat to revisit:** identities start **self-declared** (cooperative). The shared
  `RELAY_TOKEN` still gates the whole relay, but it does *not* stop humanX from claiming to be
  humanY. **Decided: real per-participant auth is deferred to the last phase (Phase 5).** We ship
  Phases 1–4 with cooperative identities and harden in Phase 5 — acceptable while this is used
  among trusted participants on a token-gated relay, but do not put anything sensitive through it
  until Phase 5 lands.

---

## 4. Suggested first slice (to get momentum)

If you want to see something move quickly, the smallest satisfying loop is **Phase 0 + Phase 1 +
a bare-bones Phase 2**: agents talk continuously via `wait_for_message`, and you watch it live in
a rough `/ui`. That proves the event-bus spine end to end; identities and routing then layer on
top without rework.

---

## 5. Resolved decisions (from refinement round 1)

All seven open questions are now answered and folded into the phases above:

1. **Tests** → kept **out of the shipped artifact** (dev-only `tests/`).
2. **Session** → include an **optional, opt-in `session`** (turn count + auto-stop on `DONE`) in Phase 1.
3. **UI scope** → **single-channel view first**; multi-channel sidebar later.
4. **Sides** → **strictly two sides** now (1 human + 1 agent each); model designed to lift to **N per side** later.
5. **Open QUESTION** → agent **keeps working**; picks up the answer at its next turn (blocking is opt-in).
6. **Interrupts** → **honored at the next turn boundary**, not a hard mid-task stop.
7. **Per-participant auth** → deferred to the **last phase (Phase 5)**; cooperative identities until then.
