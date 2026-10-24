# Hermes saved/live location

Standalone Hermes plugin for profile-local saving of Telegram location pins and quiet
live-location updates. No core patch, new model tool, second polling consumer, weather
API call or per-point LLM turn. Version 0.2.0; experimental, with offline native-dispatch
coverage. Real phone delivery and production deployment are not certified by these tests.

## Behavior

- A supported `register_telegram_handler(factory)` factory receives the native
  PTB Application and a read-only adapter. A blocking handler in group `-11`
  saves authorized new static pins without stopping dispatch. For live initial
  messages and **all location/venue edits**, it raises
  `ApplicationHandlerStop`. Later core location/observer handlers do not see them.
- Only an explicitly configured, non-bot owner in their matching private chat,
  on the configured bot, may persist a static pin or live initial share. Forwarded messages,
  sender-chat messages, group chats and foreign senders never persist.
- Unknown edits never start a share. Edits to an ordinary static pin are also
  suppressed intentionally: after a restart/forget, an edited stopped share may
  be indistinguishable from an ordinary edited pin. A **new static pin** without
  `live_period` is saved **and still follows Hermes's normal location/LLM flow once**.
  Venue pins use `venue.location`, consistently with the core adapter. Static
  rejection/storage failure never swallows or bypasses core authorization; a
  foreign/forwarded ordinary pin is not saved but keeps its original core behavior.
- While this plugin is loaded, disabled/unconfigured/invalid persistence still
  consumes live messages and location edits quietly; static pins still pass through.
  No location state is created when disabled/unconfigured.
  If the plugin is **unloaded/not enabled**, normal Hermes behavior resumes and
  live messages may go to its ordinary agent-location path. Disabling is not erasure.
- The latest point alone is retained. A missing/zero `live_period` on an edit of
  the current source marks it stopped; a later edit cannot reactivate that source.
  New sources require an observed initial message with a newer message ID.
- Latest newer source wins across both kinds: a static pin replaces an old live
  share and its later edits cannot overwrite the pin. A newer live initial share
  replaces a saved pin. One singleton holds the latest source, not one per kind.
- Static output is `status: saved`, `kind: static`, with explicit age and no expiry.
  This means **last provided place**, not current GPS or proof the owner is there.
  It remains usable for an explicitly place-based lookup until replaced/forgotten,
  even after the live stale threshold. Static edits do not refresh it.
- Expiry is original message date + initial live period. Edits never extend it.
  `stale_after_seconds` uses Telegram's source timestamp, not receive time;
  delayed delivery does not make an old point fresh. Missing updates alone do not
  prove the phone stopped sharing. Clock rollback/future points fail freshness.

## State and privacy

State lives in the canonical current `get_hermes_home()` under
`live-location/latest.sqlite3`. Runtime wire captures the owning context-local
home; callback-time environment/context switches cannot redirect its writes.
No process-global per-profile location cache or cross-profile CLI path option.

The SQLite database holds one singleton row: bot/owner binding, latest point,
source kind, original/observed/received/expiry timestamps, accuracy, stopped flag, monotonic
update watermark, newest message ID and local forget-time barrier. It does not
hold a route, user messages, tokens, addresses or reverse-geocoded names. Retired
sources have no unbounded tombstone list. Duplicate/older updates and older
message sources are rejected. Invalid/nonfinite coordinates and accuracy fail
validation. Update-ID monotonicity is deliberately conservative: Telegram can
randomize its next update ID after a week of silence. If it decreases, ingestion
may remain held; this prototype offers no unsafe reset shortcut.

Directory permissions are tightened to `0700`, database to `0600`. Symlink,
nonregular, hardlinked and foreign-owned database files are refused. SQLite uses
DELETE journals and `secure_delete`, not WAL/history storage. This is **not disk
forensic erasure or protection from the same OS user/root/backups**; crash journals,
filesystem snapshots and external backups may retain data. Only use a trusted,
local profile filesystem. SQLite reads/writes are synchronous and bounded by a
2-second lock timeout; sustained throughput/event-loop latency is not benchmarked.

