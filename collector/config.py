"""Configuration loading.

The real config.ini lives outside the repo (CLAUDE.md §3): only
config.ini.template, with placeholder values, is versioned. The SMTP
password is never read from the file — it comes from the
DATA_COLLECTOR_SMTP_PASSWORD environment variable, which the systemd timer
supplies.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "data-collector" / "config.ini"
SMTP_PASSWORD_ENV = "DATA_COLLECTOR_SMTP_PASSWORD"


class ConfigError(Exception):
    """The configuration file is missing, unreadable, or incomplete."""


@dataclass
class DeviceConfig:
    name: str
    url: str
    timeout_s: float


@dataclass
class ArchiveConfig:
    dir: Path
    max_attachment_mb: int


@dataclass
class NtfyConfig:
    server: str
    topic: str


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    to: str

    @property
    def password(self) -> str:
        """Read on demand, not cached: only required when actually sending mail."""
        password = os.environ.get(SMTP_PASSWORD_ENV)
        if not password:
            raise ConfigError(f"{SMTP_PASSWORD_ENV} is not set")
        return password


@dataclass
class AlertsConfig:
    unreachable_polls: int
    mute_sensor_hours: int


@dataclass
class Config:
    devices: List[DeviceConfig]
    archive: ArchiveConfig
    ntfy: NtfyConfig
    smtp: SmtpConfig
    alerts: AlertsConfig


def load(path: Optional[Path] = None) -> Config:
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path}")

    parser = configparser.ConfigParser()
    with path.open(encoding="ascii") as f:
        parser.read_file(f)

    try:
        devices = [
            DeviceConfig(
                name=section.split(":", 1)[1],
                url=parser.get(section, "url"),
                timeout_s=parser.getfloat(section, "timeout_s"),
            )
            for section in parser.sections()
            if section.startswith("device:")
        ]
        if not devices:
            raise ConfigError(f"no [device:*] section in {path}")

        archive = ArchiveConfig(
            dir=Path(parser.get("archive", "dir")),
            max_attachment_mb=parser.getint("archive", "max_attachment_mb"),
        )
        ntfy = NtfyConfig(
            server=parser.get("ntfy", "server"),
            topic=parser.get("ntfy", "topic"),
        )
        smtp = SmtpConfig(
            host=parser.get("smtp", "host"),
            port=parser.getint("smtp", "port"),
            user=parser.get("smtp", "user"),
            to=parser.get("smtp", "to"),
        )
        alerts = AlertsConfig(
            unreachable_polls=parser.getint("alerts", "unreachable_polls"),
            mute_sensor_hours=parser.getint("alerts", "mute_sensor_hours"),
        )
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError) as exc:
        raise ConfigError(f"invalid configuration in {path}: {exc}") from exc

    return Config(devices=devices, archive=archive, ntfy=ntfy, smtp=smtp, alerts=alerts)
