import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from common.local_day import get_local_timezone
from octopus_app.main import main

CONFIG: dict[str, Any] = {
    "octopus": {"account_number": "A-1234ABCD", "api_key": "sk_live_test"},
    "mariadb": {
        "host": "localhost",
        "port": 1,
        "database": "home_monitoring",
        "username": "test",
        "password": "test",
    },
    "data_refresh": {"refresh_interval_hours": 4, "retention_days": 45},
    "timezone": "America/New_York",
}


class _StoppedAtFirstClient(Exception):
    """Raised by the stand-in client so main() stops before any job runs."""

    def __init__(self, zone: str) -> None:
        super().__init__(zone)
        self.zone = zone


def test_the_configured_timezone_is_active_before_any_client_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text(yaml.safe_dump(CONFIG))
    monkeypatch.setattr(sys, "argv", ["octopus-app", "--config-file", str(config_file)])

    def build_client(_settings: object) -> None:
        raise _StoppedAtFirstClient(get_local_timezone().key)

    # The first thing main() builds after loading its settings.
    monkeypatch.setattr("octopus_app.main.MonitoringClient", build_client)

    with pytest.raises(_StoppedAtFirstClient) as stopped:
        main()

    assert stopped.value.zone == "America/New_York"