`status` excludes coordinates. Only `current` emits a fresh live point or saved place intentionally.
Expired, stale, stopped, forgotten, mismatched and disabled states never expose a
point through the CLI. Inactive last points remain private in the database until
`forget`. Errors/logs use fixed messages, not coordinates, IDs or exception text.
Hermes itself can retain update admission receipts (update IDs/times); external
transport/debug logging is outside this plugin's control. Avoid logging raw updates
or copying CLI coordinate output into long-term memory, prompts or public logs.

`forget` clears point/timestamps and retains bounded message/update/time barriers.
It prevents delayed edits, replays and pre-forget initial messages from restoring
forgotten state. A genuinely new initial share after the barrier can populate it.
Shares started in the same timestamp second as forget are held conservatively.
**Local forget does not stop Telegram phone sharing. Stop sharing on the phone.**
Disabling storage does not delete data or stop Telegram; forgetting does not disable
future authorized new shares. Stop on the phone, disable persistence and forget
locally if all three effects are desired. Do not delete the database as a substitute
for forget: deleting it also deletes replay barriers.

## Requirements and compatibility

- A recent Hermes checkout with the public handler-registration API, native
  Telegram admission wrapper and handler ordering used by this prototype.
- Hermes's own Python environment with `python-telegram-bot` and PyYAML. No new
  dependency installer or live credential is required for offline tests.
- Verified against local Hermes revision `d2520430e9`, PTB `22.6`, PyYAML `6.0.3`
  on Linux. No claim of compatibility with every Hermes/PTB release or Windows
  (`O_NOFOLLOW`/POSIX permissions are required).

