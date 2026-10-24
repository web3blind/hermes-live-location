import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import yaml
from live_location.__main__ import main
from live_location.config import ConfigError, load_config
from live_location.store import Event, Store
from hermes_constants import set_hermes_home_override, reset_hermes_home_override

ROOT = Path(__file__).resolve().parents[1]


def configure(home, **settings):
    values = {'enabled': True, 'owner_user_id': 88, 'bot_id': 111, **settings}
    (home / 'config.yaml').write_text(yaml.safe_dump({'plugins': {
        'enabled': ['hermes-live-location'],
        'entries': {'hermes-live-location': {'settings': values}}}}))


def seed(home):
    now = int(time.time())
    Store(home, 111, 88).ingest(Event(1, 10, 111, 88, now, now, 0.0, 0.0, 900, False), now=now)


def test_current_status_forget_and_context(isolated_home, capsys, tmp_path):
    configure(isolated_home)
    seed(isolated_home)
    assert main(['current']) == 0
    assert json.loads(capsys.readouterr().out)['latitude'] == 0.0
    assert main(['status']) == 0
    assert 'latitude' not in json.loads(capsys.readouterr().out)
    second = tmp_path / 'second'
    second.mkdir()
    configure(second)
    token = set_hermes_home_override(second)
    try:
        assert main(['current']) == 2
        assert json.loads(capsys.readouterr().out)['status'] == 'empty'
    finally:
        reset_hermes_home_override(token)
    assert main(['forget']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'forgotten'
    assert main(['current']) == 2
    assert 'latitude' not in json.loads(capsys.readouterr().out)


def test_native_cli_subprocess(isolated_home):
    configure(isolated_home)
    seed(isolated_home)
    env = {'PATH': os.environ['PATH'], 'HOME': str(isolated_home.parent),
           'HERMES_HOME': str(isolated_home),
           'PYTHONPATH': str(ROOT) + os.pathsep + str(Path(__import__('hermes_constants').__file__).parent)}
    for command, code in [('current', 0), ('status', 0), ('forget', 0), ('current', 2)]:
        result = subprocess.run([sys.executable, '-m', 'live_location', command],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == code, result.stderr
        data = json.loads(result.stdout)
        if command != 'current' or code:
            assert 'latitude' not in data
        assert not result.stderr


def test_saved_cli_contract_and_native_forget(isolated_home, capsys):
    configure(isolated_home)
    # Deliberately much older than the live stale threshold; static is a place,
    # not a currently observed GPS fix, and must not expire silently.
    Store(isolated_home, 111, 88).ingest(Event(1, 10, 111, 88, 1000, 1000, 0., 0., None, False), now=1000)
    assert main(['current']) == 0
    current = json.loads(capsys.readouterr().out)
    assert current['status'] == 'saved' and current['kind'] == 'static'
    assert current['latitude'] == 0 and current['longitude'] == 0
    assert current['age_seconds'] > 300 and current['expires_at'] is None
    assert main(['status']) == 0
    status = json.loads(capsys.readouterr().out)
    assert status['kind'] == 'static' and status['status'] == 'saved'
    assert not {'latitude', 'longitude', 'accuracy_m'} & status.keys()
    env = {'PATH': os.environ['PATH'], 'HOME': str(isolated_home.parent),
           'HERMES_HOME': str(isolated_home),
           'PYTHONPATH': str(ROOT) + os.pathsep + str(Path(__import__('hermes_constants').__file__).parent)}
    for command, code, expected in [('current', 0, 'saved'), ('status', 0, 'saved'),
                                    ('forget', 0, 'forgotten'), ('current', 2, 'forgotten')]:
        result = subprocess.run([sys.executable, '-m', 'live_location', command], cwd=ROOT,
                                env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == code, result.stderr
        data = json.loads(result.stdout)
        assert data['status'] == expected
        if command != 'current' or code:
            assert not {'latitude', 'longitude', 'accuracy_m'} & data.keys()
        assert not result.stderr


@pytest.mark.parametrize('settings', [
    {'owner_user_id': 0}, {'bot_id': True}, {'enabled': 'yes'},
    {'stale_after_seconds': 0}, {'stale_after_seconds': 86401}, {'owner_user_id': []},
])
def test_invalid_settings_fail_closed(isolated_home, capsys, settings):
    configure(isolated_home, **settings)
    with pytest.raises(ConfigError):
        load_config(isolated_home)
    assert main(['current']) == 1
    assert json.loads(capsys.readouterr().out) == {'status': 'unavailable'}


def test_default_disabled_and_corrupt_state(isolated_home, capsys):
    assert main(['current']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'disabled'
    configure(isolated_home)
    seed(isolated_home)
    path = isolated_home / 'live-location' / 'latest.sqlite3'
    path.write_bytes(b'not a database')
    assert main(['current']) == 1
    assert json.loads(capsys.readouterr().out) == {'status': 'unavailable'}
