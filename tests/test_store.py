import math
import os
import sqlite3
import runpy
from pathlib import Path
from dataclasses import replace

import pytest
from live_location.store import Event, Store


def event(**kw):
    return replace(Event(update_id=1, message_id=10, bot_id=111, owner_id=88,
                         original_at=1000, observed_at=1000, latitude=0.0,
                         longitude=0.0, live_period=900, edited=False, accuracy=5.0), **kw)


def test_lifecycle_restart_and_freshness(isolated_home):
    s = Store(isolated_home, 111, 88)
    assert s.status(now=1000)['status'] == 'empty'
    assert s.ingest(event(), now=1000) == 'accepted'
    assert s.status(now=1001)['latitude'] == 0
    assert s.status(now=1301)['status'] == 'stale'
    assert 'latitude' not in s.status(now=1301)
    assert s.ingest(event(update_id=2, edited=True, observed_at=1400, latitude=1, live_period=1800), now=1400) == 'accepted'
    assert Store(isolated_home, 111, 88).status(now=1401)['latitude'] == 1
    assert s.status(now=1900)['status'] == 'expired'
    assert 'latitude' not in s.status(now=1900)
    assert s.ingest(event(update_id=3, edited=True, observed_at=1450, live_period=None), now=1450) == 'stopped'
    assert s.status(now=1451)['status'] == 'stopped'
    assert s.ingest(event(update_id=4, edited=True, observed_at=1500), now=1500) == 'ignored'


def test_order_dedupe_new_stream_and_unknown_edits(isolated_home):
    s = Store(isolated_home, 111, 88)
    assert s.ingest(event(edited=True), now=1000) == 'ignored'
    s.ingest(event(), now=1000)
    assert s.ingest(event(latitude=5), now=1000) == 'ignored'
    s.ingest(event(update_id=3, edited=True, observed_at=1100, latitude=2), now=1100)
    assert s.ingest(event(update_id=2, edited=True, observed_at=1200), now=1200) == 'ignored'
    assert s.ingest(event(update_id=4, edited=True, observed_at=1050), now=1200) == 'ignored'
    s.ingest(event(update_id=5, message_id=11, original_at=1200, observed_at=1200), now=1200)
    assert s.ingest(event(update_id=6, edited=True, observed_at=1250, live_period=None), now=1250) == 'ignored'
    assert s.status(now=1250)['status'] == 'fresh'
    # Receiving a delayed point does not make an old source timestamp fresh.
    s.ingest(event(update_id=7, message_id=11, original_at=1200, edited=True, observed_at=1250), now=1800)
    assert s.status(now=1800)['status'] == 'stale'


def test_forget_barrier_is_persistent_and_bounded(isolated_home):
    s = Store(isolated_home, 111, 88)
    s.ingest(event(), now=1000)
    s.forget(now=1100)
    s = Store(isolated_home, 111, 88)
    assert s.status(now=1100)['status'] == 'forgotten'
    assert s.ingest(event(update_id=5, edited=True, observed_at=1150), now=1150) == 'ignored'
    assert s.ingest(event(update_id=6, message_id=12, original_at=1050, observed_at=1050), now=1150) == 'ignored'
    for n in range(20, 1020):
        s.ingest(event(update_id=n, message_id=n, original_at=1200+n, observed_at=1200+n), now=1200+n)
    with sqlite3.connect(s.path) as db:
        assert db.execute('SELECT count(*) FROM state').fetchone()[0] == 1
    assert os.stat(s.path).st_mode & 0o777 == 0o600
    assert os.stat(s.path.parent).st_mode & 0o777 == 0o700
    assert s.path.stat().st_size < 65536


@pytest.mark.parametrize('change', [
    {'latitude': math.nan}, {'longitude': math.inf}, {'latitude': 91}, {'longitude': -181},
    {'latitude': True}, {'accuracy': -1}, {'accuracy': math.nan}, {'live_period': -5},
    {'observed_at': 2000}, {'original_at': 1001}, {'owner_id': 99}, {'bot_id': 222},
    {'latitude': 10**400}, {'live_period': True}, {'update_id': -1},
])
@pytest.mark.parametrize('period', [900, None])
def test_invalid_points_rejected(isolated_home, change, period):
    s = Store(isolated_home, 111, 88)
    assert s.ingest(event(**{'live_period': period, **change}), now=1000) == 'ignored'
    assert s.status(now=1000)['status'] == 'empty'


def test_profile_and_binding_isolation(isolated_home, tmp_path):
    a = Store(isolated_home, 111, 88)
    a.ingest(event(), now=1000)
    b = Store(tmp_path / 'second', 111, 88)
    assert b.status(now=1000)['status'] == 'empty'
    assert Store(isolated_home, 222, 88).status(now=1000)['status'] == 'binding_mismatch'


def test_static_replaces_live_and_is_saved_until_replaced_or_forgotten(isolated_home):
    s = Store(isolated_home, 111, 88)
    assert s.ingest(event(), now=1000) == 'accepted'
    pin = event(update_id=2, message_id=11, original_at=1100, observed_at=1100,
                live_period=None, latitude=2)
    assert s.ingest(pin, now=1100) == 'accepted'
    saved = Store(isolated_home, 111, 88).status(now=100000)
    assert saved == {'status': 'saved', 'kind': 'static', 'latitude': 2,
                     'longitude': 0, 'accuracy_m': 5, 'observed_at': 1100,
                     'received_at': 1100, 'expires_at': None, 'age_seconds': 98900}
    assert s.ingest(event(update_id=3, edited=True, observed_at=1200), now=1200) == 'ignored'
    assert s.ingest(replace(pin, update_id=4, edited=True, observed_at=1200), now=1200) == 'ignored'
    assert s.status(now=1200)['latitude'] == 2
    assert s.ingest(event(update_id=5, message_id=12, original_at=1200, observed_at=1200), now=1200) == 'accepted'
    assert s.status(now=1200)['kind'] == 'live'
    assert s.status(now=1200)['status'] == 'fresh'
    assert s.ingest(replace(pin, update_id=6), now=1200) == 'ignored'
    s.forget(now=1300)
    assert s.status(now=1300) == {'status': 'forgotten'}
    assert s.ingest(replace(pin, update_id=7, message_id=13), now=1400) == 'ignored'
    assert s.ingest(replace(pin, update_id=8, message_id=14, original_at=1400,
                            observed_at=1400), now=1400) == 'accepted'
    s.forget(now=1500)
    assert s.ingest(replace(pin, update_id=9, message_id=14, original_at=1400,
                            observed_at=1600, edited=True), now=1600) == 'ignored'
    assert s.status(now=1600) == {'status': 'forgotten'}


