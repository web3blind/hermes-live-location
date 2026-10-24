---
name: current-location
description: Read profile-local saved places or fresh live location for lookups.
---

# Current location consumer

Use only after the standalone `hermes-live-location` plugin has been reviewed,
installed, explicitly configured and enabled in the current Hermes profile.
This skill does not activate the plugin or grant access to another profile.

1. Locate the approved plugin directory in the **current** profile, using canonical
   Hermes home resolution (`hermes_constants.get_hermes_home()` in Python).
   Do not hardcode a default home, change `HERMES_HOME` to another profile, or
   search another profile's state. If no approved plugin is installed, stop.
2. Run its Hermes-Python reader from the plugin directory:
   `python -m live_location current`. Use the Hermes interpreter, not an unrelated
   system Python. No cross-profile home flag is supported.
3. Parse JSON and require exit code 0, then distinguish **both status and kind**:
   - `status == "fresh"`, `kind == "live"`: use numeric `latitude`/`longitude`
     for the requested lookup. Treat `accuracy_m` as optional. Check `observed_at`,
     `age_seconds` and `expires_at`; do not imply continuous or precise GPS.
   - `status == "saved"`, `kind == "static"`: this is the **last provided place**,
     not present GPS. It does not expire (`expires_at: null`). Use it only for a
     lookup that can use a saved place, describe it as saved and report its age.
     If present whereabouts are needed, ask for a new phone pin/live share instead.
   - Any other status/kind pair is unusable. Never infer freshness from exit 0 alone.
4. On exit 2 (disabled/empty/forgotten/stale/expired/stopped/binding_mismatch) or
   exit 1 (unavailable), do not reuse a prior point. State that fresh location is
   unavailable and ask for a place or a new phone share if needed. Never
   reinterpret zero latitude/longitude as missing.
5. Fetch weather or nearby places once per user request/scheduled run, not on
   each location update. Do not modify existing weather jobs without explicit
   authorization. Map lookup is separate; GPS is not obstacle detection or safe
   pedestrian navigation.

Use `python -m live_location status` for coordinate-free diagnostics. Do not
print/store raw `current` output in public logs, long-term memory or system
prompts. The requested weather service necessarily receives the submitted point;
use only the approved service and the requested purpose.

On an explicit request to forget local location, run
`python -m live_location forget`, require exit 0, and explain that this clears
local saved/live coordinates but does **not** stop Telegram sharing on the phone. New
post-forget initial shares may populate state again. Disabling persistence,
forgetting locally and stopping phone sharing are distinct operations. Do not
remove the SQLite file: it contains bounded replay/forget barriers.

Plugin wire-time settings require an approved gateway reconnect/restart to
change. Never activate, restart, install, publish or configure other profiles
merely to satisfy a read request.
