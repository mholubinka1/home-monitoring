import argparse
import getpass
import sys
from collections.abc import Callable, Sequence

from hive_app.common.config import get_settings
from hive_app.data.hive_client import HiveApiSource
from hive_app.data.mysql.client import MariaDBClient


def _prompt_for_sms_code() -> str:
    return getpass.getpass("Enter the Hive SMS 2FA code: ")


def main(
    argv: Sequence[str] | None = None,
    code_provider: Callable[[], str] | None = None,
) -> int:
    """Interactive Hive SMS login: run via `docker exec -it hive-app python -m
    hive_app.login --config-file <path>`. Persists the resulting auth state
    only if the whole login succeeds, so a failed attempt never clobbers an
    existing state file."""
    parser = argparse.ArgumentParser(description="Interactive Hive SMS login.")
    parser.add_argument("--config-file")
    args = parser.parse_args(argv)

    try:
        # get_settings reports its own failures and calls sys.exit(1).
        settings = get_settings(config_file_path=args.config_file)
    except SystemExit:
        print(f"Could not load config from {args.config_file}.", file=sys.stderr)
        return 1

    source = HiveApiSource(settings.hive, MariaDBClient(settings.mariadb))
    try:
        state = source.interactive_login(code_provider or _prompt_for_sms_code)
        source.persist_auth_state(state)
    except Exception as e:
        print(f"Hive login failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(
        f"Hive login successful; auth state saved to {settings.hive.auth_state_path}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