def test_symlink_store_refused(isolated_home, tmp_path):
    directory = isolated_home / 'live-location'
    directory.mkdir()
    outside = tmp_path / 'outside'
    outside.write_text('untouched')
    (directory / 'latest.sqlite3').symlink_to(outside)
    with pytest.raises(OSError):
        Store(isolated_home, 111, 88).status(now=1000)
    assert outside.read_text() == 'untouched'


@pytest.mark.parametrize('state,expected', [
    ('live', 'fresh'), ('stale', 'stale'), ('expired', 'expired'),
    ('stopped', 'stopped'), ('forgotten', 'forgotten'), ('mismatch', 'binding_mismatch'),
])
def test_legacy_additive_migration_backup_restore_and_barriers(isolated_home, tmp_path, state, expected):
    legacy = runpy.run_path(str(Path(__file__).parent / 'fixtures' / 'v0_1' / 'live_location' / 'store.py'))
    old = legacy['Store'](isolated_home, 111, 88)
    old.ingest(event(), now=1000)
    now = {'stale': 1400, 'expired': 2000}.get(state, 1100)
    if state == 'stopped':
        old.ingest(event(update_id=2, edited=True, observed_at=1050, live_period=None), now=1050)
    if state == 'forgotten':
        old.forget(now=1050)
    owner = 99 if state == 'mismatch' else 88
    backup = tmp_path / 'backup.sqlite3'
    with sqlite3.connect(old.path) as source, sqlite3.connect(backup) as target:
        source.row_factory = sqlite3.Row
        before = dict(source.execute('SELECT * FROM state').fetchone())
        assert 'kind' not in before
        source.backup(target)
    backup.chmod(0o600)
    s = Store(isolated_home, 111, owner)
    status = s.status(now=now)
    assert status['status'] == expected
    with sqlite3.connect(s.path) as db:
        db.row_factory = sqlite3.Row
        migrated = dict(db.execute('SELECT * FROM state').fetchone())
        assert migrated.pop('kind') == 'live'
        assert migrated == before
        assert db.execute('SELECT count(*) FROM state').fetchone()[0] == 1
    assert os.stat(s.path).st_mode & 0o777 == 0o600
    assert os.stat(s.directory).st_mode & 0o777 == 0o700
    assert s.ingest(event(update_id=1, latitude=5), now=now) == 'ignored'
    assert s.ingest(event(update_id=3, message_id=9, live_period=None), now=now) == 'ignored'
    if state == 'forgotten':
        assert s.ingest(event(update_id=4, edited=True, observed_at=1100), now=now) == 'ignored'
        assert s.ingest(event(update_id=5, message_id=11, live_period=None), now=now) == 'ignored'
    if state == 'stopped':
        assert s.ingest(event(update_id=4, edited=True, observed_at=1100), now=now) == 'ignored'
    # Exercise restoring the consistent pre-upgrade backup only into another
    # disposable home, and migrate it again. The backup itself stays legacy.
    restored_home = tmp_path / 'restored'
    restored = Store(restored_home, 111, owner)
    restored.directory.mkdir(parents=True, mode=0o700)
    with sqlite3.connect(backup) as source, sqlite3.connect(restored.path) as target:
        source.backup(target)
        assert 'kind' not in {c[1] for c in source.execute('PRAGMA table_info(state)')}
    assert restored.status(now=now) == status


def test_static_and_live_sources_remain_singleton(isolated_home):
    s = Store(isolated_home, 111, 88)
    for n in range(1, 101):
        assert s.ingest(event(update_id=n, message_id=n, original_at=1000+n,
                              observed_at=1000+n, live_period=900 if n % 2 else None), now=1000+n) == 'accepted'
    assert s.status(now=2000)['status'] == 'saved'
    with sqlite3.connect(s.path) as db:
        assert db.execute('SELECT count(*) FROM state').fetchone()[0] == 1
    assert s.path.stat().st_size < 65536


def test_failed_access_rolls_back_additive_migration(isolated_home):
    legacy = runpy.run_path(str(Path(__file__).parent / 'fixtures' / 'v0_1' / 'live_location' / 'store.py'))
    old = legacy['Store'](isolated_home, 111, 88)
    old.ingest(event(), now=1000)

    class FailedRead(Store):
        def _matches(self, row):
            raise sqlite3.OperationalError('synthetic failure after schema upgrade')

    with pytest.raises(sqlite3.OperationalError):
        FailedRead(isolated_home, 111, 88).status(now=1000)
    with sqlite3.connect(old.path) as db:
        assert 'kind' not in {c[1] for c in db.execute('PRAGMA table_info(state)')}
        assert db.execute('SELECT latitude, watermark FROM state').fetchone() == (0, 1)
    assert Store(isolated_home, 111, 88).status(now=1000)['status'] == 'fresh'
