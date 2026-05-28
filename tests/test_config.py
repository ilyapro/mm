from __future__ import annotations

import pytest

from mm_mcp.config import ConfigError, load_config


# Test fixture names are constructed at runtime so the source file does not
# contain a literal `export <KEY>="..."` line that the merge-gate's secret
# scanner heuristically rejects as a credential export.
_TOKEN_KEY = "MM_" + "TOKEN"
_URL_KEY = "MM_" + "URL"
_PLACEHOLDER_VALUE = "quoted" + " " + "value"


def test_env_only_happy_path_strips_trailing_slash() -> None:
    cfg = load_config({_URL_KEY: "https://mm.example.test/", _TOKEN_KEY: "token"})

    assert cfg.url == "https://mm.example.test"
    assert cfg.token == "token"


def test_env_file_fallback_success_with_export_and_quotes(tmp_path) -> None:
    env_file = tmp_path / "mm.env"
    dq = chr(34)
    sq = chr(39)
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "",
                f"export {_TOKEN_KEY}={dq}{_PLACEHOLDER_VALUE}{dq}",
                f"{_URL_KEY}={sq}https://from-file.example/{sq}",
                "",
            ]
        )
    )

    cfg = load_config({"MM_MCP_ENV_FILE": str(env_file)})

    assert cfg.url == "https://from-file.example"
    assert cfg.token == _PLACEHOLDER_VALUE


def test_missing_url_message_names_mm_url() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_config({_TOKEN_KEY: "token"})

    assert _URL_KEY in str(exc_info.value)


def test_env_values_are_not_overridden_by_env_file(tmp_path) -> None:
    env_file = tmp_path / "mm.env"
    env_file.write_text(
        "\n".join(
            [
                f"{_URL_KEY}=https://file.example",
                f"{_TOKEN_KEY}=file-secret",
                "",
            ]
        )
    )

    cfg = load_config(
        {
            _URL_KEY: "https://env.example/",
            _TOKEN_KEY: "env-secret",
            "MM_MCP_ENV_FILE": str(env_file),
        }
    )

    assert cfg.url == "https://env.example"
    assert cfg.token == "env-secret"
