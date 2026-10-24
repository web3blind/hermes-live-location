"""Actual Hermes discovery -> connected TelegramApplication.process_update -> SQLite.
Only transport/polling/background services/model boundary are replaced.
"""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from telegram import Update
from telegram.ext import TypeHandler
from telegram.request import BaseRequest
from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from hermes_cli.plugins import PluginManager
from live_location.store import Store

ROOT = Path(__file__).resolve().parents[1]
_ORIGINAL_DATES = {}


@pytest.fixture(autouse=True)
def reset_message_dates():
    _ORIGINAL_DATES.clear()
    yield
    _ORIGINAL_DATES.clear()


def configure(home, **settings):
    settings = {'enabled': True, 'bot_id': 111, 'owner_user_id': 88, **settings}
    (home / 'config.yaml').write_text(yaml.safe_dump({'plugins': {'enabled': ['hermes-live-location'], 'entries': {
        'hermes-live-location': {'enabled': True, 'settings': settings}}}}))


class Offline(BaseRequest):
    @property
    def read_timeout(self):
        return 1

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        assert url.rsplit('/', 1)[-1] == 'getMe', 'No polls/sends/downloads permitted'
        return 200, json.dumps({'ok': True, 'result': {
            'id': 111, 'is_bot': True, 'first_name': 'Offline', 'username': 'offline_bot'}}).encode()


def incoming(bot, uid, *, mid=10, edited=False, period: int | None = 900, lat=0.0,
             user=88, chat=88, forward=False, group=False, date=None, observed=None, text=None):
    now = int(time.time())
    # Telegram preserves message.date across edits. A newly constructed fixture
    # must not acquire a new original date merely because the wall clock ticks.
    original = _ORIGINAL_DATES.setdefault((bot.id, chat, mid), now) if date is None else date
    msg = {'message_id': mid, 'date': original, 'chat': {
        'id': chat, 'type': 'group' if group else 'private'},
        'from': {'id': user, 'is_bot': False, 'first_name': 'Synthetic'}}
    if text is None:
        msg['location'] = {'latitude': lat, 'longitude': 0.0}
        if period is not None:
            msg['location']['live_period'] = period
    else:
        msg['text'] = text
    if edited:
        msg['edit_date'] = observed or now
    if forward:
        msg['forward_origin'] = {'type': 'hidden_user', 'date': now, 'sender_user_name': 'Synthetic'}
    return Update.de_json({'update_id': uid, 'edited_message' if edited else 'message': msg}, bot)


@asynccontextmanager
async def connected(monkeypatch, home, source=ROOT):
    # Install ONLY into a disposable home to exercise actual manifest discovery and
    # generated-package imports, not a synthetic context/handler-manager facade.
    destination = home / 'plugins' / 'hermes-live-location'
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache'))
    manager = PluginManager(scope_key=str(home.resolve()))
    monkeypatch.setenv('HERMES_BUNDLED_PLUGINS', str(home / 'empty-bundled'))
    manager.discover_and_load()
    factories = manager.get_platform_handler_factories('telegram')
    assert len(factories) == 1
    monkeypatch.setattr('hermes_cli.plugins.get_plugin_manager', lambda: manager)
    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', '88')
    monkeypatch.setenv('TELEGRAM_WEBHOOK_URL', '')
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token='111:offline-test'))
    monkeypatch.setattr(adapter, '_build_ptb_requests', AsyncMock(return_value=(Offline(), Offline())))
    monkeypatch.setattr(adapter, '_start_polling_mode', AsyncMock())
    monkeypatch.setattr(adapter, '_start_post_connect_housekeeping', lambda: None)
    monkeypatch.setattr(adapter, '_restart_task_attr', lambda name, coroutine: coroutine.close())
    monkeypatch.setattr(adapter, '_set_status_indicator', AsyncMock())
    delivered = []
    adapter._message_handler = AsyncMock()
    monkeypatch.setattr(adapter, '_start_session_processing', lambda event, key: delivered.append(event) or True)
    assert await adapter.connect()
    try:
        yield adapter, adapter._app, delivered
    finally:
        await adapter.disconnect()
        session_store = getattr(adapter, '_session_store', None)
        if session_store is not None:
            session_store.close_all_db_handles()


