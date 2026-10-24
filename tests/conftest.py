import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from hermes_constants import set_hermes_home_override, reset_hermes_home_override


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('Offline tests forbid real socket connections')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', no_network)
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    token = set_hermes_home_override(home)
    yield home
    reset_hermes_home_override(token)
