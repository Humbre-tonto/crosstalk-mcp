# C1 — N participants per channel (the paid wedge, in OSS)

Status: **ready to implement** · Depends on: nothing (additive) · Blocks: C2+ Cloud work
Ticket for the `docs/CLOUD-PLAN.md` Phase **C1**: "N-participants-per-side in OSS, config-limited."

## Goal

Let a channel hold **any number of agents and humans** — each addressable, present, and able to
take part in directed Q&A — instead of the current implicit "two sides, one human + one agent
each." Ship it in the open-source Python edition, behind a configurable participant limit, keeping
the wire contract **additive and backward compatible**.

This is the feature Cloud sells ("many agents & humans per channel"), proven first in the OSS code.

## What's already N-ready (so we don't rebuild it)

- **Presence** (`_online_participants[channel][id]`) is keyed by participant id — already holds N.
- **Addressing:** `recipient` is a participant id; directed `QUESTION → <id>` and `get_directives`
  already work for arbitrary ids, plus `any-human` broadcast.
- **Schema:** `side`, `recipient` are nullable columns — no migration needed.
- **UI:** message colors are per-sender (`getDeterministicColor`), and the recipient dropdown is
  built from live presence.

## What bakes in "two sides / one-per-side" today (the actual work)

| Location (`python/crosstalk_mcp.py`) | Current behavior | Change |
|---|---|---|
| `_register_agent_presence` (~L90-97) | Forces `side` to **"X"/"Y"** by a name heuristic when not given | Treat `side` as an **optional free-form role/team tag**; store what's given, else leave unset — stop inventing X/Y |
| `_post` session block (~L214) | Auto-stops when `len(done_senders) >= 2` | Generalize the **DONE quorum** to N (see below) |
| `_get_directives` (~L295) | Broadcasts match `recipient IS NULL/''/'any-human'` | Add `any-agent`, `all`, and `side:<tag>` group addressing |
| SSE presence (~L514) & `rest_presence` | Register/list participants unbounded | Enforce the new **participant cap**; prune before counting |
| `ui.html` identity modal / badges / roster | Side = **X or Y** picker; "Side X/Y" badges | Free-form **role** (optional); roster + badges generalize to N |

## The one product decision to make first

**What is the default participant cap in OSS?** The site's Free tier says "1 human + 1 agent per
side"; the Cloud plan says "gated by a limit Cloud raises." Options:

- **(A) Recommended — default cap that matches the simple story, env-raisable.**
  `RELAY_MAX_PARTICIPANTS` defaults to **4** (human+agent ×2). Self-hosters can raise it via env;
  Cloud sets it high/unlimited per plan. OSS stays honest to the site copy, the wedge stays intact
  as a *managed-convenience* lever (a hard software lock is unenforceable in open source anyway),
  and power users aren't crippled.
- **(B) OSS uncapped**, Cloud sells only hosting/history/scale. Cleaner ideologically, but weakens
  the "more participants" wedge and means rewording the site's Free bullet.

Recommendation: **(A)** with default `4`, and reword the site to "up to N participants — raise the
limit yourself, or let Cloud manage it." Pick the default number before implementing.

## Config

```
RELAY_MAX_PARTICIPANTS   # int, per channel. Default 4 (decision above). 0 = unlimited.
```
Parsed once at startup like the other env vars. Counts **live** participants (agents + humans) per
channel, after pruning stale ones.

## Server changes (`python/crosstalk_mcp.py`)

1. **Optional role, not forced side.** In `_register_agent_presence`, drop the X/Y heuristic
   fallback: if `side` is provided, store it; otherwise store `None` (or `""`). Keep accepting an
   explicit `side` from `post_message`/SSE. (Update the two existing side-heuristic tests to reflect
   that an unspecified side is now unset, not guessed.)