@pytest.mark.asyncio
@pytest.mark.parametrize('timedelta_mode', [False, True])
async def test_connected_lifecycle_quiet_and_static_pin_preserved(monkeypatch, isolated_home, timedelta_mode):
    monkeypatch.setenv('PTB_TIMEDELTA', '1' if timedelta_mode else '0')
    configure(isolated_home)
    async with connected(monkeypatch, isolated_home) as (adapter, app, delivered):
        store = Store(isolated_home, 111, 88)
        observer = AsyncMock()
        app.add_handler(TypeHandler(Update, observer), group=98)
        await app.process_update(incoming(app.bot, 1))
        assert store.status()['status'] == 'fresh'
        await app.process_update(incoming(app.bot, 2, edited=True, lat=1))
        assert store.status()['latitude'] == 1
        await app.process_update(incoming(app.bot, 2, edited=True, lat=2))
        assert store.status()['latitude'] == 1
        # Force a clock-second boundary while creating the stop fixture; only
        # edit_date changes, never the original date of this Telegram message.
        later = int(time.time()) + 2
        with monkeypatch.context() as clock_patch:
            clock_patch.setitem(globals(), 'time', SimpleNamespace(time=lambda: later))
            stopped = incoming(app.bot, 3, edited=True, period=None)
        assert stopped.edited_message is not None
        assert stopped.edited_message.edit_date is not None
        assert stopped.edited_message.date.timestamp() < stopped.edited_message.edit_date.timestamp()
        await app.process_update(stopped)
        assert store.status()['status'] == 'stopped'
        await app.process_update(incoming(app.bot, 4, edited=True))
        assert store.status()['status'] == 'stopped'
        assert delivered == [] and observer.await_count == 0
        assert '111:3' in adapter._seen_update_ids
        await app.process_update(incoming(app.bot, 5, mid=11, period=None))
        assert len(delivered) == 1
        assert observer.await_count == 1
        assert store.status()['status'] == 'saved'
        assert store.status()['kind'] == 'static'
        await app.process_update(incoming(app.bot, 6, mid=12, text='ordinary hello'))
        await asyncio.gather(*adapter._pending_text_batch_tasks.values())
        assert len(delivered) == 2


