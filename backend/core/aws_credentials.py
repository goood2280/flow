"""AWS credential file helpers for Flow runtime data."""
from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple


def aws_home(data_root: Path) -> Path:
    return Path(data_root) / "s3_ingest" / "aws"


def credentials_path(data_root: Path) -> Path:
    return aws_home(data_root) / "credentials"


def config_path(data_root: Path) -> Path:
    return aws_home(data_root) / "config"


def _read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    if path.exists():
        try:
            parser.read(path, encoding="utf-8")
        except Exception:
            pass
    return parser


def read_credentials(data_root: Path) -> Dict[str, Dict[str, str]]:
    parser = _read_ini(credentials_path(data_root))
    return {section: dict(parser.items(section)) for section in parser.sections()}


def read_config(data_root: Path) -> Dict[str, Dict[str, str]]:
    parser = _read_ini(config_path(data_root))
    out: Dict[str, Dict[str, str]] = {}
    for section in parser.sections():
        name = section[8:].strip() if section.lower().startswith("profile ") else section
        out[name] = dict(parser.items(section))
    return out


def profile_values(data_root: Path, profile: str | None) -> Tuple[Dict[str, str], Dict[str, str]]:
    name = (profile or "default").strip() or "default"
    return read_credentials(data_root).get(name, {}), read_config(data_root).get(name, {})


def cli_env(data_root: Path, base_env: Mapping[str, str] | None = None) -> Dict[str, str]:
    env: Dict[str, str] = dict(os.environ if base_env is None else base_env)
    env["AWS_SHARED_CREDENTIALS_FILE"] = str(credentials_path(data_root))
    env["AWS_CONFIG_FILE"] = str(config_path(data_root))
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    return env


def boto3_session_kwargs(data_root: Path, profile: str | None) -> Tuple[Dict[str, Any], Dict[str, str]]:
    name = (profile or "default").strip() or "default"
    creds, conf = profile_values(data_root, name)
    kwargs: Dict[str, Any] = {}
    access_key = (creds.get("aws_access_key_id") or "").strip()
    secret_key = (creds.get("aws_secret_access_key") or "").strip()
    session_token = (creds.get("aws_session_token") or "").strip()
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
        if session_token:
            kwargs["aws_session_token"] = session_token
    elif profile:
        kwargs["profile_name"] = name
    return kwargs, conf
