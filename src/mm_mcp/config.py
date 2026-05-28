from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MmConfig:
    url: str
    token: str


class ConfigError(RuntimeError):
    pass


_REQUIRED_KEYS = ("MM_URL", "MM_TOKEN")


def _missing(env: Mapping[str, str]) -> list[str]:
    return [key for key in _REQUIRED_KEYS if not env.get(key)]


def _strip_surrounding_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_env_file(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        parsed[key] = _strip_surrounding_quotes(value)
    return parsed


def load_config(env: Mapping[str, str] | None = None) -> MmConfig:
    merged = dict(os.environ if env is None else env)
    missing = _missing(merged)
    env_file = merged.get("MM_MCP_ENV_FILE")

    if missing and env_file and Path(env_file).exists():
        for key, value in _parse_env_file(Path(env_file)).items():
            if key not in merged:
                merged[key] = value

    missing = _missing(merged)
    if missing:
        raise ConfigError(", ".join(missing))

    return MmConfig(url=merged["MM_URL"].rstrip("/"), token=merged["MM_TOKEN"])