@pytest.mark.asyncio
async def test_static_live_ordering_and_venue_core_once(monkeypatch, isolated_home):
    configure(isolated_home)
    async with connected(monkeypatch, isolated_home) as (_, app, delivered):
        store = Store(isolated_home, 111, 88)
        observer = AsyncMock()
        app.add_handler(TypeHandler(Update, observer), group=98)
        await app.process_update(incoming(app.bot, 1))
        pin = incoming(app.bot, 2, mid=11, period=None, lat=2)
        await app.process_update(pin)
        await app.process_update(pin)
        assert store.status()['status'] == 'saved'
        assert store.status()['latitude'] == 2
        assert len(delivered) == observer.await_count == 1
        await app.process_update(incoming(app.bot, 3, edited=True, lat=3))
        await app.process_update(incoming(app.bot, 4, edited=True, period=None))
        await app.process_update(incoming(app.bot, 5, mid=11, edited=True, lat=4))
        assert store.status()['latitude'] == 2
        assert len(delivered) == observer.await_count == 1
        await app.process_update(incoming(app.bot, 6, mid=12, lat=5))
        assert store.status()['status'] == 'fresh'
        assert store.status()['kind'] == 'live'
        assert store.status()['latitude'] == 5
        # Core's existing location contract also covers venue.location.
        data = incoming(app.bot, 7, mid=13, period=None, lat=6).to_dict()
        data['message']['venue'] = {'location': data['message'].pop('location'),
                                    'title': 'Synthetic', 'address': 'Synthetic'}
        venue = Update.de_json(data, app.bot)
        await app.process_update(venue)
        await app.process_update(venue)
        assert store.status()['status'] == 'saved'
        assert store.status()['latitude'] == 6
        assert len(delivered) == observer.await_count == 2
        data['update_id'] = 8
        data['edited_message'] = data.pop('message')
        data['edited_message']['edit_date'] = int(time.time())
        await app.process_update(Update.de_json(data, app.bot))
        assert len(delivered) == observer.await_count == 2
        assert store.status()['latitude'] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs,core_count', [
    ({'user': 99}, 0), ({'chat': 99}, 1), ({'group': True}, 1),
    ({'forward': True}, 1), ({'user': 99, 'edited': True}, 0),
])
async def test_foreign_static_keeps_original_auth_and_no_persistence(monkeypatch, isolated_home, kwargs, core_count):
    configure(isolated_home)
    async with connected(monkeypatch, isolated_home) as (_, app, delivered):
        await app.process_update(incoming(app.bot, 1, period=None, **kwargs))
        assert Store(isolated_home, 111, 88).status()['status'] == 'empty'
        assert len(delivered) == core_count


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['disabled', 'invalid', 'wrong_bot', 'store'])
async def test_static_persistence_failure_does_not_swallow_core(monkeypatch, isolated_home, caplog, failure):
    configure(isolated_home, **{'disabled': {'enabled': False}, 'invalid': {'owner_user_id': 0},
                               'wrong_bot': {'bot_id': 222}, 'store': {}}[failure])
    async with connected(monkeypatch, isolated_home) as (_, app, delivered):
        if failure == 'store':
            store = Store(isolated_home, 111, 88)
            store.status()
            store.path.write_bytes(b'broken database')
        await app.process_update(incoming(app.bot, 1, period=None, lat=12.3456789))
        assert len(delivered) == 1
        await app.process_update(incoming(app.bot, 2, edited=True, lat=12.3456789))
        assert len(delivered) == 1
        assert '12.3456789' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [
    {'user': 99}, {'chat': 99}, {'group': True}, {'forward': True},
    {'edited': True}, {'lat': 91},
])
async def test_rejected_live_never_leaks_to_core(monkeypatch, isolated_home, kwargs):
    configure(isolated_home)
    async with connected(monkeypatch, isolated_home) as (_, app, delivered):
        await app.process_update(incoming(app.bot, 1, **kwargs))
        assert Store(isolated_home, 111, 88).status()['status'] == 'empty'
        assert delivered == []


@pytest.mark.asyncio
@pytest.mark.parametrize('period', [900, None])
async def test_captured_profile_forget_and_store_failure(monkeypatch, isolated_home, tmp_path, caplog, period):
    configure(isolated_home)
    async with connected(monkeypatch, isolated_home) as (_, app, delivered):
        other = tmp_path / 'other'
        other.mkdir()
        token = set_hermes_home_override(other)
        try:
            await app.process_update(incoming(app.bot, 1, period=period))
            assert not (other / 'live-location').exists()
        finally:
            reset_hermes_home_override(token)
        store = Store(isolated_home, 111, 88)
        assert store.status()['status'] == ('saved' if period is None else 'fresh')
        store.forget()
        await app.process_update(incoming(app.bot, 2, edited=True))
        assert store.status()['status'] == 'forgotten'
        # Deliberately sabotage ONLY the disposable store; no payload in logs.
        store.path.unlink()
        store.path.mkdir()
        await app.process_update(incoming(app.bot, 3, mid=11, lat=12.3456789))
        assert len(delivered) == (1 if period is None else 0)
        assert '12.3456789' not in caplog.text
        assert 'local state unavailable' in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('settings', [{'enabled': False}, {'owner_user_id': 0}, {'bot_id': 222}])
async def test_unconfigured_disabled_or_wrong_bot_is_quiet(monkeypatch, isolated_home, settings):
    configure(isolated_home, **settings)
    async with connected(monkeypatch, isolated_home) as (_, app, delivered):
        await app.process_update(incoming(app.bot, 1))
        await app.process_update(incoming(app.bot, 2, edited=True, period=None))
        assert not (isolated_home / 'live-location').exists()
        assert delivered == []