2. **Participant cap.** Add `_can_join(channel, participant_id) -> bool`: prune stale, then allow if
   the id is already present OR the live count `< RELAY_MAX_PARTICIPANTS` (or unlimited). Enforce in:
   - **SSE `rest_stream`** (human/agent joining): if `_can_join` is false, return **403**
     `{"error":"channel_full","limit":N}` before registering.
   - **`_register_agent_presence`** (called from `_post`): if over cap and the sender is new, skip
     registration (still store the message, or reject the post — decide; recommend **reject the
     post with 403** so behavior is consistent and visible).

3. **DONE quorum for N.** Track the set of senders that have posted in the session
   (`sess["speakers"]`). Auto-stop when **every speaker has posted DONE and there are ≥ 2 speakers**
   (replaces the hard `>= 2 done_senders`). Optional `min_done` override on `start_session`.

4. **Group addressing.** Extend `_get_directives` (and document for `post_message`) so `recipient`
   may be a participant id **or** a group token: `any-human`, `any-agent`, `all`, or `side:<tag>`.
   `_get_directives(channel, recipient)` returns messages addressed to `recipient`, to a group the
   recipient belongs to, or broadcast — resolved against presence (`kind`, `side`).

5. **Presence payload:** include `role`/`side` (optional) and `kind` as-is; nothing removed
   (additive).

## UI changes (`site`-independent — `python/ui.html`)

- **Identity modal:** replace the "Side X / Side Y" select with an **optional free-text Role/Team**
  field (leave blank for none). Keep participant id + display name.
- **Roster:** already lists all online participants — group by `role` when present, else flat; show
  a small role chip. Remove any hard two-column "X vs Y" assumption.
- **Recipient dropdown:** already built from presence; add the group options (`any-human`,
  `any-agent`, `all`). 
- **Badges:** render the `side`/role tag generically (chip with deterministic color) instead of
  literal "Side X/Y".
- **Channel-full state:** if the SSE stream returns 403 `channel_full`, show a clear banner
  ("This channel is at its participant limit (N).").

## Tests (`tests/test_relay.py`)

- N participants (e.g. 5) register and all appear in presence.
- Cap enforcement: with `RELAY_MAX_PARTICIPANTS=3`, the 4th distinct participant is rejected (403 /
  `_can_join` false); an already-present id re-joining is allowed; stale pruning frees a slot.
- Directed `QUESTION → <one of N>` reaches only that recipient via `get_directives`; `any-agent` /
  `side:<tag>` group addressing resolves correctly; `all` reaches everyone.
- Session DONE quorum: 3 speakers, session stops only when all 3 have posted DONE.
- Backward-compat: existing 2-participant flows and the current tests still pass (adjust only the
  two side-heuristic assertions).

## Backward compatibility

- Wire contract stays additive: no fields removed; `side` becomes optional/free-form; new
  `recipient` group tokens are opt-in; new env has a default.
- Default cap ≥ today's typical usage, so existing 2-side setups are unaffected.
- Only two existing unit tests change (the side-heuristic assertions), and by design.

## Out of scope (later phases)

- Per-org / per-plan limit **enforcement in a control plane** and billing → **C3/C4**.
- Postgres storage, history, cross-instance fan-out → **C2 / C5**.
- Java-edition parity for any of this.

## Acceptance criteria

- A channel can hold ≥ 5 mixed agents/humans, all present, all addressable by id and by group.
- `RELAY_MAX_PARTICIPANTS` enforces the cap with a clear 403; `0` = unlimited.
- Directed Q&A, interrupts, and sessions work with N participants (DONE quorum generalized).
- `/ui` shows an N-participant roster, optional roles, and group recipients; handles channel-full.
- Full test suite green (old + new).

## Suggested PR breakdown

1. **Server core** — config + optional role + cap + DONE quorum + group addressing (+ tests).
2. **UI** — role field, N roster, group recipients, channel-full banner.
3. **Docs/site** — reword the Free-tier participant copy per the decision above.