References: [Hermes docs](https://hermes-agent.nousresearch.com/docs/),
[Bot API Location](https://core.telegram.org/bots/api#location),
[Bot API Update](https://core.telegram.org/bots/api#update).
Bot-sent `stopMessageLiveLocation` is not a way to stop a user's phone share.
Telegram/client support and delivery cadence must be checked with the actual phone.

## Installation

### Install from GitHub with the Hermes CLI

Select the intended Hermes profile first. With Hermes's existing Python/PTB/YAML
dependencies available, install this public repository without enabling it yet:

```bash
hermes plugins install web3blind/hermes-live-location --no-enable --no-deps
```

This is a custom GitHub source, **not an entry in the official Hermes plugin
catalog**. The CLI's unreviewed-source warning is expected; inspect the source
before trusting it. Successful installation alone does not configure or enable
location storage. Continue below with owner/bot settings and activation.

### Manual directory installation

Use a reviewed source checkout and Hermes's Python environment (the environment
that runs your gateway, not an unrelated system Python). Review the requirements
above before installing: no minimum public Hermes release has been established;
the required native APIs must be available. This is a directory plugin, not a
standalone pip package. The host provides its Hermes, PTB and PyYAML dependencies.

Choose the intended Hermes profile first. Set `HERMES_HOME` to that profile's
canonical home for **both** these commands and the gateway; for the default
profile this is normally `$HOME/.hermes`. Never copy location state between profiles.
The following commands install only source/manifest files, not this checkout's
Git history, tests, documentation or runtime databases. They refuse to overwrite
an existing plugin; see the upgrade section for an existing installation.

```bash
# Run from the reviewed source checkout with HERMES_HOME explicitly selected.
: "${HERMES_HOME:?Set HERMES_HOME to the intended profile home first}"
mkdir -p "$HERMES_HOME/plugins"
PLUGIN_DIR="$HERMES_HOME/plugins/hermes-live-location"
mkdir "$PLUGIN_DIR" && mkdir "$PLUGIN_DIR/live_location" &&
  install -m 644 plugin.yaml __init__.py "$PLUGIN_DIR/" &&
  install -m 644 live_location/*.py "$PLUGIN_DIR/live_location/" &&
  hermes plugins validate "$PLUGIN_DIR" --json
```

Proceed only if validation returns `ok: true`. This copy does not enable the plugin,
change configuration or restart a gateway. A failed partial copy requires inspection
before retrying; do not run an overwrite command against an existing installation.

Choose numeric owner and bot IDs from trusted existing Telegram/Hermes settings.
Do not use a username as an ID. Do not put a bot token in this plugin's settings.
Settings are in `config.yaml`, using the Hermes config CLI, not `.env`:

```bash
# Replace OWNER_ID and BOT_ID with your own integer IDs before executing.
hermes config set plugins.entries.hermes-live-location.settings.owner_user_id OWNER_ID
hermes config set plugins.entries.hermes-live-location.settings.bot_id BOT_ID
hermes config set plugins.entries.hermes-live-location.settings.stale_after_seconds 300
hermes config set plugins.entries.hermes-live-location.settings.enabled true
hermes plugins enable hermes-live-location
```

The effective configuration is `plugins.enabled` containing `hermes-live-location`,
not present in `plugins.disabled`, and `settings.enabled: true`. The entry's optional
`enabled: false` also disables persistence. IDs must be positive integers; stale
threshold must be an integer from 1 to 86400 seconds. Default is disabled. Wire-time
configuration is frozen: **restart/reconnect the intended gateway after changes**
under separate approval. CLI reads current configuration every invocation. Avoid
hot rewire for config changes: older native handlers may still exist until reconnect.
Only one bot/owner binding per profile is supported. A changed binding returns
`binding_mismatch`; explicit forget rebinds while retaining conservative fences.

### Upgrading 0.1 to 0.2 — separately approved operation

Before replacing installed code, back up that exact profile's plugin directory
and make a **consistent SQLite backup using SQLite's backup API**, not a raw copy
of an open database/journal. Keep backups private (`0700` directory, `0600` DB).
Verify the backup opens, has the singleton, point and binding/replay/forget fields;
restore and compare it in a disposable home before changing live state. This
project's migration tests perform those checks only against synthetic old-schema
fixtures. They are not a backup of any real profile.

The first 0.2 store access adds `kind TEXT NOT NULL DEFAULT 'live'` in a transaction.
Existing live points, stop state, expiry, bindings, update/message watermarks and
forget barriers are preserved without rewriting those fields. No route/history
table is introduced. Do not delete or recreate the database during upgrade.

Native rewire deduplicates factories by `(plugin_name, __qualname__)`; merely
rediscovering code under the old `wire` identity does not replace a live callback.
Version 0.2 registers stable `wire_static_live_v2` at group `-11`, ahead of the old
0.1 handler at `-10`. Through the supported `adapter.rewire_plugin_handlers()`
boundary, it adds one handler without editing/removing legacy or unrelated
handlers. Live updates stop before the legacy callback; new static pins pass it
and reach core once. Repeated rediscovery/rewire adds no duplicates. The remaining
legacy handler disappears on an ordinary approved reconnect. Do not rename the
factory on every reload or use this as a general config-reload mechanism.

A separately approved profile-local code upgrade must explicitly rediscover the
target profile's manager and invoke the supported rewire in that adapter's owning
runtime context/event loop. Rediscovery alone is insufficient (same-name plugin
reload may not emit a new-plugin notification). Do not restart a multiplex service
or touch other profiles to achieve this. If that scoped boundary cannot be reached,
defer to an approved target-profile reconnect. Verify installed code and exact
target state after the operation; a phone one-shot test remains a separate check.
For rollback, restore matched code + consistent DB backup only while target writes
are quiesced; restoring the pre-upgrade DB discards any points/fences accepted since
the backup, so replay/privacy impact needs explicit approval.

## Sending a location

After configuration and a gateway reconnect, open the bot's **private chat** from
the configured owner's account. Use Telegram's attachment/location menu to send:

- A one-time location pin (or venue) to save a place. Hermes's normal pin handling
  still runs once, so the pin may reach the model and produce a normal reply.
- A live location share to update quietly. Leave phone sharing enabled while you
  need live updates; this plugin does not request background GPS itself.

Menu labels and live-sharing availability depend on the Telegram client. Check
`status` for coordinate-free diagnostics and `current` for the consumer output
below. No new bot slash command is installed. Stop sharing in Telegram to stop the
phone stream; use `forget` separately to clear the plugin's stored point.

## CLI / consumer contract

From this project or the copied plugin directory, using Hermes's Python interpreter:

```bash
python -m live_location status
python -m live_location current
python -m live_location forget
```

Outside that directory, prepend the installed plugin directory to `PYTHONPATH`.
The commands always use canonical current profile resolution. There is no `--home`
or cross-profile reader flag. CLI settings must refer to the same configured bot
and owner as ingest. JSON is the stable interface:

- `status`: status, kind (when a point exists) and timestamps/age only; exit 0 (error: 1).
- `current`: exit 0 for either `status: fresh, kind: live` or
  `status: saved, kind: static`. Both include `latitude`, `longitude`, nullable
  `accuracy_m`, `observed_at`, `received_at`, `expires_at`, `age_seconds`.
  Saved places have `expires_at: null` and may be arbitrarily old; they are **not
  a current GPS claim**. Existing fresh-only consumers must opt in to saved places.
- Unusable current point: status only or status/timestamps, **no coordinates**,
  exit 2. Statuses include `disabled`, `empty`, `forgotten`, `binding_mismatch`,
  `stopped`, `expired`, `stale`. Configuration/storage errors return
  `{"status":"unavailable"}` and exit 1, without a traceback or raw payload.
- `forget`: `{"status":"forgotten"}`, exit 0. It also works with disabled valid
  settings; repair malformed config first. It does not promise physical erasure.

Weather/travel consumers should invoke `current` at request/run time, require exit
0 and inspect **both status and kind**. For `fresh/live`, use the point with live
age/accuracy caveats. For `saved/static`, use it only when the requested lookup can
use the last provided place, and explicitly describe it as saved with its age;
ask for a new share when present whereabouts are required. Never silently label
saved coordinates current GPS. Pass the numeric point directly to the existing
lookup. Never reuse coordinates from a previous successful call after a failed
one. Do not `eval` output. Do not geocode/fetch weather on every GPS update. No
weather script or cron integration is included or modified. Nearby/map lookup is
a separate operation; coordinates are not obstacle awareness or safe navigation.
The optional `skills/current-location/SKILL.md` documents this contract for later
manual skill installation; it is not automatically activated by this plugin.

## Offline verification

With `HERMES_SOURCE` pointing to the read-only Hermes checkout, run its canonical
wrapper with an **absolute** test path (not direct pytest):

```bash
"$HERMES_SOURCE/scripts/run_tests.sh" "$PWD/tests" --tb=short
hermes plugins validate "$PWD" --json
```

The test fixture sets temporary homes and context overrides. Discovery tests copy
this project only into those disposable homes, use real manifest loading,
`PluginContext.register_telegram_handler`, adapter `connect()` and native
`TelegramApplication.process_update`. A fake `BaseRequest` permits only `getMe`;
no polling, sends, downloads, weather requests or LLM calls are permitted. Core
static pin processing reaches an observed stand-in at the model boundary.

Covered: saved static/venue pins with exactly-once core dispatch, cross-kind
ordering, indefinite saved age, legacy additive migration and SQLite backup/restore,
0.1 already-wired native upgrade with repeated rediscovery, live initial/edit/stop,
unchanged original expiry, freshness and delayed
points, duplicates/out-of-order/retired sources, unknown/inactive edits, defaults,
foreign/forwarded/group messages, invalid points, CLI subprocess and context home
switching, restart/replay receipts, forget barriers, binding isolation, bounded
records/permissions, unavailable/corrupt stores and quiet failure behavior.

**Live-unverified:** phone menu/support in the exact private bot chat, real update
cadence/accuracy, actual stop representation, background/battery/network behavior,
real bot reconnect and live multiplex deployment, end-to-end weather delivery,
throughput and memory/CPU limits. Offline tests are not proof of those behaviors.
A separately approved phone trial must observe initial → movement edit → phone stop,
confirm no per-point response/LLM history and separately test one fresh weather read.
Independent read-only review is a separate acceptance step; see `plan.md`.

## License

[MIT](LICENSE). Copyright (c) 2026 web3blind.