@pytest.mark.asyncio
async def test_same_profile_restart_and_corrupt_database(monkeypatch, isolated_home, caplog):
    configure(isolated_home)
    async with connected(monkeypatch, isolated_home) as (_, app, delivered):
        await app.process_update(incoming(app.bot, 1))
        assert not delivered
    store = Store(isolated_home, 111, 88)
    assert store.status()['status'] == 'fresh'
    # Existing Hermes durable receipts and plugin state both survive a rebuild.
    # Reuse discovered plugin copy rather than overwriting any existing profile.
    (isolated_home / 'plugins' / 'hermes-live-location').rename(isolated_home / 'previous-copy')
    async with connected(monkeypatch, isolated_home) as (_, app, delivered):
        await app.process_update(incoming(app.bot, 1, lat=9))
        assert store.status()['latitude'] == 0
        await app.process_update(incoming(app.bot, 2, edited=True, lat=1))
        assert store.status()['latitude'] == 1
        store.path.write_bytes(b'broken database')
        await app.process_update(incoming(app.bot, 3, edited=True, lat=2))
        assert not delivered
        assert 'local state unavailable' in caplog.text
        assert 'broken database' not in caplog.text


@pytest.mark.asyncio
async def test_native_upgrade_from_wired_v01_and_repeat_rediscovery(monkeypatch, isolated_home):
    from hermes_cli import plugins
    configure(isolated_home)
    async with connected(monkeypatch, isolated_home, ROOT / 'tests' / 'fixtures' / 'v0_1') as (adapter, app, delivered):
        await app.process_update(incoming(app.bot, 1))
        store = Store(isolated_home, 111, 88)
        assert store.status()['status'] == 'fresh'
        old_handlers = [h for handlers in app.handlers.values() for h in handlers]
        destination = isolated_home / 'plugins' / 'hermes-live-location'
        shutil.rmtree(destination)
        shutil.copytree(ROOT, destination, ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache'))
        manager = plugins.get_plugin_manager()
        manager.discover_and_load(force=True)
        adapter.rewire_plugin_handlers()
        new_handlers = [h for handlers in app.handlers.values() for h in handlers]
        assert len(new_handlers) == len(old_handlers) + 1
        assert all(h in new_handlers for h in old_handlers)
        for _ in range(3):
            manager.discover_and_load(force=True)
            adapter.rewire_plugin_handlers()
            assert [h for handlers in app.handlers.values() for h in handlers] == new_handlers
        pin = incoming(app.bot, 2, mid=11, period=None, lat=2)
        await app.process_update(pin)
        await app.process_update(pin)
        assert store.status()['status'] == 'saved'
        assert store.status()['latitude'] == 2
        assert len(delivered) == 1
        await app.process_update(incoming(app.bot, 3, edited=True, lat=3))
        assert store.status()['latitude'] == 2
        await app.process_update(incoming(app.bot, 4, mid=12, lat=4))
        await app.process_update(incoming(app.bot, 5, mid=12, edited=True, lat=5))
        assert store.status()['latitude'] == 5
        assert store.status()['status'] == 'fresh'
        assert len(delivered) == 1


@pytest.mark.asyncio
async def test_unknown_edit_without_edit_date_is_quiet_and_text_passes(monkeypatch, isolated_home):
    configure(isolated_home)
    async with connected(monkeypatch, isolated_home) as (adapter, app, delivered):
        data = incoming(app.bot, 1, edited=True, period=None).to_dict()
        data['edited_message'].pop('edit_date')
        await app.process_update(Update.de_json(data, app.bot))
        assert not delivered
        assert Store(isolated_home, 111, 88).status()['status'] == 'empty'
        await app.process_update(incoming(app.bot, 2, text='ordinary synthetic message'))
        await asyncio.gather(*adapter._pending_text_batch_tasks.values())
        assert len(delivered) == 1
