import logging
import logging.config
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

import common.logging
from common.logging import logging_config


@pytest.fixture(autouse=True)
def _close_configured_handlers() -> Iterator[None]:
    yield
    for name in ("hive-monitor", "octopus-monitor"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def test_logging_config_configures_a_logger_with_the_given_name() -> None:
    config = logging_config("octopus-monitor")

    assert "octopus-monitor" in config["loggers"]
    assert "hive-monitor" not in config["loggers"]


def test_a_writable_log_directory_gets_a_rotating_log_file_alongside_console_output(
    tmp_path: Path,
) -> None:
    config = logging_config("hive-monitor", log_dir=str(tmp_path))

    file_handler = config["handlers"]["file"]
    assert file_handler["class"] == "logging.handlers.RotatingFileHandler"
    assert file_handler["filename"] == str(tmp_path / "hive-monitor.log")
    assert config["loggers"]["hive-monitor"]["handlers"] == ["console", "file"]

    logging.config.dictConfig(config)
    logging.getLogger("hive-monitor").info("hello from the test")
    for handler in logging.getLogger("hive-monitor").handlers:
        handler.flush()

    assert "hello from the test" in (tmp_path / "hive-monitor.log").read_text()


def test_a_missing_log_directory_falls_back_to_console_only_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_dir = tmp_path / "does-not-exist"

    config = logging_config("hive-monitor", log_dir=str(missing_dir))

    assert "file" not in config["handlers"]
    assert config["loggers"]["hive-monitor"]["handlers"] == ["console"]
    assert str(missing_dir) in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to read-only directories")
def test_an_unwritable_log_directory_falls_back_to_console_only_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    read_only_dir = tmp_path / "read-only"
    read_only_dir.mkdir()
    read_only_dir.chmod(0o555)

    try:
        config = logging_config("hive-monitor", log_dir=str(read_only_dir))
    finally:
        read_only_dir.chmod(0o755)

    assert "file" not in config["handlers"]
    assert config["loggers"]["hive-monitor"]["handlers"] == ["console"]
    assert str(read_only_dir) in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to read-only files")
def test_an_unwritable_existing_log_file_falls_back_to_console_only_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_file = tmp_path / "hive-monitor.log"
    log_file.write_text("previous run\n")
    log_file.chmod(0o444)

    config = logging_config("hive-monitor", log_dir=str(tmp_path))

    assert "file" not in config["handlers"]
    assert config["loggers"]["hive-monitor"]["handlers"] == ["console"]
    assert str(log_file) in capsys.readouterr().err


def test_the_default_log_directory_is_used_when_none_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common.logging, "DEFAULT_LOG_DIR", str(tmp_path))

    config = logging_config("hive-monitor")

    assert config["handlers"]["file"]["filename"] == str(tmp_path / "hive-monitor.log")


def test_logging_config_is_parameterized_by_logger_name_not_hardcoded() -> None:
    octopus_config = logging_config("octopus-monitor")
    hive_config = logging_config("hive-monitor")

    assert set(octopus_config["loggers"]) == {"octopus-monitor"}
    assert set(hive_config["loggers"]) == {"hive-monitor"}
