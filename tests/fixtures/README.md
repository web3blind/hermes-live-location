# Frozen 0.1 compatibility fixture

These six files preserve the runtime-relevant 0.1 plugin from local revision
`2e8b7a0` (terminal extraction may omit the final newline). Do not modernize them
alongside the production implementation: tests need the original `wire` factory,
group `-10` live-only interception and SQLite schema without `kind`.

Native tests load this fixture through real plugin discovery in a disposable home,
wire it to a connected offline Telegram application, then replace only that
home's plugin copy with the candidate and invoke supported rediscovery/rewire.
Store tests use the old Store to create legacy SQLite rows and verify additive
migration, consistent backup/restore, privacy fields and rollback on failure.

All configuration, Telegram identities, points and SQLite data in tests are
synthetic. This directory is never installed into a real profile by the tests.
