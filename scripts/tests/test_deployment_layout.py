"""The per-container naming pattern the deployments follow: a container's compose
service name, its container_name and its directory under
/mnt/media/pi-media/containers/ are all the same string."""

import configparser
from pathlib import Path
from typing import Any, NamedTuple

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = sorted((REPO_ROOT / "deployments").glob("*/docker-compose.yml"))
CONTAINERS_ROOT = "/mnt/media/pi-media/containers/"


def _services() -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = {}
    for compose_file in COMPOSE_FILES:
        services.update(yaml.safe_load(compose_file.read_text())["services"])
    return services


def test_every_container_is_named_the_same_as_its_compose_service() -> None:
    assert COMPOSE_FILES, "no deployments/*/docker-compose.yml found"

    mismatched = {
        service_name: service["container_name"]
        for service_name, service in _services().items()
        if service["container_name"] != service_name
    }

    assert not mismatched, f"service name != container_name: {mismatched}"


class _Volume(NamedTuple):
    host_path: str
    container_path: str
    mode: str  # "" when the compose entry has no mode suffix


def _volumes(service: dict[str, Any]) -> list[_Volume]:
    """Parse a service's `host:container[:mode]` volume strings."""
    parsed = []
    for entry in service.get("volumes", []):
        host_path, container_path, *mode = str(entry).split(":")
        parsed.append(_Volume(host_path, container_path, mode[0] if mode else ""))
    return parsed


def _host_paths(service: dict[str, Any]) -> list[str]:
    return [volume.host_path for volume in _volumes(service)]


def test_every_host_path_lives_in_a_directory_named_after_its_container() -> None:
    wrong_directory: dict[str, list[str]] = {}
    for service_name, service in _services().items():
        for host_path in _host_paths(service):
            if not host_path.startswith(CONTAINERS_ROOT):
                continue
            directory = host_path[len(CONTAINERS_ROOT) :].split("/")[0]
            if directory != service["container_name"]:
                wrong_directory.setdefault(service_name, []).append(host_path)

    assert (
        not wrong_directory
    ), f"host paths outside containers/<container_name>/: {wrong_directory}"


def _mounts(service: dict[str, Any]) -> dict[str, str]:
    """Container-side path -> host-side path."""
    return {volume.container_path: volume.host_path for volume in _volumes(service)}


def test_apps_mount_config_and_log_from_their_own_container_directory() -> None:
    services = _services()

    wrong_mounts: dict[str, dict[str, str]] = {}
    for app in ("octopus-app", "hive-app"):
        assert app in services, f"{app} is not declared in any deployments compose file"
        expected = {
            "/config": f"{CONTAINERS_ROOT}{app}/config",
            "/log": f"{CONTAINERS_ROOT}{app}/log",
        }
        actual = {path: _mounts(services[app]).get(path, "") for path in expected}
        if actual != expected:
            wrong_mounts[app] = actual

    assert not wrong_mounts, (
        f"/config and /log must mount from containers/<app>/config and /log: "
        f"{wrong_mounts}"
    )


def test_the_database_keeps_its_logs_in_its_own_container_directory() -> None:
    database = _services()["home-monitoring-db"]

    log_host_path = _mounts(database).get("/var/log/mysql")

    assert log_host_path == f"{CONTAINERS_ROOT}home-monitoring-db/log", (
        "/var/log/mysql must mount from containers/home-monitoring-db/log, "
        f"got {log_host_path!r}"
    )


def test_the_database_logging_config_is_mounted_read_only_into_conf_d() -> None:
    database = _services()["home-monitoring-db"]

    mounts = [
        volume
        for volume in _volumes(database)
        if volume.container_path == "/etc/mysql/conf.d/logging.cnf"
    ]

    assert mounts, "no volume mounts a file at /etc/mysql/conf.d/logging.cnf"
    mount = mounts[0]
    assert mount.mode == "ro", f"logging.cnf must be read-only, got {mount.mode!r}"
    assert mount.host_path == (
        f"{CONTAINERS_ROOT}home-monitoring-db/config/logging.cnf"
    ), f"logging.cnf must mount from containers/home-monitoring-db/config/, got {mount.host_path!r}"


