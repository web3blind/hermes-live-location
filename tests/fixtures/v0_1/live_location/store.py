"""Bounded latest-point store. No transport, geocoding, logs or route archive."""
from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path
import sqlite3
import stat
import time


@dataclass(frozen=True)
class Event:
    update_id: int
    message_id: int
    bot_id: int
    owner_id: int
    original_at: float
    observed_at: float
    latitude: float
    longitude: float
    live_period: int | None
    edited: bool
    accuracy: float | None = None


def finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def positive_int(value):
    return type(value) is int and 0 < value <= 9223372036854775807


class Store:
    def __init__(self, home: Path, bot_id: int, owner_id: int, stale_after=300):
        self.directory = Path(home) / 'live-location'
        self.path = self.directory / 'latest.sqlite3'
        self.bot_id, self.owner_id, self.stale_after = bot_id, owner_id, stale_after

    @contextmanager
    def _db(self):
        # Refuse symlinks and foreign-owned paths. Do not chmod profile ancestors.
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise OSError('unsafe location directory')
        self.directory.chmod(0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise OSError('unsafe location file')
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA secure_delete=ON')
            db.execute('CREATE TABLE IF NOT EXISTS state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), '
                       'bot INTEGER, owner INTEGER, message INTEGER DEFAULT 0, watermark INTEGER DEFAULT -1, '
                       'barrier REAL DEFAULT 0, original REAL, observed REAL, received REAL, expires REAL, '
                       'latitude REAL, longitude REAL, accuracy REAL, stopped INTEGER DEFAULT 0)')
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO state(singleton, bot, owner) VALUES (1, ?, ?)',
                       (self.bot_id, self.owner_id))
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _matches(self, row):
        return row['bot'] == self.bot_id and row['owner'] == self.owner_id

    def ingest(self, event: Event, *, now=None):
        now = time.time() if now is None else now
        e = event
        if (e.bot_id != self.bot_id or e.owner_id != self.owner_id
                or type(e.update_id) is not int or not 0 <= e.update_id <= 9223372036854775807
                or not positive_int(e.message_id)
                or not finite(e.original_at) or not finite(e.observed_at)
                or e.original_at > e.observed_at or e.observed_at > now + 30):
            return 'ignored'
        with self._db() as db:
            row = db.execute('SELECT * FROM state').fetchone()
            if not self._matches(row) or e.update_id <= row['watermark']:
                return 'ignored'
            same = e.message_id == row['message'] and row['original'] is not None
            if same:
                if (not e.edited or row['stopped'] or e.original_at != row['original']
                        or e.observed_at < row['observed']):
                    return 'ignored'
                if e.live_period is None or e.live_period == 0:
                    db.execute('UPDATE state SET stopped=1, watermark=?, received=?, observed=? WHERE singleton=1',
                               (e.update_id, now, e.observed_at))
                    return 'stopped'
            elif (e.edited or e.message_id <= row['message'] or e.original_at <= row['barrier']
                  or (row['original'] is not None and e.original_at < row['original'])):
                return 'ignored'
            if (not positive_int(e.live_period) or e.live_period > 2147483647
                    or not finite(e.latitude) or not -90 <= e.latitude <= 90
                    or not finite(e.longitude) or not -180 <= e.longitude <= 180
                    or (e.accuracy is not None and (not finite(e.accuracy) or not 0 <= e.accuracy <= 1500))):
                return 'ignored'
            expiry = row['expires'] if same else e.original_at + e.live_period
            db.execute('UPDATE state SET message=?, watermark=?, original=?, observed=?, received=?, expires=?, '
                       'latitude=?, longitude=?, accuracy=?, stopped=0 WHERE singleton=1',
                       (e.message_id, e.update_id, e.original_at, e.observed_at, now, expiry,
                        e.latitude, e.longitude, e.accuracy))
            return 'accepted'

    def status(self, *, now=None):
        now = time.time() if now is None else now
        with self._db() as db:
            row = db.execute('SELECT * FROM state').fetchone()
            if not self._matches(row):
                return {'status': 'binding_mismatch'}
            if row['latitude'] is None:
                return {'status': 'forgotten' if row['barrier'] else 'empty'}
            status = ('stopped' if row['stopped'] else 'expired' if now >= row['expires'] else
                      'stale' if now - row['observed'] > self.stale_after or now < row['observed'] else 'fresh')
            result = {'status': status, 'observed_at': row['observed'], 'received_at': row['received'],
                      'expires_at': row['expires'], 'age_seconds': max(0, now - row['observed'])}
            if status == 'fresh':
                result.update(latitude=row['latitude'], longitude=row['longitude'], accuracy_m=row['accuracy'])
            return result

    def forget(self, *, now=None):
        now = time.time() if now is None else now
        with self._db() as db:
            # Rebinding is only explicit local forget; retain message/update fences.
            db.execute('UPDATE state SET bot=?, owner=?, barrier=max(barrier, ?), original=NULL, observed=NULL, '
                       'received=NULL, expires=NULL, latitude=NULL, longitude=NULL, accuracy=NULL, stopped=0 '
                       'WHERE singleton=1', (self.bot_id, self.owner_id, now))
        return {'status': 'forgotten'}