# Saved/live location: design and verification

## Scope

Standalone Hermes directory plugin for one explicitly configured Telegram bot/owner
binding per profile. Keep only the latest location and bounded replay/forget fences.
Do not add a core patch, model tool, second update consumer, route history, automatic
weather lookup or per-point LLM turn. Runtime state belongs to canonical
`get_hermes_home()`; no cross-profile reader option is provided.

## Runtime contract

- Authorized new static/venue pins persist and reach the existing core location
  flow once. A saved place is the last provided place, not a current GPS claim.
- Initial live messages and all location/venue edits are consumed before core
  location/model handlers, including rejected or unavailable persistence.
- Latest newer source wins across static and live kinds. Static edits never refresh
  a saved place. Retired or forgotten live edits cannot restore a previous source.
- Live expiry uses the original message date and initial period; edits never extend
  it. Freshness uses the source timestamp. Stopped, stale and expired are distinct.
- `status` never emits coordinates. `current` emits them only for `fresh/live` or
  `saved/static`; consumers must inspect both fields and explicit age. Inactive
  output must not fall back to a previously successful read.
- `forget` clears coordinates and timestamps but retains bounded update/message/time
  barriers. It does not stop phone sharing or promise disk-forensic erasure.
- Only an explicitly configured owner in their matching private chat may persist.
  Reject forwards, sender-chat messages, bots, foreign senders and invalid points.
  Use restrictive local filesystem permissions and fixed, payload-free errors.

## Compatibility and upgrade design

Version 0.2 adds `kind TEXT NOT NULL DEFAULT 'live'` transactionally without changing
legacy values. The frozen 0.1 fixture intentionally retains the old schema and
`wire` factory at group `-10`. Version 0.2 registers `wire_static_live_v2` at `-11`,
so supported native rewire can preempt an already-wired legacy handler without
removing unrelated handlers. Repeated rediscovery/rewire must not add duplicates.
Rediscovery alone is not a configuration reload; reconnect is the normal boundary.

Before an actual upgrade, back up code and use SQLite's consistent backup API;
verify restore/migration in a disposable home. Do not delete replay barriers or copy
state across profiles. See README for operational privacy and rollback limits.

## Offline verification

Run the host's canonical test wrapper with an absolute plugin test path:

```bash
"$HERMES_SOURCE/scripts/run_tests.sh" "$PWD/tests" --tb=short
hermes plugins validate "$PWD" --json
```

Tests use temporary homes and synthetic Telegram identities, timestamps and points.
They exercise native plugin discovery, PTB dispatch/admission, CLI subprocesses,
static core-once routing, live quiet routing, additive migration, consistent
backup/restore, factory deduplication, frozen runtime-home capture, source ordering,
original expiry, delayed delivery, freshness, restart/replay, forget barriers,
binding isolation, storage corruption and restrictive file permissions. Socket
connections are denied in-process; the fake Telegram request accepts only `getMe`.
No live credentials, phone, weather service or real profile database is required.

## Deliberate limits

All location edits are suppressed because an unknown stopped live share may look
like an edited ordinary pin. Persistence-disabled but loaded handlers still consume
live updates; unloading the plugin restores normal core behavior. A decreasing
Telegram update ID after long inactivity can conservatively hold ingestion. SQLite
calls are synchronous with a bounded lock timeout; throughput is not benchmarked.

Real phone menus, cadence/accuracy, stop representation, battery/network behavior,
production deployment and end-to-end weather delivery need separate live testing.
Offline verification is not evidence that those behaviors have been exercised.

## Public-source boundary

This design document intentionally contains no operator deployment diary, profile
backups, machine-specific paths or private state. Preparing the working tree does
not sanitize prior Git commits or their author metadata. Review full history before
publication; a clean export or history rewrite is a separate explicit decision.
Published under the MIT License; see `LICENSE`.