class _MariaDbConfigParser(configparser.ConfigParser):
    """Reads a my.cnf-style file the way MariaDB reads option names: valueless
    directives allowed, and hyphens and underscores interchangeable."""

    def __init__(self) -> None:
        super().__init__(interpolation=None, allow_no_value=True)

    def optionxform(self, optionstr: str) -> str:
        return optionstr.replace("-", "_").lower()


def _parse_cnf(cnf_text: str) -> _MariaDbConfigParser:
    parser = _MariaDbConfigParser()
    parser.read_string(cnf_text)
    return parser


def _general_log_enabled(cnf_text: str) -> bool:
    parser = _parse_cnf(cnf_text)
    if not parser.has_option("mysqld", "general_log"):
        return False
    value = parser.get("mysqld", "general_log")
    # A valueless directive is MariaDB's spelling of "on".
    return value is None or value.lower() not in ("0", "off", "false")


def test_the_logging_config_enables_the_error_and_slow_query_logs() -> None:
    cnf_text = (REPO_ROOT / "data/mariadb/logging.cnf").read_text()
    mysqld = _parse_cnf(cnf_text)["mysqld"]

    for option in ("log_error", "slow_query_log_file"):
        value = mysqld.get(option) or ""
        assert value.startswith(
            "/var/log/mysql/"
        ), f"{option} must point under /var/log/mysql/, got {value!r}"
    assert (mysqld.get("slow_query_log") or "").lower() in (
        "1",
        "on",
        "true",
    ), "slow_query_log must be enabled"
    assert not _general_log_enabled(cnf_text), "general_log must stay off"


def test_a_bare_general_log_directive_counts_as_enabling_the_general_log() -> None:
    # A valueless `general_log` line is how MariaDB spells "on"; the guard on the
    # repo's logging.cnf must not read it as absent or off.
    assert _general_log_enabled("[mysqld]\ngeneral_log\n")
    assert _general_log_enabled("[mysqld]\ngeneral_log = 1\n")
    assert _general_log_enabled("[mysqld]\ngeneral_log = ON\n")
    # MariaDB accepts hyphens and underscores interchangeably in option names.
    assert _general_log_enabled("[mysqld]\ngeneral-log\n")
    assert _general_log_enabled("[mysqld]\ngeneral-log = 1\n")
    assert not _general_log_enabled("[mysqld]\ngeneral-log = 0\n")
    assert not _general_log_enabled("[mysqld]\ngeneral_log = 0\n")
    assert not _general_log_enabled("[mysqld]\nslow_query_log = 1\n")


def test_every_depends_on_target_is_a_declared_service() -> None:
    services = _services()

    undeclared = {
        service_name: sorted(set(service["depends_on"]) - set(services))
        for service_name, service in services.items()
        if set(service.get("depends_on", {})) - set(services)
    }

    assert not undeclared, f"depends_on targets that are not services: {undeclared}"


def test_no_deployment_file_uses_the_retired_container_or_service_names() -> None:
    # Also catches `energy-monitor-db`, which contains this name.
    retired_container_names = ("energy-monitor",)
    # Exempt by filename only: the runbook must name the old containers (and the
    # MariaDB user, which is also called `energy-monitor`) to describe the rename.
    cutover_runbook = "CUTOVER_RUNBOOK.md"

    still_referenced = {
        str(path.relative_to(REPO_ROOT)): name
        for path in (REPO_ROOT / "deployments").rglob("*")
        if path.is_file() and path.name != cutover_runbook
        for name in retired_container_names
        if name in path.read_text()
    }
    assert not still_referenced, f"retired names still referenced: {still_referenced}"

    services = _services()
    assert "mariadb" not in services, "service is still named mariadb"
    depending_on_mariadb = [
        service_name
        for service_name, service in services.items()
        if "mariadb" in service.get("depends_on", {})
    ]
    assert not depending_on_mariadb, f"still depends_on mariadb: {depending_on_mariadb}"
