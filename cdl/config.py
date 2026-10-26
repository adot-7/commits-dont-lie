"""Configuration loading for Commits Don't Lie.

This module owns environment parsing, defaults, and required-variable checks.
Other modules must receive a ``Settings`` object and must not read the process
environment directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is listed in requirements
    load_dotenv = None


DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_PROVIDER = "bharatcode"
DEFAULT_BHARATCODE_MODEL = "qwen-3.8-27b"
DEFAULT_BHARATCODE_BASE_URL = "https://bharatcode.ai/api/model/v1"
PROVIDER_KEY_VARS = {"bharatcode": "BHARATCODE_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
DEFAULT_REPO = "adot-7/commits-dont-lie"
DEFAULT_DATABASE_PATH = "data/cdl.sqlite"
DEFAULT_STYLE_EXAMPLES_FILE = "style/examples.md"

REQUIRED_ENV_VARS = (
    "ADMIN_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_WEBHOOK_SECRET",
    "GITHUB_REPO",
    "NOTION_TOKEN",
    "NOTION_NOTES_DB_ID",
    "NOTION_POSTS_DB_ID",
    "NOTION_NOTES_DS_ID",
    "NOTION_POSTS_DS_ID",
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "SLACK_CHANNEL_ID",
)


class ConfigError(RuntimeError):
    """Raised when production configuration is missing required variables."""


@dataclass(frozen=True)
class Settings:
    """Runtime settings shared by the application modules."""

    app_base_url: str
    admin_token: str
    github_token: str
    github_webhook_secret: str
    github_repo: str
    notion_token: str
    notion_notes_db_id: str
    notion_posts_db_id: str
    notion_notes_ds_id: str
    notion_posts_ds_id: str
    slack_bot_token: str
    slack_signing_secret: str
    slack_channel_id: str
    anthropic_api_key: str
    anthropic_model: str
    database_path: str
    style_examples: str = ""
    git_sha: str = ""
    first_sha: str = ""
    style_examples_file: str = DEFAULT_STYLE_EXAMPLES_FILE
    llm_provider: str = DEFAULT_PROVIDER
    bharatcode_api_key: str = ""
    bharatcode_model: str = DEFAULT_BHARATCODE_MODEL
    bharatcode_base_url: str = DEFAULT_BHARATCODE_BASE_URL
    bharatcode_reasoning_effort: str = "none"

    @property
    def llm_model(self) -> str:
        """Return the model ID used by the configured provider."""

        return self.bharatcode_model if self.llm_provider == "bharatcode" else self.anthropic_model


def _env(name: str, default: str = "") -> str:
    """Read one environment variable after loading a local dotenv file."""

    return os.environ.get(name, default).strip()


def load_settings(*, strict: bool = True, dotenv_path: str | Path | None = None) -> Settings:
    """Load settings and optionally fail with every missing required variable.

    ``strict=False`` is useful for importing the app in local tests and for
    routes that do not need external credentials; production boot uses strict
    mode through the CLI.
    """

    if load_dotenv is not None:
        load_dotenv(dotenv_path=dotenv_path, override=False)

    provider = (_env("LLM_PROVIDER", DEFAULT_PROVIDER) or DEFAULT_PROVIDER).lower()
    if provider not in PROVIDER_KEY_VARS:
        raise ConfigError(f"LLM_PROVIDER must be one of {', '.join(PROVIDER_KEY_VARS)}; got {provider!r}")
    values = {name: _env(name) for name in REQUIRED_ENV_VARS}
    missing = [name for name in REQUIRED_ENV_VARS if not values[name]]
    key_var = PROVIDER_KEY_VARS[provider]
    if not _env(key_var):
        missing.append(key_var)
    if strict and missing:
        raise ConfigError("Missing required environment variables: " + ", ".join(missing))

    return Settings(
        app_base_url=_env("APP_BASE_URL", "http://127.0.0.1:8000"),
        admin_token=values["ADMIN_TOKEN"],
        github_token=values["GITHUB_TOKEN"],
        github_webhook_secret=values["GITHUB_WEBHOOK_SECRET"],
        github_repo=values["GITHUB_REPO"] or DEFAULT_REPO,
        notion_token=values["NOTION_TOKEN"],
        notion_notes_db_id=values["NOTION_NOTES_DB_ID"],
        notion_posts_db_id=values["NOTION_POSTS_DB_ID"],
        notion_notes_ds_id=values["NOTION_NOTES_DS_ID"],
        notion_posts_ds_id=values["NOTION_POSTS_DS_ID"],
        slack_bot_token=values["SLACK_BOT_TOKEN"],
        slack_signing_secret=values["SLACK_SIGNING_SECRET"],
        slack_channel_id=values["SLACK_CHANNEL_ID"],
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
        database_path=_env("DATABASE_PATH", DEFAULT_DATABASE_PATH) or DEFAULT_DATABASE_PATH,
        style_examples=_env("STYLE_EXAMPLES"),
        git_sha=_env("GIT_SHA"),
        first_sha=_env("FIRST_SHA"),
        style_examples_file=_env("STYLE_EXAMPLES_FILE", DEFAULT_STYLE_EXAMPLES_FILE) or DEFAULT_STYLE_EXAMPLES_FILE,
        llm_provider=provider,
        bharatcode_api_key=_env("BHARATCODE_API_KEY"),
        bharatcode_model=_env("BHARATCODE_MODEL", DEFAULT_BHARATCODE_MODEL) or DEFAULT_BHARATCODE_MODEL,
        bharatcode_base_url=_env("BHARATCODE_BASE_URL", DEFAULT_BHARATCODE_BASE_URL) or DEFAULT_BHARATCODE_BASE_URL,
        bharatcode_reasoning_effort=_env("BHARATCODE_REASONING_EFFORT", "none"),
    )


def get_settings(*, strict: bool = False) -> Settings:
    """Return settings for callers that do not want to repeat dotenv loading."""

    return load_settings(strict=strict)
