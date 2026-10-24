"""Profile-local JSON reader. Only `current` intentionally outputs coordinates."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from hermes_constants import get_hermes_home
from .config import ConfigError, load_config
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read or forget this profile saved place or live location')
    parser.add_argument('command', choices=('status', 'current', 'forget'))
    args = parser.parse_args(argv)
    try:
        home = Path(get_hermes_home()).resolve()
        config = load_config(home)
        if not config.enabled and args.command != 'forget':
            result = {'status': 'disabled'}
        else:
            store = Store(home, config.bot_id, config.owner_user_id, config.stale_after_seconds)
            result = store.forget() if args.command == 'forget' else store.status()
        if args.command != 'current':
            result = {k: v for k, v in result.items() if k not in ('latitude', 'longitude', 'accuracy_m')}
        print(json.dumps(result, allow_nan=False, sort_keys=True))
        return 0 if args.command != 'current' or result['status'] in ('fresh', 'saved') else 2
    except (ConfigError, OSError, sqlite3.Error):
        print(json.dumps({'status': 'unavailable'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
