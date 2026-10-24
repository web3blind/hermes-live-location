"""Validated profile-local configuration; no environment-global profile cache."""
from dataclasses import dataclass
from pathlib import Path
import yaml
from .store import positive_int

PLUGIN_ID = 'hermes-live-location'


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    owner_user_id: int = 0
    bot_id: int = 0
    stale_after_seconds: int = 300


def load_config(home: Path):
    try:
        path = home / 'config.yaml'
        data = yaml.safe_load(path.read_text()) if path.exists() else {}
        plugins = (data or {}).get('plugins', {})
        entry = plugins.get('entries', {}).get(PLUGIN_ID, {})
        values = entry.get('settings', {})
        enabled = values.get('enabled', False)
        owner, bot = values.get('owner_user_id', 0), values.get('bot_id', 0)
        stale = values.get('stale_after_seconds', 300)
        if type(enabled) is not bool or type(stale) is not int or not 1 <= stale <= 86400:
            raise ConfigError('invalid location settings')
        if any(type(value) is not int or not 0 <= value <= 9223372036854775807 for value in (owner, bot)):
            raise ConfigError('invalid identity settings')
        if enabled and (not positive_int(owner) or not positive_int(bot)):
            raise ConfigError('owner and bot must be explicit positive integer IDs')
        allowlist, disabled = plugins.get('enabled', []), plugins.get('disabled', [])
        if not isinstance(allowlist, list) or not isinstance(disabled, list):
            raise ConfigError('invalid plugin activation')
        active = (enabled and PLUGIN_ID in allowlist and PLUGIN_ID not in disabled
                  and entry.get('enabled', True) is not False)
        return Config(active, owner, bot, stale)
    except (AttributeError, TypeError, yaml.YAMLError, UnicodeError) as exc:
        raise ConfigError('invalid location settings') from None
